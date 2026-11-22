"""Resume KMA Hub day-ahead grids and nationwide hourly observations.

Default target dates: 2022-01-01..2026-10-31; issue: previous day 08 KST.
Raw source values, missing sentinels and run identity are retained unmodified.
This is a local archive worker, not a DB ingestion or model-training worker.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector_credentials import get_api_key
from scripts.collect_public_history import KST, ROOT, decode_body, dump, now
from scripts.maintain_public_data import Singleton, stop_requested
from scripts.postgres_store import open_store
from backend.app.generation_policy import FORECAST_HOURS_KST, ZERO_HOURS_KST, POLICY_VERSION

BASE = "https://apihub.kma.go.kr/api/typ01/"
ENDPOINTS = {"forecast": BASE + "cgi-bin/url/nph-dfs_shrt_grd", "observation": BASE + "url/kma_sfctm2.php"}
VARIABLES = ("TMP", "TMX", "TMN", "UUU", "VVV", "VEC", "WSD", "SKY", "PTY", "POP", "PCP", "SNO", "REH", "WAV")
OBS_COLUMNS = "TM STN WD WS GST_WD GST_WS GST_TM PA PS PT PR TA TD HM PV RN RN_DAY RN_JUN RN_INT SD_HR3 SD_DAY SD_TOT WC WP WW CA_TOT CA_MID CH_MIN CT CT_TOP CT_MID CT_LOW VS SS SI ST_GD TS TE_005 TE_01 TE_02 TE_03 ST_SEA WH BF IR IX".split()
MAX_BODY = 16 * 1024 * 1024


class WaitForQuota(Exception):
    pass


def forecast_tasks(target_day, issue_hour=8):
    """Dates denote valid dates, not issue dates (Jan 1 uses Dec 31's run)."""
    issue = (target_day - timedelta(days=1)).strftime("%Y%m%d") + f"{issue_hour:02}"
    for hour in FORECAST_HOURS_KST:
        target = target_day.strftime("%Y%m%d") + f"{hour:02}"
        for variable in VARIABLES:
            yield f"forecast:{issue}:{target}:{variable}", {"tmfc": issue, "tmef": target, "vars": variable}


def observation_tasks(day):
    for hour in range(24):
        target = day.strftime("%Y%m%d") + f"{hour:02}00"
        yield f"observation:{target}", {"tm": target, "stn": 0, "help": 1}


def validate(raw, kind, params):
    """Reject HTML/permission/error bodies even with HTTP 200; no fabricated zeros."""
    text = decode_body(raw).strip()
    if kind == "forecast":
        parts = [part for part in re.split(r"[\s,]+", text) if part]
        if len(parts) != 149 * 253:
            raise ValueError("INVALID_GRID_LENGTH")
        values = [float(x) for x in parts]
        if not all(math.isfinite(x) for x in values):
            raise ValueError("NONFINITE_GRID")
        valid = sum(x != -99 for x in values)
        return (
            {
                "cells": len(values),
                "valid_cells": valid,
                "grid_shape": [253, 149],
                "grid_order": "source order; coordinate mapping not yet verified",
            },
            [],
            bool(valid),
        )
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != len(OBS_COLUMNS) or fields[0] != params["tm"] or not fields[1].isdigit():
            raise ValueError("INVALID_OBSERVATION_ROW")
        rows.append(dict(zip(OBS_COLUMNS, fields)))
    if not rows and not (text.startswith("#START") and "#END" in text):
        raise ValueError("INVALID_OBSERVATION_BODY")
    return (
        {"rows": len(rows), "columns": OBS_COLUMNS, "missing_policy": "source sentinel values retained"},
        rows,
        bool(rows),
    )


class Archive:
    def __init__(self, output, key, daily_calls=18000, daily_bytes=4_800_000_000, delay=0.7):
        self.output, self.key = Path(output), key
        self.output.mkdir(parents=True, exist_ok=True)
        self.db = open_store(self.output, "kma")
        self.daily_calls, self.daily_bytes, self.delay = daily_calls, daily_bytes, delay
        self.session = requests.Session()
        self.last_request = 0.0
        self.last_task = None
        # Retire old pending/retry night requests; retain already downloaded raw grids.
        self.db.execute(
            "UPDATE tasks SET state='excluded_by_hour_policy' WHERE kind='forecast' "
            "AND state IN ('pending','retry','empty') AND substr(params::jsonb->>'tmef',9,2) "
            "IN ('00','01','02','03','04','05','22','23')"
        )
        self.db.commit()

    def seed_day(self, kind, day, priority=10, issue_hour=8, available_before=None):
        tasks = forecast_tasks(day, issue_hour) if kind == "forecast" else observation_tasks(day)
        for task_id, params in tasks:
            if available_before and kind == "observation":
                if datetime.strptime(params["tm"], "%Y%m%d%H%M").replace(tzinfo=KST) > available_before:
                    continue
            self.db.execute(
                "INSERT INTO tasks(id,kind,day,params,priority) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET priority=least(tasks.priority,excluded.priority)",
                (task_id, kind, str(day), json.dumps(params, sort_keys=True), priority),
            )
        self.db.commit()

    def seed_history(self, start, end, issue_hour, forecast_only=False):
        # Lazy queues: retain a durable high-water date, only enqueue seven days ahead.
        for kind in ENDPOINTS:
            if forecast_only and kind == "observation":
                continue
            queue_key = f"queued:{kind}:{start}:{end}:{issue_hour}"
            stored = self.db.execute("SELECT value FROM settings WHERE key=?", (queue_key,)).fetchone()
            left = date.fromisoformat(stored[0]) + timedelta(days=1) if stored else start
            pending = self.db.execute(
                "SELECT count(*) FROM tasks WHERE kind=? AND state='pending'", (kind,)
            ).fetchone()[0]
            if pending >= (len(FORECAST_HOURS_KST) * len(VARIABLES) if kind == "forecast" else 24) * 3:
                continue
            for offset in range(7):
                day = left + timedelta(days=offset)
                if day > end:
                    break
                self.seed_day(kind, day, issue_hour=issue_hour)
                self.db.execute(
                    "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (queue_key, str(day)),
                )
                self.db.commit()

    def seed_live(self, current, issue_hour, forecast_only=False):
        # Recover every day since history end separately below; newest runs first.
        for offset in (0, 1, 2):
            issue_day = current.date() - timedelta(days=offset)
            ready = datetime.combine(issue_day, datetime.min.time(), KST).replace(hour=issue_hour, minute=30)
            if ready <= current:
                self.seed_day(
                    "forecast", issue_day + timedelta(days=1), priority=offset, issue_hour=issue_hour
                )
            if not forecast_only:
                self.seed_day(
                    "observation",
                    issue_day,
                    priority=offset,
                    available_before=current - timedelta(minutes=90),
                )
        # Recheck recent empty responses/sentinel-only grids after six hours.
        self.db.execute("UPDATE tasks SET state='retry' WHERE state='empty' AND due<=?", (time.time(),))
        self.db.commit()

    def usage(self, day=None):
        day = day or str(datetime.now(KST).date())
        row = self.db.execute(
            "SELECT count(*),coalesce(sum(bytes),0) FROM calls WHERE day=?", (day,)
        ).fetchone()
        return {"day_kst": day, "calls": int(row[0]), "bytes": int(row[1])}

    def claim(self):
        current = datetime.now(KST)
        usage = self.usage(str(current.date()))
        if usage["calls"] >= self.daily_calls or usage["bytes"] + MAX_BODY > self.daily_bytes:
            raise WaitForQuota("LOCAL_DAILY_LIMIT")
        if shutil.disk_usage(self.output).free < 2_000_000_000:
            raise WaitForQuota("LOW_DISK_SPACE")
        # Reserve maximum response bytes. An interrupted/timeout transfer stays reserved.
        cur = self.db.execute(
            "INSERT INTO calls(day,at,bytes,status) VALUES(?,?,?,?) RETURNING id",
            (str(current.date()), now(), MAX_BODY, "reserved"),
        )
        self.db.commit()
        return cur.lastrowid

    def next_task(self, kind):
        return self.db.execute(
            "SELECT * FROM tasks WHERE kind=? AND state IN ('pending','retry') "
            "AND due<=? ORDER BY priority,day,id LIMIT 1",
            (kind, time.time()),
        ).fetchone()

    def fetch_one(self, kind):
        task = self.next_task(kind)
        if task is None:
            return False
        call_id = self.claim()
        params = json.loads(task["params"])
        self.last_task = {"id": task["id"], "kind": kind, "day": task["day"]}
        pause = self.delay - (time.monotonic() - self.last_request)
        if pause > 0:
            time.sleep(pause)
        raw = bytearray()
        body_complete = False
        error = None
        state = "retry"
        self.last_request = time.monotonic()
        try:
            with self.session.get(
                ENDPOINTS[kind], params={**params, "authKey": self.key}, timeout=(10, 35), stream=True
            ) as response:
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BODY:
                        raise ValueError("BODY_TOO_LARGE")
                body_complete = True
                if response.status_code != 200:
                    raise ValueError(f"HTTP_{response.status_code}")
                summary, rows, has_data = validate(bytes(raw), kind, params)
                state = "ok" if has_data else "empty"
                fetched = now()
                digest = hashlib.sha256(raw).hexdigest()
                relative = (
                    Path("raw")
                    / kind
                    / task["day"][:7]
                    / (task["id"].replace(":", "_") + f"_{digest[:16]}.txt.gz")
                )
                target = self.output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    tmp = target.with_suffix(".tmp")
                    tmp.write_bytes(gzip.compress(bytes(raw), mtime=0))
                    tmp.replace(target)
                metadata = {
                    "endpoint": ENDPOINTS[kind],
                    "params": params,
                    "fetched_at_utc": fetched,
                    "sha256": digest,
                    "response_bytes": len(raw),
                    "state": state,
                    **summary,
                }
                dump(target.with_suffix(".json"), metadata)
                self.db.store_raw(relative, target.read_bytes(), metadata)
                if kind == "forecast":
                    from scripts.postgres_ingest import ingest_forecast_grid

                    ingest_forecast_grid(
                        self.db.connection, task["id"], params, target.read_bytes(), metadata
                    )
                for row in rows:
                    self.db.execute(
                        "INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT(tm,stn) DO UPDATE SET payload=excluded.payload,fetched=excluded.fetched",
                        (row["TM"], row["STN"], json.dumps(row), fetched),
                    )
                self.db.execute(
                    "UPDATE tasks SET path=?,summary=?,fetched=? WHERE id=?",
                    (str(relative), json.dumps(summary), fetched, task["id"]),
                )
        except requests.RequestException as exc:
            error = type(exc).__name__  # requests exceptions contain credential-bearing URLs.
        except (ValueError, UnicodeError) as exc:
            # Only our fixed error codes; never persist the source response or exception URL.
            error = (
                str(exc)
                if str(exc).startswith(("HTTP_", "INVALID_", "NONFINITE_", "BODY_"))
                else type(exc).__name__
            )
        finally:
            if body_complete:
                self.db.execute(
                    "UPDATE calls SET bytes=?,status=? WHERE id=?", (len(raw), error or state, call_id)
                )
            else:
                self.db.execute("UPDATE calls SET status=? WHERE id=?", (error or "interrupted", call_id))
            attempts = task["attempts"] + 1
            retry_seconds = min(86400, 300 * 2 ** min(attempts - 1, 8))
            empty_retry = 21600 if task["priority"] < 10 else 7 * 86400
            due = time.time() + (empty_retry if state == "empty" else retry_seconds if error else 0)
            self.db.execute(
                "UPDATE tasks SET state=?,attempts=?,due=?,error=? WHERE id=?",
                (state, attempts, due, error, task["id"]),
            )
            self.db.commit()
        return True

    def report(self, args, state, started, error=None):
        counts = {}
        for row in self.db.execute("SELECT kind,state,count(*) FROM tasks GROUP BY kind,state"):
            counts.setdefault(row[0], {})[row[1]] = row[2]
        history = {}
        for row in self.db.execute(
            "SELECT kind,state,count(*),min(day),max(day) FROM tasks "
            "WHERE day BETWEEN ? AND ? GROUP BY kind,state",
            (str(args.start), str(args.end)),
        ):
            history.setdefault(row[0], {})[row[1]] = {
                "requests": row[2],
                "first_target_day": row[3],
                "last_target_day": row[4],
            }
        observations = self.db.execute("SELECT count(*),min(tm),max(tm) FROM observations").fetchone()
        result = {
            "pid": os.getpid(),
            "started_at_utc": started,
            "updated_at_utc": now(),
            "state": state,
            "history_start": str(args.start),
            "history_end": str(args.end),
            "issue_hour_kst": args.issue_hour,
            "forecast_target": "next calendar day 06..21 KST",
            "target_hours_kst": list(FORECAST_HOURS_KST),
            "zero_generation_hours_kst": list(ZERO_HOURS_KST),
            "generation_policy": POLICY_VERSION,
            "enabled_sources": ["forecast"] if args.forecast_only else list(ENDPOINTS),
            "variables": list(VARIABLES),
            "history_planned_forecast_requests": ((args.end - args.start).days + 1)
            * len(FORECAST_HOURS_KST)
            * len(VARIABLES),
            "daily_call_limit": self.daily_calls,
            "daily_byte_limit": self.daily_bytes,
            "usage_today": self.usage(),
            "queued_task_counts": counts,
            "history": history,
            "observation_rows": observations[0],
            "observation_first_kst": observations[1],
            "observation_last_kst": observations[2],
            "last_task": self.last_task,
            "error": error,
            "storage": "PostgreSQL raw responses + forecast tasks + observations; local raw copies retained",
            "limitations": [
                "Raw forecast grid order is preserved; plant/grid mapping is not verified.",
                "Empty responses are not zero and are excluded from successful coverage.",
                "Only lazy-enqueued history is counted in queued_task_counts; planned total is separate.",
            ],
        }
        dump(self.output / "collector_status.json", result)
        return result

    def close(self):
        self.session.close()
        self.db.close()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 31))
    p.add_argument("--issue-hour", type=int, choices=(2, 5, 8, 11, 14, 17, 20, 23), default=8)
    p.add_argument("--output", type=Path, default=ROOT / "data/kma_hub")
    p.add_argument("--daily-calls", type=int, default=18000)
    p.add_argument("--daily-bytes", type=int, default=4_800_000_000)
    p.add_argument("--delay", type=float, default=0.7)
    p.add_argument("--max-calls", type=int, default=0, help="0 keeps running; useful for a bounded smoke run")
    p.add_argument("--no-live", action="store_true")
    p.add_argument(
        "--forecast-only", action="store_true", help="Only collect forecasts; retain existing observations"
    )
    return p


