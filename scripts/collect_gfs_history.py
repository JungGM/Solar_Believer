"""Archive day-ahead NOAA GFS Korean grids, oldest first, in PostgreSQL.

Source GRIB messages are range-downloaded, decoded without interpolation, and
regional source values are retained in compressed JSON. Never use analysis data.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import threading

import eccodes as ec
import numpy as np
import requests
from psycopg.types.json import Jsonb

from scripts.maintain_public_data import Singleton
from scripts.postgres_store import connect_postgres, persist_runtime_state

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "noaa_gfs"
KST = timezone(timedelta(hours=9))
UTC = timezone.utc
# The Windows ecCodes build's definition parser is not thread safe.
DECODE_LOCK = threading.Lock()
FIELDS = {
    "temperature_2m": ("TMP", "2 m above ground"),
    "relative_humidity_2m": ("RH", "2 m above ground"),
    "wind_u_10m": ("UGRD", "10 m above ground"),
    "wind_v_10m": ("VGRD", "10 m above ground"),
    "cloud_cover": ("TCDC", "entire atmosphere"),
    "shortwave_down": ("DSWRF", "surface"),
    "precipitation": ("APCP", "surface"),
}


def run_for_day(day):
    # Previous-day 08 KST cutoff. 12 UTC two calendar days earlier is 11h before it.
    return datetime.combine(day - timedelta(days=2), datetime.min.time(), UTC).replace(hour=12)


def source_url(run, lead):
    run = run.astimezone(UTC)
    return (f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{run:%Y%m%d}/"
            f"{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{lead:03}")


def select_messages(index):
    rows = [line.split(":") for line in index.splitlines() if line.strip()]
    selected = {}
    for name, (variable, level) in FIELDS.items():
        matches = []
        for i, row in enumerate(rows):
            if len(row) < 7 or (row[3], row[4]) != (variable, level):
                continue
            if name == "cloud_cover" and "ave" in row[5]:
                continue
            start = int(row[1])
            end = int(rows[i + 1][1]) - 1 if i + 1 < len(rows) else None
            matches.append((start, end, ":".join(row)))
        if not matches:
            raise ValueError(f"Missing GFS field: {name}")
        # APCP can include both 0-lead and a shorter accumulation. Keep shortest interval.
        selected[name] = matches[0]
    return selected


def download_message(session, url, start, end):
    if end is None or end < start or end - start > 16_000_000:
        raise ValueError("Unsafe GRIB byte range")
    with session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=(15, 90), stream=True) as r:
        r.raise_for_status()
        if r.status_code != 206 or not r.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
            raise ValueError("Server did not honor requested byte range")
        raw = r.raw.read(end - start + 2)
    if len(raw) != end - start + 1 or raw[:4] != b"GRIB" or raw[-4:] != b"7777":
        raise ValueError("Incomplete GRIB message")
    return raw


def decode_region(raw, run, target):
    with DECODE_LOCK:
        return _decode_region(raw, run, target)


def _decode_region(raw, run, target):
    handle = ec.codes_new_from_message(raw)
    try:
        expected_run = (int(run.strftime("%Y%m%d")), run.hour * 100)
        expected_target = (int(target.strftime("%Y%m%d")), target.hour * 100)
        if (ec.codes_get(handle, "dataDate"), ec.codes_get(handle, "dataTime")) != expected_run:
            raise ValueError("Wrong forecast initialization")
        if (ec.codes_get(handle, "validityDate"), ec.codes_get(handle, "validityTime")) != expected_target:
            raise ValueError("Wrong forecast valid time")
        lat = ec.codes_get_array(handle, "latitudes")
        lon = ec.codes_get_array(handle, "longitudes")
        mask = (lat >= 32) & (lat <= 40) & (lon >= 124) & (lon <= 132)
        values = ec.codes_get_values(handle)[mask]
        if len(values) != 1089:
            raise ValueError(f"Unexpected regional cell count {len(values)}")
        missing = ec.codes_get(handle, "missingValue")
        result = {
            "latitude": lat[mask].tolist(), "longitude": lon[mask].tolist(),
            "values": [float(x) if np.isfinite(x) and x != missing else None for x in values],
        }
        keys = ("shortName", "units", "typeOfLevel", "level", "stepType", "startStep", "endStep",
                "stepRange", "gridType", "Ni", "Nj", "iDirectionIncrementInDegrees",
                "jDirectionIncrementInDegrees")
        metadata = {key: ec.codes_get(handle, key) for key in keys}
        metadata["missing_cells"] = result["values"].count(None)
        if metadata["missing_cells"] == len(values):
            raise ValueError("Entire regional field is missing")
        return result, metadata
    finally:
        ec.codes_release(handle)


def ensure_tables():
    with connect_postgres() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS public.noaa_gfs_forecast_grids(
            run_at TIMESTAMPTZ NOT NULL,target_at TIMESTAMPTZ NOT NULL,variable TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL,body_gzip BYTEA NOT NULL,metadata JSONB NOT NULL,
            PRIMARY KEY(run_at,target_at,variable))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS public.noaa_gfs_collection_tasks(
            target_at TIMESTAMPTZ PRIMARY KEY,run_at TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")


def seed(start, end):
    with connect_postgres() as conn:
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            run = run_for_day(day)
            for hour in range(6, 22):
                target = datetime.combine(day, datetime.min.time(), KST).replace(hour=hour)
                conn.execute("""INSERT INTO public.noaa_gfs_collection_tasks(target_at,run_at)
                    VALUES(%s,%s) ON CONFLICT DO NOTHING""", (target, run))


def collect_target(target, run):
    run = run.astimezone(UTC)
    lead = int((target - run).total_seconds() // 3600)
    url = source_url(run, lead)
    downloaded = 0
    try:
        with connect_postgres() as conn, requests.Session() as session:
            completed = {r[0] for r in conn.execute(
                "SELECT variable FROM public.noaa_gfs_forecast_grids WHERE run_at=%s AND target_at=%s",
                (run, target))}
            conn.commit()
            if len(completed) < len(FIELDS):
                response = session.get(url + ".idx", timeout=(15, 45))
                response.raise_for_status()
                index = response.text
                messages = select_messages(index)
                for variable, (start, end, line) in messages.items():
                    if variable in completed:
                        continue
                    raw = download_message(session, url, start, end)
                    downloaded += len(raw)
                    region, metadata = decode_region(raw, run, target.astimezone(UTC))
                    received = datetime.now(UTC)
                    metadata.update({
                        "source": "NOAA GFS 0.25 degree", "url": url, "index_line": line,
                        "index_sha256": hashlib.sha256(index.encode()).hexdigest(),
                        "grib_sha256": hashlib.sha256(raw).hexdigest(), "range": [start, end],
                        "downloaded_bytes": len(raw), "bbox": [124, 32, 132, 40],
                        "lead_hours": lead, "run_at": run.isoformat(), "target_at": target.isoformat(),
                        "cutoff_kst": (target.astimezone(KST).replace(hour=8) - timedelta(days=1)).isoformat(),
                        "historical_publication_time": None,
                        "availability_policy": "Conservative older run; historical distribution time unverified",
                        "representation": "Decoded regional source grid; no interpolation or unit conversion",
                        "full_global_grib_retained": False,
                    })
                    body = gzip.compress(json.dumps(region, allow_nan=False, separators=(",", ":")).encode())
                    conn.execute("""INSERT INTO public.noaa_gfs_forecast_grids
                        VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (run, target, variable, received, body, Jsonb(metadata)))
                    conn.commit()
            conn.execute("""UPDATE public.noaa_gfs_collection_tasks SET state='done',error=NULL,
                attempts=attempts+1,updated_at=now() WHERE target_at=%s""", (target,))
        return {"target": target.isoformat(), "state": "done", "downloaded_bytes": downloaded}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:1500]
        with connect_postgres() as conn:
            conn.execute("""UPDATE public.noaa_gfs_collection_tasks SET state='retry',attempts=attempts+1,
                error=%s,next_attempt_at=now()+interval '15 minutes',updated_at=now() WHERE target_at=%s""",
                (error, target))
        return {"target": target.isoformat(), "state": "retry", "error": error,
                "downloaded_bytes": downloaded}


