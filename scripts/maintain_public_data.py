"""Keep approved public feeds current and resume historical gaps within quotas.

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
    APIError, Download, ENDPOINTS, KOMIPO_RESOURCES, KST, LIMITS, ROOT,
    UsageLedger, dump, emit, months, now,
)

# Existing regional representative grids, explicitly not verified plant grids.
REGION_GRIDS = [
    [60, 127], [98, 76], [89, 90], [55, 124], [58, 74], [67, 100],
    [102, 84], [66, 103], [60, 120], [73, 134], [69, 107], [68, 100],
    [63, 89], [51, 67], [91, 106], [91, 77], [52, 38],
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
    def __init__(self, output, source, key, ledger_path, stop_file, *, cache_seconds=None, max_calls=None, call_limit=None):
        super().__init__(output, source, key, usage_path=ledger_path)
        self.stop_file = Path(stop_file)
        self.cache_seconds = cache_seconds
        self.empty_cache_seconds = 3600
        self.calls_this_cycle = 0
        self.max_calls = max_calls
        self.call_limit = LIMITS[source] if call_limit is None else call_limit

    def claim_call(self, today):
        if self.stop_file.exists():
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


def fetch_generation_range(job, start, end, resources=KOMIPO_RESOURCES):
    stored = 0
    if job.source == "komipo":
        failures = []
        for code, unit in resources:
            try:
                rows, total, _, rid, metadata = job.request(dict(strDateS=start.strftime("%Y%m%d"), strDateE=end.strftime("%Y%m%d"), strOrgNo=code, strHoki=unit))
                if total is not None and total != len(rows):
                    raise APIError("INCOMPLETE_RESPONSE", "Provider returned fewer rows than advertised")
                stored += job.save_records(rows, rid, metadata, start, end)
            except APIError as exc:
                if exc.code in CONTROL_ERRORS:
                    raise
                failures.append({"resource": f"{code}:{unit}", "code": exc.code, "message": exc.message})
        return stored, failures
    for left, right in months(start, end):
        params, page_key, size_key, size = month_params(job.source, left, right)
        for rows, rid, metadata in job.pages(params, page_key, size_key, size):
            stored += job.save_records(rows, rid, metadata, start, end)
    return stored, []


def export_live(job):
    bounds = job.db.execute("SELECT min(day),max(day) FROM records").fetchone()
    if bounds[0] is not None:
        return job.export(date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1]))
    return None


def live_source(source, args, key, current):
    job = ManagedDownload(args.output / "live", source, key, args.ledger, args.output / "STOP", cache_seconds=args.interval_minutes * 60 - 5)
    status = {"source": source, "started_at_utc": now(), "phase": "live", "errors": []}
    path = args.output / f"{source}_status.json"
    dump(path, status)
    try:
        first = current.date() - timedelta(days=args.lookback_days - 1)
        if source == "forecast":
            job.cache_seconds = None  # Nonempty published issues are immutable.
            issues = forecast_issues(current)
            if args.forecast_max_issues:
                issues = issues[:args.forecast_max_issues]
            status.update(grid_scope="configured regional representative grids; not verified plant grids", planned_issues=len(issues), completed_issues=0)
            for issue in issues:
                for nx, ny in REGION_GRIDS:
                    params = dict(base_date=issue.strftime("%Y%m%d"), base_time=issue.strftime("%H%M"), nx=nx, ny=ny, dataType="JSON")
                    for rows, rid, metadata in job.pages(params, size=1000):
                        if not rows:
                            status["errors"].append({"issue": issue.isoformat(), "grid": [nx, ny], "code": "NO_DATA_YET"})
                        for row in rows:
                            if str(row.get("baseDate")) != params["base_date"] or str(row.get("baseTime")).zfill(4) != params["base_time"]:
                                raise APIError("WRONG_FORECAST_ISSUE", "Returned issue differs from requested issue")
                        job.save_records(rows, rid, metadata, date.min, date.max)
                status["completed_issues"] += 1
                status["stored_records"] = job.count()
                status["updated_at_utc"] = now()
                dump(path, status)
                emit(source=source, phase="live_forecast", issue=issue.isoformat(), records=job.count())
        elif source == "smp":
            for offset in range(args.lookback_days + 1):
                selected = first + timedelta(days=offset)
                for rows, rid, metadata in job.pages(dict(date=selected.strftime("%Y%m%d"), dataType="json")):
                    job.save_records(rows, rid, metadata, first, current.date() + timedelta(days=1))
                    if not rows:
                        status["errors"].append({"date": selected.isoformat(), "code": "NO_DATA_YET"})
        else:
            received, errors = fetch_generation_range(job, first, current.date())
            status["errors"].extend(errors)
            if not received:
                status["errors"].append({"start": str(first), "end": str(current.date()), "code": "NO_DATA_YET"})
        status["state"] = "updated" if not status["errors"] else "partial"
    except APIError as exc:
        status.update(state="waiting" if exc.code in {"22", "LOCAL_DAILY_BUDGET", "CYCLE_BUDGET", "STOP_REQUESTED"} else "error", error={"code": exc.code, "message": exc.message})
    except Exception as exc:
        status.update(state="error", error={"code": "LOCAL_ERROR", "message": type(exc).__name__})
    finally:
        status.update(stored_records=job.count(), api_calls=job.calls_this_cycle, finished_at_utc=now())
        # Export append-only live storage; fixed historical exports remain intact.
        export_live(job)
        dump(path, status)
        job.close()
    emit(source=source, phase="live", state=status["state"], records=status["stored_records"], calls=status["api_calls"])
    return status


def completed_ranges(job):
    groups = {}
    for metadata, in job.db.execute("SELECT metadata FROM requests WHERE status='ok'"):
        meta = json.loads(metadata)
        p = meta["params"]
        key = json.dumps({k: v for k, v in p.items() if k not in {"page", "pageNo", "size", "numOfRows"}}, sort_keys=True)
        item = groups.setdefault(key, [0, meta.get("total")])
        item[0] += meta.get("rows", 0)
    return {key for key, (received, total) in groups.items() if total is not None and received == total}


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


def repair_tasks(job, start, end):
    """Include internal/trailing gaps and missing hourly values; never invent rows."""
    seen, tasks = {}, set()
    prefix = {"koen": "qhorGen", "kospo": "qhorgen", "iwest": "q", "komipo": "q"}.get(job.source)
    for day, resource, payload in job.db.execute("SELECT day,resource,payload FROM records ORDER BY resource,day"):
        selected = date.fromisoformat(day)
        if not start <= selected <= end:
            continue
        seen.setdefault(resource, []).append(selected)
        row = json.loads(payload)
        fields = ["smp"] if job.source == "smp" else [f"{prefix}{h:02d}" for h in range(1, 25)]
        if any(row.get(field) in (None, "", "null") for field in fields):
            tasks.add((selected.year, selected.month, resource if job.source == "komipo" else ""))
    for resource, days in seen.items():
        for earlier, later in zip(days, [*days[1:], end + timedelta(days=1)]):
            cursor = earlier + timedelta(days=1)
            while cursor < later:
                tasks.add((cursor.year, cursor.month, resource if job.source == "komipo" else ""))
                cursor += timedelta(days=1)
    # Whole missing dates, including gaps preceding the first response, also need
    # a retry; absence is not evidence that any particular plant existed then.
    present = {d for days in seen.values() for d in days}
    if job.source != "komipo":
        cursor = start
        while cursor <= end:
            if cursor not in present:
                tasks.add((cursor.year, cursor.month, ""))
            cursor += timedelta(days=1)
    return sorted(tasks)


def repair_observed_gaps(job, current, state, start=None, end=None):
    bounds = job.db.execute("SELECT min(day),max(day) FROM records").fetchone()
    if start is None:
        if not bounds[0]:
            return []
        start = date.fromisoformat(bounds[0])
    end = end or current.date() - timedelta(days=1)
    failures = []
    # Fresh successful pages can resume a partially refreshed month without
    # paying for the same pages on every cycle.
    job.cache_seconds = 3600
    tasks = repair_tasks(job, start, end)
    tasks.sort(key=lambda item: state.get(f"repair_v2:{item[0]:04d}{item[1]:02d}:{item[2]}", ""))
    for year, month, resource in tasks:
        task = f"repair_v2:{year:04d}{month:02d}:{resource}"
        if state.get(task, "") > now():
            continue
        left = max(start, date(year, month, 1))
        right = min(end, next(months(date(year, month, 1), date(year+1, 1, 1)))[1])
        try:
            if job.source in {"koen", "kospo", "iwest", "komipo"}:
                roster = [resource.split(":")] if resource else KOMIPO_RESOURCES
                _, errors = fetch_generation_range(job, left, right, roster)
                if errors:
                    raise APIError("PROVIDER_PARTIAL", json.dumps(errors, ensure_ascii=False))
            else:
                # One day per task so a month cannot exceed the cycle budget
                # forever and starve all later missing SMP intervals.
                days = [left + timedelta(days=offset) for offset in range((right-left).days+1)]
                days.sort(key=lambda day: state.get(f"{task}:{day}", ""))
                for day in days:
                    day_task = f"{task}:{day}"
                    if state.get(day_task, "") > now():
                        continue
                    try:
                        for rows, rid, metadata in job.pages(dict(date=day.strftime("%Y%m%d"), dataType="json")):
                            job.save_records(rows, rid, metadata, left, right)
                    except APIError as exc:
                        if exc.code in CONTROL_ERRORS:
                            raise
                        failures.append({"task": day_task, "code": exc.code, "message": exc.message})
                    state[day_task] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        except APIError as exc:
            if exc.code in CONTROL_ERRORS:
                raise
            failures.append({"task": task, "code": exc.code, "message": exc.message})
        state[task] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return failures


def reserve_live_calls(source, args, current):
    per_cycle = {"komipo": len(KOMIPO_RESOURCES), "smp": args.lookback_days + 1,
                 "iwest": 2, "koen": 2, "kospo": 2}.get(source, 1)
    if source == "iwest":
        # Two days x <=45 observed resources fit the verified 100-row page.
        # Keep two calls when the window crosses a month boundary. The global
        # hard quota still applies if a response grows or needs a retry.
        first = current.date() - timedelta(days=args.lookback_days - 1)
        per_cycle = max(math.ceil(args.lookback_days * 45 / 100), 1 + int(first.month != current.month))
    return (24 - current.hour) * max(1, math.ceil(60 / args.interval_minutes)) * per_cycle


def backfill_source(source, args, key, current):
    if source == "forecast":
        return {"source": source, "state": "historical_archive_unavailable"}
    # Leave enough daily quota for the remaining hourly live checks.
    reserve = reserve_live_calls(source, args, current)
    limit = max(0, LIMITS[source] - reserve)
    cycle_cap = max(args.backfill_calls_per_cycle, len(KOMIPO_RESOURCES)) if source == "komipo" else args.backfill_calls_per_cycle
    hist = ManagedDownload(args.history_output, source, key, args.ledger, args.output / "STOP", max_calls=cycle_cap, call_limit=limit)
    status = {"source": source, "phase": "backfill", "started_at_utc": now(), "errors": []}
    changed = False
    changes_before = hist.db.total_changes
    cooldown_path = args.output / f"{source}_backfill_cooldown.json"
    cooldown = json.loads(cooldown_path.read_text(encoding="utf-8")) if cooldown_path.exists() else {}
    try:
        completed = completed_ranges(hist)
        if source in {"koen", "kospo", "iwest"}:
            pending = []
            for left, right in months(args.history_start, args.history_end):
                params, page_key, size_key, size = month_params(source, left, right)
                if json.dumps(params, sort_keys=True) not in completed:
                    pending.append((params, page_key, size_key, size))
            pending.sort(key=lambda item: cooldown.get("archive_v2:" + json.dumps(item[0], sort_keys=True), ""))
            for params, page_key, size_key, size in pending:
                task = "archive_v2:" + json.dumps(params, sort_keys=True)
                if cooldown.get(task, "") > now():
                    continue
                try:
                    for rows, rid, metadata in hist.pages(params, page_key, size_key, size):
                        hist.save_records(rows, rid, metadata, args.history_start, args.history_end)
                        changed = True
                except APIError as exc:
                    if exc.code in CONTROL_ERRORS:
                        raise
                    status["errors"].append({"task": task, "code": exc.code, "message": exc.message})
                cooldown[task] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        elif source == "smp":
            if json.dumps({"dataType": "json"}, sort_keys=True) not in completed:
                for rows, rid, metadata in hist.pages({"dataType": "json"}):
                    hist.save_records(rows, rid, metadata, args.history_start, args.history_end)
                    changed = True
        elif source == "komipo":
            for code, unit in sorted(KOMIPO_RESOURCES, key=lambda item: cooldown.get(f"resource_v2:{item[0]}:{item[1]}", "")):
                params = dict(strDateS=args.history_start.strftime("%Y%m%d"), strDateE=args.history_end.strftime("%Y%m%d"), strOrgNo=code, strHoki=unit)
                scope = f"resource_v2:{code}:{unit}"
                if json.dumps(params, sort_keys=True) in completed:
                    continue
                if cooldown.get(scope, "") > now():
                    continue
                try:
                    rows, total, _, rid, metadata = hist.request(params)
                    if total is not None and total != len(rows):
                        raise APIError("INCOMPLETE_RESPONSE", "Provider row count mismatch")
                    hist.save_records(rows, rid, metadata, args.history_start, args.history_end)
                    changed = True
                except APIError as exc:
                    if exc.code in CONTROL_ERRORS:
                        raise
                    status["errors"].append({"resource": scope, "code": exc.code, "message": exc.message})
                    cooldown[scope] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        status["state"] = "caught_up" if not status["errors"] else "provider_error"
        # Recheck gaps hourly, including null hour values and trailing dates.
        changes_before_gap_repair = hist.db.total_changes
        status["errors"].extend(repair_observed_gaps(hist, current, cooldown, args.history_start, args.history_end))
        changed = changed or hist.db.total_changes > changes_before_gap_repair
        if status["errors"]:
            status["state"] = "provider_error"
    except APIError as exc:
        status.update(state="waiting", error={"code": exc.code, "message": exc.message})
    except Exception as exc:
        status.update(state="error", error={"code": "LOCAL_ERROR", "message": type(exc).__name__})
    finally:
        changed = changed or hist.db.total_changes > changes_before
        if changed:
            error = status.get("error")
            if not error and status["errors"]:
                error = {"code": "PROVIDER_PARTIAL", "message": json.dumps(status["errors"], ensure_ascii=False)}
            hist.export(args.history_start, args.history_end, error)
        status.update(stored_records=hist.count(), api_calls=hist.calls_this_cycle, finished_at_utc=now())
        hist.close()
        dump(cooldown_path, cooldown)
        dump(args.output / f"{source}_backfill_status.json", status)
    return status


def bridge_and_gaps(source, args, key, current):
    """Fill the interval after the fixed historical end and recheck observed gaps."""
    if source == "forecast":
        return
    reserve = reserve_live_calls(source, args, current)
    cycle_cap = max(args.backfill_calls_per_cycle, len(KOMIPO_RESOURCES)) if source == "komipo" else args.backfill_calls_per_cycle
    job = ManagedDownload(args.output / "live", source, key, args.ledger, args.output / "STOP", max_calls=cycle_cap, call_limit=max(0, LIMITS[source] - reserve))
    task_file = args.output / f"{source}_gap_tasks.json"
    state = json.loads(task_file.read_text(encoding="utf-8")) if task_file.exists() else {}
    waiting_for_source = False
    left, right = args.history_end + timedelta(days=1), current.date() - timedelta(days=args.lookback_days)
    try:
        if left <= right:
            for start, end in months(left, right):
                task = f"bridge_v2:{start}:{end}"
                if state.get(task, "") > now():
                    continue
                if source == "smp":
                    for day in (start + timedelta(days=i) for i in range((end-start).days+1)):
                        for rows, rid, meta in job.pages(dict(date=day.strftime("%Y%m%d"), dataType="json")):
                            job.save_records(rows, rid, meta, start, end)
                else:
                    received, errors = fetch_generation_range(job, start, end)
                    if errors:
                        state.setdefault("provider_errors", {})[task] = errors
                        state[task] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                        waiting_for_source = True
                        continue
                    if not received:
                        state[task] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                        waiting_for_source = True
                        continue
                    state.get("provider_errors", {}).pop(task, None)
                state[task] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        # Check days missing between each observed resource's first and last day.
        # Avoid inferring that a resource existed before its first observation.
        state["repair_errors"] = repair_observed_gaps(job, current, state, args.history_end + timedelta(days=1), current.date() - timedelta(days=1))
        state["last_state"] = "waiting_for_source" if waiting_for_source else "checked"
        state.pop("error", None)
    except APIError as exc:
        state.update(last_state="waiting", error={"code": exc.code, "message": exc.message})
    finally:
        export_live(job)
        job.close()
        dump(task_file, state)


def run_cycle(args, key):
    current = datetime.now(KST)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(live_source, source, args, key, current): source for source in args.sources}
        for future in concurrent.futures.as_completed(pending):
            source = pending[future]
            try:
                results[source] = future.result()
            except Exception as exc:
                results[source] = {"source": source, "state": "error", "error": type(exc).__name__}
    for source in args.sources:
        if (args.output / "STOP").exists():
            break
        try:
            # A long cycle or suspended PC may resume on a new KST day.
            # Reserve live-call quota using the current clock for each phase.
            bridge_and_gaps(source, args, key, datetime.now(KST))
            backfill_source(source, args, key, datetime.now(KST))
        except Exception as exc:
            emit(source=source, phase="maintenance_error", error=type(exc).__name__)
    return results


def next_cycle_at(started, current, interval_minutes):
    regular = started + timedelta(minutes=interval_minutes)
    local = current.astimezone(KST)
    reset = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), KST) + timedelta(seconds=20)
    return max(current, min(regular, reset.astimezone(timezone.utc)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/continuous_collection")
    parser.add_argument("--history-output", type=Path, default=ROOT / "data/api_history_20220101_20261031")
    parser.add_argument("--history-start", type=date.fromisoformat, default=date(2022,1,1))
    parser.add_argument("--history-end", type=date.fromisoformat, default=date(2026,10,31))
    parser.add_argument("--interval-minutes", type=float, default=60)
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--sources", default="koen,kospo,komipo,iwest,smp")
    parser.add_argument("--backfill-calls-per-cycle", type=int, default=20)
    parser.add_argument("--forecast-max-issues", type=int, default=0, help="0 recovers every issue still inside KMA's three-day window")
    parser.add_argument("--iwest-daily-limit", type=int, default=100)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.interval_minutes) or args.interval_minutes < 1 or not 1 <= args.lookback_days <= 7 or args.backfill_calls_per_cycle < 1 or args.history_start > args.history_end or args.iwest_daily_limit < 11 or args.forecast_max_issues < 0:
        parser.error("Invalid interval, dates, lookback or budget")
    args.sources = list(dict.fromkeys(args.sources.split(",")))
    if any(s not in ENDPOINTS for s in args.sources):
        parser.error("Unknown source")
    args.output.mkdir(parents=True, exist_ok=True)
    args.ledger = ROOT / "data/public_api_usage.sqlite"
    key = unquote(dotenv_values(ROOT / ".env").get("DATA_GO_KR_API_KEY") or "").strip()
    if not key:
        parser.error("DATA_GO_KR_API_KEY is missing")
    LIMITS["iwest"] = args.iwest_daily_limit - 10
    lock = Singleton(args.output / "collector.lock")
    ledger = UsageLedger(args.ledger)
    ledger.seed(args.history_output)
    ledger.seed(args.output / "live")
    ledger.close()
    status = {"pid": os.getpid(), "started_at_utc": now(), "interval_minutes": args.interval_minutes, "sources": args.sources, "history_start": str(args.history_start), "history_end": str(args.history_end), "output": str(args.output), "cycles_completed": 0}
    try:
        while not (args.output / "STOP").exists():
            status.update(state="collecting", cycle_started_at_utc=now(), next_run_at_utc=None)
            dump(args.output / "collector_status.json", status)
            try:
                results = run_cycle(args, key)
            except Exception as exc:
                # A cycle failure must not silently terminate periodic polling.
                results = {"scheduler": {"state": "error", "error": type(exc).__name__}}
                emit(phase="cycle_error", error=type(exc).__name__)
            status["cycles_completed"] += 1
            status.update(state="waiting", last_cycle_finished_at_utc=now(), last_results=results)
            next_run = next_cycle_at(datetime.fromisoformat(status["cycle_started_at_utc"]), datetime.now(timezone.utc), args.interval_minutes)
            status["next_run_at_utc"] = next_run.isoformat()
            dump(args.output / "collector_status.json", status)
            if args.once:
                break
            while datetime.now(timezone.utc) < next_run and not (args.output / "STOP").exists():
                time.sleep(min(5, max(0, (next_run-datetime.now(timezone.utc)).total_seconds())))
        status.update(state="one_cycle_complete" if args.once else "stopped", updated_at_utc=now())
        dump(args.output / "collector_status.json", status)
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