def run(args, stop_event=None):
    stop_event = stop_event or threading.Event()
    args.output.mkdir(parents=True, exist_ok=True)
    key = (
        get_api_key("KMA_APIHUB_KEY", ROOT / ".env")
    ).strip()
    if not key:
        raise ValueError("KMA_APIHUB_KEY is missing")
    lock = Singleton(args.output / "collector.lock")
    archive = Archive(args.output, key, args.daily_calls, args.daily_bytes, args.delay)
    started, last_seed, last_report, calls = now(), 0.0, 0.0, 0

    def stopped():
        return (
            stop_event.is_set()
            or stop_requested(args.output / "STOP")
            or stop_requested(ROOT / "data/continuous_collection/STOP")
        )

    try:
        while not stopped():
            try:
                current = datetime.now(KST)
                if time.monotonic() - last_seed > 300 or not last_seed:
                    archive.seed_history(args.start, args.end, args.issue_hour, args.forecast_only)
                    if not args.no_live:
                        archive.seed_live(current, args.issue_hour, args.forecast_only)
                        # Include all dates after the requested historical end, not just latest days.
                        if current.date() > args.end:
                            archive.seed_history(
                                args.end + timedelta(days=1),
                                current.date(),
                                args.issue_hour,
                                args.forecast_only,
                            )
                    last_seed = time.monotonic()
                if time.monotonic() - last_report >= 10 or not last_report:
                    archive.report(args, "collecting", started)
                    last_report = time.monotonic()
                # One observation hour per 14 forecast requests: neither family starves.
                kinds = ("observation", "forecast") if calls % 15 == 0 else ("forecast", "observation")
                if args.forecast_only:
                    kinds = ("forecast",)
                fetched = False
                for kind in kinds:
                    if archive.fetch_one(kind):
                        calls += 1
                        fetched = True
                        break
                if args.max_calls and calls >= args.max_calls:
                    break
                if not fetched:
                    archive.report(args, "waiting_for_retry_or_latest", started)
                    stop_event.wait(10)
                    last_seed = 0
            except WaitForQuota as exc:
                archive.report(args, "waiting_for_quota_or_disk", started, str(exc))
                stop_event.wait(10)
            except Exception as exc:
                # Keep the independent weather worker alive without leaking credential URLs.
                archive.report(args, "retrying_worker", started, type(exc).__name__)
                stop_event.wait(30)
        archive.report(
            args, "bounded_run_complete" if args.max_calls and calls >= args.max_calls else "stopped", started
        )
    finally:
        archive.close()
        lock.close()


def main():
    p = parser()
    args = p.parse_args()
    if (
        args.start > args.end
        or args.daily_calls < 1
        or args.daily_bytes <= MAX_BODY
        or not math.isfinite(args.delay)
        or args.delay < 0.2
        or args.max_calls < 0
    ):
        p.error("Invalid dates, limits or delay")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