def status(last=None):
    with connect_postgres() as conn:
        states = dict(conn.execute("SELECT state,count(*) FROM public.noaa_gfs_collection_tasks GROUP BY state"))
        grids, first, latest, byte_count = conn.execute("""SELECT count(*),min(target_at),max(target_at),
            sum((metadata->>'downloaded_bytes')::bigint) FROM public.noaa_gfs_forecast_grids""").fetchone()
        oldest = conn.execute("""SELECT min(target_at) FROM public.noaa_gfs_collection_tasks
            WHERE state<>'done'""").fetchone()[0]
    document = {"pid": os.getpid(), "updated_at": datetime.now(UTC).isoformat(), "source": "NOAA GFS",
                "order": "oldest target date first; failed requests retried after 15 minutes",
                "states": states, "grid_fields": grids, "first_target": str(first), "latest_target": str(latest),
                "oldest_incomplete": str(oldest), "downloaded_grib_bytes": int(byte_count or 0), "last": last}
    persist_runtime_state(OUTPUT / "collector_status.json", document)
    temporary = OUTPUT / "collector_status.tmp"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(OUTPUT / "collector_status.json")
    print(json.dumps(document, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, help="Omit to advance through the current KST date")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--once", action="store_true", help="Process one oldest pending day, then exit")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    lock = Singleton(OUTPUT / "collector.lock")
    started = time.time()
    try:
        ensure_tables()
        seeded_end = None
        while True:
            stop = OUTPUT / "STOP"
            if stop.exists() and stop.stat().st_mtime >= started:
                break
            end = args.end or datetime.now(KST).date()
            if args.start > end:
                raise ValueError("Start date is later than end date")
            if seeded_end != end:
                seed(args.start if seeded_end is None else seeded_end + timedelta(days=1), end)
                seeded_end = end
            with connect_postgres() as conn:
                rows = conn.execute("""SELECT target_at,run_at FROM public.noaa_gfs_collection_tasks
                    WHERE state<>'done' AND next_attempt_at<=now()
                    AND (target_at AT TIME ZONE 'Asia/Seoul')::date BETWEEN %s AND %s
                    ORDER BY target_at LIMIT 16""", (args.start, end)).fetchall()
            status()
            if rows:
                # Complete an older date before advancing to a newer one.
                oldest_day = rows[0][0].astimezone(KST).date()
                rows = [r for r in rows if r[0].astimezone(KST).date() == oldest_day]
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    for future in as_completed([pool.submit(collect_target, *row) for row in rows]):
                        status(future.result())
            if args.once:
                break
            if not rows:
                # Interruptible idle wait; STOP is checked every second.
                for _ in range(60):
                    if stop.exists() and stop.stat().st_mtime >= started:
                        break
                    time.sleep(1)
    finally:
        lock.close()


if __name__ == "__main__":
    main()
