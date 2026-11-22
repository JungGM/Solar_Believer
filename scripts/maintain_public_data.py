"""Keep every available public resource current and advance past archive errors.

Runs independently of the web/DB worker. Each provider has isolated checkpoints,
raw responses, revisions and status. Create OUTPUT/STOP to stop after a request.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from dotenv import dotenv_values

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_public_history import (
    APIError,
    Download,
    ENDPOINTS,
    KOMIPO_RESOURCES,
    KST,
    LIMITS,
    ROOT,
    UsageLedger,
    dump,
    emit,
    months,
    now,
)
from scripts.collection_queue import enqueue, load_queue, run_queue, task_identity
from scripts.generation_roster import all_komipo_resources
from scripts.collection_status import write_collection_summary

PROCESS_STARTED_AT = time.time()
SOURCE_POLL_INTERVALS = {"koen": 5, "kospo": 5, "komipo": 40, "iwest": 60, "smp": 40}


def source_interval_minutes(args, source):
    return max(args.interval_minutes, SOURCE_POLL_INTERVALS.get(source, args.interval_minutes))


def source_poll_due(args, source, current):
    if getattr(args, "force_first_poll", False):
        return True
    path = args.output / f"{source}_status.json"
    if not path.exists():
        return True
    previous = json.loads(path.read_text(encoding="utf-8"))
    selected = previous.get("next_poll_at_utc")
    return not selected or datetime.fromisoformat(selected) <= current.astimezone(timezone.utc) + timedelta(
        seconds=5
    )


def stop_requested(path, started_after=None):
    """Ignore old STOP files restored by OneDrive after an explicit restart."""
    try:
        return path.stat().st_mtime >= (PROCESS_STARTED_AT if started_after is None else started_after)
    except FileNotFoundError:
        return False


# Existing regional representative grids, explicitly not verified plant grids.
REGION_GRIDS = [
    [60, 127],
    [98, 76],
    [89, 90],
    [55, 124],
    [58, 74],
    [67, 100],
    [102, 84],
    [66, 103],
    [60, 120],
    [73, 134],
    [69, 107],
    [68, 100],
    [63, 89],
    [51, 67],
    [91, 106],
    [91, 77],
    [52, 38],
]


class Singleton:
    def __init__(self, path):
        self.stream = path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("A collector is already running for this output directory") from None

    def close(self):
        self.stream.close()


class ManagedDownload(Download):
    def __init__(
        self,
        output,
        source,
        key,
        ledger_path,
        stop_file,
        *,
        cache_seconds=None,
        max_calls=None,
        call_limit=None,
    ):
        super().__init__(output, source, key, usage_path=ledger_path)
        self.stop_file = Path(stop_file)
        self.cache_seconds = cache_seconds
        self.empty_cache_seconds = min(3600, cache_seconds) if cache_seconds is not None else 3600
        self.calls_this_cycle = 0
        self.max_calls = max_calls
        self.call_limit = LIMITS[source] if call_limit is None else call_limit

    def claim_call(self, today):
        if stop_requested(self.stop_file):
            raise APIError("STOP_REQUESTED", "Collector stop requested")
        if self.max_calls is not None and self.calls_this_cycle >= self.max_calls:
            raise APIError("CYCLE_BUDGET", "Remaining work will resume next cycle")
        self.usage.claim(self.db, self.directory, self.source, today, self.call_limit)
        self.calls_this_cycle += 1


def forecast_issues(current, days=3):
    """KMA archive accepts today and two previous calendar dates, not 72h."""
    result = []
    for offset in range(days):
        day = current.date() - timedelta(days=offset)
        for hour in (2, 5, 8, 11, 14, 17, 20, 23):
            issue = datetime.combine(day, datetime.min.time(), KST).replace(hour=hour)
            if issue + timedelta(minutes=20) <= current:
                result.append(issue)
    # Recover the newest issue first, then the oldest before it expires.
    result.sort()
    return [result[-1], *result[:-1]] if result else []


def month_params(source, left, right):
    s, e = left.strftime("%Y%m%d"), right.strftime("%Y%m%d")
    if source == "koen":
        return dict(startD=s, endD=e), "page", "size", 100
    if source == "kospo":
        return dict(strSdate=s, strEdate=e), "pageNo", "numOfRows", 10000
    if source == "iwest":
        return dict(startDate=s, endDate=e), "pageNo", "numOfRows", 100
    raise ValueError(source)


def fetch_generation_range(job, start, end, resources=None, store_start=None):
    stored = 0
    if job.source == "komipo":
        if resources is None:
            resources = all_komipo_resources(
                ROOT / "data/api_history_20220101_20261031",
                ROOT / "data/continuous_collection/live",
                refresh=False,
            )
        failures = []
        for code, unit in resources:
            try:
                rows, total, _, rid, metadata = job.request(
                    dict(
                        strDateS=start.strftime("%Y%m%d"),
                        strDateE=end.strftime("%Y%m%d"),
                        strOrgNo=code,
                        strHoki=unit,
                    )
                )
                if total is not None and total != len(rows):
                    raise APIError("INCOMPLETE_RESPONSE", "Provider returned fewer rows than advertised")
                stored += job.save_records(rows, rid, metadata, store_start or start, end)
            except APIError as exc:
                if exc.code in CONTROL_ERRORS:
                    raise
                failures.append({"resource": f"{code}:{unit}", "code": exc.code, "message": exc.message})
        return stored, failures
    failures = []
    for left, right in months(start, end):
        params, page_key, size_key, size = month_params(job.source, left, right)
        try:
            for rows, rid, metadata in job.pages(params, page_key, size_key, size):
                stored += job.save_records(rows, rid, metadata, start, end)
        except APIError as exc:
            if exc.code in CONTROL_ERRORS:
                raise
            failures.append({"start": str(left), "end": str(right), "code": exc.code, "message": exc.message})
    return stored, failures


def export_live(job):
    bounds = job.db.execute("SELECT min(day),max(day) FROM records").fetchone()
    if bounds[0] is not None:
        return job.export(date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1]))
    return None


def live_source(source, args, key, current):
    poll_minutes = source_interval_minutes(args, source)
    job = ManagedDownload(
        args.output / "live",
        source,
        key,
        args.ledger,
        args.output / "STOP",
        cache_seconds=0 if getattr(args, "force_first_poll", False) else poll_minutes * 60 - 5,
    )
    status = {"source": source, "started_at_utc": now(), "phase": "live", "errors": []}
    path = args.output / f"{source}_status.json"
    dump(path, status)
    try:
        first = current.date() - timedelta(days=args.lookback_days - 1)
        status.update(
            requested_start_kst=str(first),
            requested_end_kst=str(current.date()),
            collection_target_end_kst=str(current.date()),
        )
        if source == "forecast":
            job.cache_seconds = None  # Nonempty published issues are immutable.
            issues = forecast_issues(current)
            if args.forecast_max_issues:
                issues = issues[: args.forecast_max_issues]
            status.update(
                grid_scope="configured regional representative grids; not verified plant grids",
                planned_issues=len(issues),
                completed_issues=0,
            )
            for issue in issues:
                for nx, ny in REGION_GRIDS:
                    params = dict(
                        base_date=issue.strftime("%Y%m%d"),
                        base_time=issue.strftime("%H%M"),
                        nx=nx,
                        ny=ny,
                        dataType="JSON",
                    )
                    for rows, rid, metadata in job.pages(params, size=1000):
                        if not rows:
                            status["errors"].append(
                                {"issue": issue.isoformat(), "grid": [nx, ny], "code": "NO_DATA_YET"}
                            )
                        for row in rows:
                            if (
                                str(row.get("baseDate")) != params["base_date"]
                                or str(row.get("baseTime")).zfill(4) != params["base_time"]
                            ):
                                raise APIError(
                                    "WRONG_FORECAST_ISSUE", "Returned issue differs from requested issue"
                                )
                        job.save_records(rows, rid, metadata, date.min, date.max)
                status["completed_issues"] += 1
                status["stored_records"] = job.count()
                status["updated_at_utc"] = now()
                dump(path, status)
                emit(source=source, phase="live_forecast", issue=issue.isoformat(), records=job.count())
        elif source == "smp":
            status["requested_end_kst"] = str(current.date() + timedelta(days=1))
            status["requested_start_kst"] = str(current.date())
            for offset in range(2):
                selected = current.date() + timedelta(days=offset)
                try:
                    for rows, rid, metadata in job.pages(
                        dict(date=selected.strftime("%Y%m%d"), dataType="json")
                    ):
                        job.save_records(rows, rid, metadata, first, current.date() + timedelta(days=1))
                        if not rows:
                            status["errors"].append({"date": selected.isoformat(), "code": "NO_DATA_YET"})
                except APIError as exc:
                    if exc.code in CONTROL_ERRORS:
                        raise
                    status["errors"].append(
                        {"date": selected.isoformat(), "code": exc.code, "message": exc.message}
                    )
        elif source == "komipo":
            # The provider returns DB_ERROR for recent-only ranges even though
            # the full approved range returns valid data. Keep the working
            # query and store every returned date after the archive split.
            status["requested_start_kst"] = str(args.history_start)
            received, errors = fetch_generation_range(
                job,
                args.history_start,
                current.date(),
                args.komipo_resources,
                store_start=args.history_end + timedelta(days=1),
            )
            status["errors"].extend(errors)
            if not received:
                status["errors"].append({"code": "NO_RECENT_DATA_RETURNED"})
        else:
            received, errors = fetch_generation_range(job, first, current.date())
            status["errors"].extend(errors)
            if not received:
                status["errors"].append(
                    {"start": str(first), "end": str(current.date()), "code": "NO_DATA_YET"}
                )
        status["state"] = "updated" if not status["errors"] else "partial"
    except APIError as exc:
        status.update(
            state="waiting"
            if exc.code in {"22", "LOCAL_DAILY_BUDGET", "CYCLE_BUDGET", "STOP_REQUESTED"}
            else "error",
            error={"code": exc.code, "message": exc.message},
        )
    except Exception as exc:
        status.update(state="error", error={"code": "LOCAL_ERROR", "message": type(exc).__name__})
    finally:
        selected = datetime.now(timezone.utc) + timedelta(minutes=poll_minutes)
        if status.get("error", {}).get("code") in {"22", "LOCAL_DAILY_BUDGET"}:
            selected = datetime.combine(
                datetime.now(KST).date() + timedelta(days=1), datetime.min.time(), KST
            ) + timedelta(seconds=20)
        status.update(poll_interval_minutes=poll_minutes, next_poll_at_utc=selected.isoformat())
        status.update(stored_records=job.count(), api_calls=job.calls_this_cycle, finished_at_utc=now())
        # Export append-only live storage; fixed historical exports remain intact.
        export_live(job)
        dump(path, status)
        job.close()
    emit(
        source=source,
        phase="live",
        state=status["state"],
        records=status["stored_records"],
        calls=status["api_calls"],
    )
    return status


def completed_ranges(job):
    groups = {}
    for (metadata,) in job.db.execute("SELECT metadata FROM requests WHERE status='ok'"):
        meta = json.loads(metadata)
        p = meta["params"]
        key = json.dumps(
            {k: v for k, v in p.items() if k not in {"page", "pageNo", "size", "numOfRows"}}, sort_keys=True
        )
        item = groups.setdefault(key, [0, meta.get("total")])
        item[0] += meta.get("rows", 0)
    return {
        key
        for key, (received, total) in groups.items()
        if total is not None and total > 0 and received == total
    }


def observed_gap_months(job):
    seen = {}
    for day, resource in job.db.execute("SELECT day,resource FROM records ORDER BY resource,day"):
        seen.setdefault(resource, []).append(date.fromisoformat(day))
    result = set()
    for days in seen.values():
        for earlier, later in zip(days, days[1:]):
            cursor = earlier + timedelta(days=1)
            while cursor < later:
                result.add((cursor.year, cursor.month))
                cursor += timedelta(days=1)
    return result


CONTROL_ERRORS = {"22", "LOCAL_DAILY_BUDGET", "CYCLE_BUDGET", "STOP_REQUESTED"}


def reserve_live_calls(source, args, current):
    per_cycle = {
        "komipo": len(getattr(args, "komipo_resources", KOMIPO_RESOURCES)),
        "smp": 2,
        "iwest": 2,
        "koen": 2,
        "kospo": 2,
    }.get(source, 1)
    if source == "iwest":
        # Reserve based on page size; this estimate never limits resource collection.
        # Keep two calls when the window crosses a month boundary. The global
        # hard quota still applies if a response grows or needs a retry.
        first = current.date() - timedelta(days=args.lookback_days - 1)
        per_cycle = max(
            math.ceil(args.lookback_days * getattr(args, "iwest_resources_per_day", 50) / 100),
            1 + int(first.month != current.month),
        )
    return (
        math.ceil((24 * 60 - current.hour * 60 - current.minute) / source_interval_minutes(args, source))
        * per_cycle
    )


def _collect_queue(source, args, key, current, *, history):
    if source == "forecast":
        return {"source": source, "state": "historical_archive_unavailable"}
    phase = "backfill" if history else "bridge"
    output = args.history_output if history else args.output / "live"
    job = ManagedDownload(
        output,
        source,
        key,
        args.ledger,
        args.output / "STOP",
        max_calls=args.backfill_calls_per_cycle,
        call_limit=max(0, LIMITS[source] - reserve_live_calls(source, args, current)),
    )
    path = args.output / f"{source}_{phase}_queue.json"
    queue = load_queue(path)
    completed = completed_ranges(job)
    for identity, task in queue["tasks"].items():
        if task["state"] == "complete" and task["received"] == 0 and identity not in completed:
            task["state"] = "pending"
    first, last = (
        (args.history_start, args.history_end)
        if history
        else (args.history_end + timedelta(days=1), current.date() - timedelta(days=args.lookback_days))
    )
    if not history and source == "smp":
        last = current.date() - timedelta(days=1)
    if not history and queue["tasks"]:
        first = max(
            first,
            max(date.fromisoformat(task["end"]) for task in queue["tasks"].values()) + timedelta(days=1),
        )
    if first <= last:
        if source in {"koen", "kospo", "iwest"}:
            for left, right in months(first, last):
                params, page_key, size_key, size = month_params(source, left, right)
                enqueue(
                    queue,
                    params,
                    left,
                    right,
                    page_key,
                    size_key,
                    size,
                    completed=task_identity(params) in completed,
                )
        elif source == "smp":
            if history:
                params = {"dataType": "json"}
                enqueue(
                    queue,
                    params,
                    first,
                    last,
                    "pageNo",
                    "numOfRows",
                    10000,
                    completed=task_identity(params) in completed,
                )
            else:
                for offset in range((last - first).days + 1):
                    day = first + timedelta(days=offset)
                    enqueue(
                        queue,
                        {"date": day.strftime("%Y%m%d"), "dataType": "json"},
                        day,
                        day,
                        "pageNo",
                        "numOfRows",
                        10000,
                    )
        else:
            for code, unit in args.komipo_resources:
                params = dict(
                    strDateS=first.strftime("%Y%m%d"),
                    strDateE=last.strftime("%Y%m%d"),
                    strOrgNo=code,
                    strHoki=unit,
                )
                enqueue(queue, params, first, last, completed=task_identity(params) in completed)

    def version():
        return (
            job.db.execute("SELECT count(*),max(fetched) FROM records").fetchone(),
            job.db.execute("SELECT count(*) FROM revisions").fetchone()[0],
        )

    before = version()
    status = dict(
        source=source,
        phase=phase,
        started_at_utc=now(),
        policy="skip_errors_and_gaps",
        resource_count_limit=None,
    )
    try:
        status.update(run_queue(job, queue, path))
    except Exception as exc:
        status.update(state="error", error={"code": "LOCAL_ERROR", "message": type(exc).__name__})
    finally:
        if version() != before:
            bounds = (
                (args.history_start, args.history_end)
                if history
                else tuple(
                    date.fromisoformat(value)
                    for value in job.db.execute("SELECT min(day),max(day) FROM records").fetchone()
                )
            )
            job.export(*bounds, status.get("error"))
        status.update(stored_records=job.count(), api_calls=job.calls_this_cycle, finished_at_utc=now())
        job.close()
        dump(args.output / f"{source}_{phase}_status.json", status)
        emit(
            source=source,
            phase=phase,
            state=status["state"],
            records=status["stored_records"],
            calls=status["api_calls"],
            skipped_pages=status.get("skipped_pages", 0),
        )
    return status


def backfill_source(source, args, key, current):
    return _collect_queue(source, args, key, current, history=True)


def bridge_and_gaps(source, args, key, current):
    """Collect new intervals once; existing gaps and failed requests are skipped."""
    return _collect_queue(source, args, key, current, history=False)


def run_cycle(args, key):
    args.komipo_resources = all_komipo_resources(args.history_output, args.output / "live")
    current = datetime.now(KST)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {}
        for source in args.sources:
            if source_poll_due(args, source, current):
                pending[pool.submit(live_source, source, args, key, current)] = source
            else:
                previous = json.loads((args.output / f"{source}_status.json").read_text(encoding="utf-8"))
                results[source] = dict(previous, poll_deferred=True)
        for future in concurrent.futures.as_completed(pending):
            source = pending[future]
            try:
                results[source] = future.result()
            except Exception as exc:
                results[source] = {"source": source, "state": "error", "error": type(exc).__name__}
    args.force_first_poll = False
    maintenance_due = getattr(args, "next_maintenance_at_utc", None)
    maintenance_sources = args.sources if maintenance_due is None or current >= maintenance_due else []
    for source in maintenance_sources:
        if stop_requested(args.output / "STOP"):
            break
        try:
            # A long cycle or suspended PC may resume on a new KST day.
            # Reserve live-call quota using the current clock for each phase.
            # A low-quota source must not spend every spare call rechecking the
            # same recent provider gap before downloading its missing archive.
            phases = (
                (backfill_source, bridge_and_gaps)
                if source == "iwest"
                else (bridge_and_gaps, backfill_source)
            )
            for phase in phases:
                phase(source, args, key, datetime.now(KST))
        except Exception as exc:
            emit(source=source, phase="maintenance_error", error=type(exc).__name__)
    if maintenance_sources:
        args.next_maintenance_at_utc = datetime.now(KST) + timedelta(hours=1)
    write_collection_summary(args.history_output, args.output, args.history_start, datetime.now(KST).date())
    return results


def next_cycle_at(started, current, interval_minutes):
    regular = started + timedelta(minutes=interval_minutes)
    local = current.astimezone(KST)
    reset = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), KST) + timedelta(
        seconds=20
    )
    return max(current, min(regular, reset.astimezone(timezone.utc)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/continuous_collection")
    parser.add_argument("--history-output", type=Path, default=ROOT / "data/api_history_20220101_20261031")
    parser.add_argument("--history-start", type=date.fromisoformat, default=date(2022, 1, 1))
    parser.add_argument("--history-end", type=date.fromisoformat, default=date(2026, 10, 31))
    parser.add_argument("--interval-minutes", type=float, default=5)
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--sources", default="koen,kospo,komipo,iwest,smp")
    parser.add_argument("--backfill-calls-per-cycle", type=int, default=20)
    parser.add_argument(
        "--forecast-max-issues",
        type=int,
        default=0,
        help="0 recovers every issue still inside KMA's three-day window",
    )
    parser.add_argument("--iwest-daily-limit", type=int, default=100)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if (
        not math.isfinite(args.interval_minutes)
        or args.interval_minutes < 1
        or not 1 <= args.lookback_days <= 7
        or args.backfill_calls_per_cycle < 1
        or args.history_start > args.history_end
        or args.iwest_daily_limit < 11
        or args.forecast_max_issues < 0
    ):
        parser.error("Invalid interval, dates, lookback or budget")
    args.sources = list(dict.fromkeys(args.sources.split(",")))
    if any(s not in ENDPOINTS for s in args.sources):
        parser.error("Unknown source")
    args.output.mkdir(parents=True, exist_ok=True)
    args.ledger = ROOT / "data/public_api_usage"
    args.force_first_poll = True
    args.komipo_resources = all_komipo_resources(args.history_output, args.output / "live")
    key = unquote(dotenv_values(ROOT / ".env").get("DATA_GO_KR_API_KEY") or "").strip()
    if not key:
        parser.error("DATA_GO_KR_API_KEY is missing")
    LIMITS["iwest"] = args.iwest_daily_limit - 10
    lock = Singleton(args.output / "collector.lock")
    ledger = UsageLedger(args.ledger)
    ledger.seed(args.history_output)
    ledger.seed(args.output / "live")
    ledger.close()
    status = {
        "pid": os.getpid(),
        "started_at_utc": now(),
        "interval_minutes": args.interval_minutes,
        "live_poll_intervals_minutes": {
            source: source_interval_minutes(args, source) for source in args.sources
        },
        "sources": args.sources,
        "history_start": str(args.history_start),
        "history_end": str(args.history_end),
        "archive_storage_end_kst": str(args.history_end),
        "collection_target_end_kst": str(datetime.now(KST).date()),
        "output": str(args.output),
        "cycles_completed": 0,
        "policy": "skip_errors_and_gaps",
        "database": "PostgreSQL",
        "service_database_auto_ingest": True,
        "resource_count_limit": None,
        "komipo_roster_count": len(args.komipo_resources),
    }
    try:
        while not stop_requested(args.output / "STOP"):
            status.update(
                state="collecting",
                cycle_started_at_utc=now(),
                next_run_at_utc=None,
                collection_target_end_kst=str(datetime.now(KST).date()),
            )
            dump(args.output / "collector_status.json", status)
            try:
                results = run_cycle(args, key)
            except Exception as exc:
                # A cycle failure must not silently terminate periodic polling.
                results = {"scheduler": {"state": "error", "error": type(exc).__name__}}
                emit(phase="cycle_error", error=type(exc).__name__)
            status["cycles_completed"] += 1
            status.update(state="waiting", last_cycle_finished_at_utc=now(), last_results=results)
            next_run = next_cycle_at(
                datetime.fromisoformat(status["cycle_started_at_utc"]),
                datetime.now(timezone.utc),
                args.interval_minutes,
            )
            status["next_run_at_utc"] = next_run.isoformat()
            dump(args.output / "collector_status.json", status)
            if args.once:
                break
            while datetime.now(timezone.utc) < next_run and not stop_requested(args.output / "STOP"):
                time.sleep(min(5, max(0, (next_run - datetime.now(timezone.utc)).total_seconds())))
        status.update(state="one_cycle_complete" if args.once else "stopped", updated_at_utc=now())
        dump(args.output / "collector_status.json", status)
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
