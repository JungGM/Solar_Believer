"""Collect hourly day-ahead archives since 2022 and updated tomorrow forecasts."""

import argparse
import csv
import json
import math
import os
import signal
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from psycopg.types.json import Jsonb

from scripts.open_meteo_store import Store, identity

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
KST = timezone(timedelta(hours=9))
VARIABLES = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "cloud_cover", "cloud_cover_low",
    "cloud_cover_mid", "cloud_cover_high", "wind_speed_10m", "wind_direction_10m", "pressure_msl",
    "precipitation", "snowfall", "snow_depth", "shortwave_radiation", "direct_radiation",
    "diffuse_radiation", "direct_normal_irradiance", "terrestrial_radiation", "sunshine_duration", "is_day",
)
MODELS = {"gfs_global": "ncep_gfs013", "jma_gsm": "jma_gsm"}
PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
LIVE = "https://api.open-meteo.com/v1/forecast"
METADATA = "https://api.open-meteo.com/data/{}/static/meta.json"


class QuotaWait(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, seconds)


def chunks(start, end):
    while start <= end:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = min(end, following - timedelta(days=1))
        yield start, last
        start = last + timedelta(days=1)


def call_cost(start, end, variables=VARIABLES):
    # Conservative weighted quota: multiple locations are never treated as one call.
    return math.ceil(((end - start).days + 1) / 14) * math.ceil(len(variables) / 10)


def known_locations(store):
    """Only registered coordinates with provenance; never geocode/infer missing plants."""
    members = []
    for source, resource, scope, metadata in store.query(
        "SELECT source,resource,scope_id,metadata FROM public.collector_resource_registry"
    ).fetchall():
        known = metadata.get("provider_metadata") or {}
        if known.get("latitude") and known.get("longitude"):
            members.append({"source": source, "resource_id": resource, "scope_id": scope,
                            "latitude": known["latitude"], "longitude": known["longitude"],
                            "coordinate_source": "registered provider_metadata; unit-level accuracy unverified"})
    path = ROOT / "data/raw/koen_plants_meta.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                members.append({"source": "koen", "resource_id": row["ippt"] + ":" + row["hogi"],
                                "scope_id": row["plant_id"], "latitude": row["latitude"],
                                "longitude": row["longitude"], "coordinate_source":
                                "registered koen_plants_meta.csv; unit-level accuracy unverified"})
    path = ROOT / "data/source_catalogs/generator_metadata.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("coordinate_source"):
                    members.append(row)
    # Explicit metadata takes precedence, matching exact source/resource identifiers.
    unique = {(m["source"], m["resource_id"]): m for m in members}
    locations = {}
    for member in unique.values():
        try:
            lat, lon = float(member["latitude"]), float(member["longitude"])
        except (ValueError, TypeError, KeyError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        location_id = identity([lat, lon])[:24]
        item = locations.setdefault(location_id, {"location_id": location_id, "latitude": lat,
                                                 "longitude": lon, "members": []})
        item["members"].append(member)
    return list(locations.values())


class Collector:
    def __init__(self, args, store=None, session=None):
        self.args = args
        self.store = store or Store()
        self.session = session or requests.Session()
        self.started = datetime.now(UTC)
        self.stop = threading.Event()
        self.locations = []
        self.last_metadata = {}
        self.last_error = None
        self.last_live_check = 0.0
        self.last_seed = 0.0
        self.calls = 0

    def seed(self, today):
        self.locations = known_locations(self.store)
        if not self.locations:
            raise ValueError("No registered plant coordinates; add source-backed generator metadata first")
        end = min(self.args.end or today, today)
        for location in self.locations:
            self.store.query("""INSERT INTO public.open_meteo_locations VALUES(%s,%s,%s,%s,now())
                ON CONFLICT(location_id) DO UPDATE SET members=excluded.members,updated_at=now()""",
                (location["location_id"], location["latitude"], location["longitude"], Jsonb(location["members"])))
            for model in self.args.models:
                for lead in self.args.lead_days:
                    stream = identity([location["location_id"], model, lead, VARIABLES])
                    previous = self.store.state("seeded:" + stream)
                    start = max(self.args.start, date.fromisoformat(previous["end"]) + timedelta(days=1)) if previous else self.args.start
                    for first, last in chunks(start, end):
                        task_id = identity([stream, str(first), str(last)])
                        self.store.query("""INSERT INTO public.open_meteo_tasks
                            (task_id,location_id,model,lead_days,start_date,end_date)
                            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (task_id, location["location_id"], model, lead, first, last))
                    if start <= end:
                        self.store.state("seeded:" + stream, {"end": str(end)})
        self.store.conn.commit()

    def reserve(self, cost, mode, at):
        limits = [(60, 400), (3600, 4000), (86400, self.args.daily_budget - (500 if mode == "historical_fixed_lead" else 0))]
        for seconds, limit in limits:
            rows = self.store.query("SELECT at,cost FROM public.open_meteo_calls WHERE at>%s ORDER BY at",
                                    (at - timedelta(seconds=seconds),)).fetchall()
            total = sum(r[1] for r in rows)
            if total + cost > limit:
                release = rows[0][0] + timedelta(seconds=seconds, microseconds=1) if rows else at + timedelta(seconds=seconds)
                self.store.conn.commit()
                raise QuotaWait((release - at).total_seconds())
        call_id = self.store.query("""INSERT INTO public.open_meteo_calls(at,cost,mode,status)
            VALUES(%s,%s,%s,'reserved') RETURNING id""", (at, cost, mode)).fetchone()[0]
        self.store.conn.commit()
        return call_id

    def fetch(self, task, location, metadata=None):
        start, end = task["start_date"], task["end_date"]
        lead = task.get("lead_days", 0)
        suffix = f"_previous_day{lead}" if lead else ""
        params = {"latitude": location["latitude"], "longitude": location["longitude"],
                  "start_date": str(start), "end_date": str(end), "timezone": "Asia/Seoul",
                  "timeformat": "unixtime", "wind_speed_unit": "ms", "models": task["model"],
                  "hourly": ",".join(name + suffix for name in VARIABLES)}
        at = datetime.now(UTC)
        call_id = self.reserve(call_cost(start, end), task["mode"], at)
        if task.get("task_id"):
            self.store.query("""UPDATE public.open_meteo_tasks SET attempts=attempts+1,due_at=%s
                WHERE task_id=%s""", (at + timedelta(minutes=5), task["task_id"]))
            self.store.conn.commit()
        try:
            response = self.session.get(PREVIOUS if lead else LIVE, params=params, timeout=(10, 60))
            response.raise_for_status()
            result = self.store.save(task, params, response.content, VARIABLES, at, datetime.now(UTC), metadata)
            self.store.query("UPDATE public.open_meteo_calls SET status='received' WHERE id=%s", (call_id,))
            self.store.conn.commit()
            self.calls += 1
            self.last_error = None
            return result
        except Exception as exc:
            self.store.conn.rollback()
            # Avoid propagating query strings or credentials into status/logs.
            error = type(exc).__name__
            code = getattr(getattr(exc, "response", None), "status_code", None)
            delay = 3600 if code == 429 else min(21600, 300 * (2 ** min(task.get("attempts", 0), 6)))
            self.store.query("UPDATE public.open_meteo_calls SET status=%s WHERE id=%s", (error, call_id))
            if task.get("task_id"):
                self.store.query("""UPDATE public.open_meteo_tasks SET state='error',error=%s,due_at=%s
                    WHERE task_id=%s""", (error, datetime.now(UTC) + timedelta(seconds=delay), task["task_id"]))
            self.store.conn.commit()
            self.last_error = error
            if code == 429:
                self.store.state("provider_block", {"until": (datetime.now(UTC) + timedelta(hours=1)).isoformat()})
                raise QuotaWait(3600) from None
            return None

    def collect_live(self, current):
        tomorrow = current.astimezone(KST).date() + timedelta(days=1)
        for model in self.args.models:
            try:
                response = self.session.get(METADATA.format(MODELS[model]), timeout=(10, 20))
                response.raise_for_status()
                metadata = response.json()
                for field in ("last_run_initialisation_time", "last_run_availability_time", "update_interval_seconds"):
                    if not isinstance(metadata.get(field), (int, float)) or metadata[field] <= 0:
                        raise ValueError("Invalid provider model metadata")
                self.last_metadata[model] = metadata
                self.store.state("provider_metadata", self.last_metadata)
                ready = metadata["last_run_availability_time"] + 600
                if current.timestamp() < ready:
                    continue
                cycle = str(metadata["last_run_initialisation_time"]) + ":" + str(tomorrow)
                for location in self.locations:
                    key = "live:" + model + ":" + location["location_id"]
                    if (self.store.state(key) or {}).get("cycle") == cycle:
                        continue
                    task = {"location_id": location["location_id"], "model": model,
                            "mode": "live_forecast", "lead_days": 0,
                            "start_date": tomorrow, "end_date": tomorrow}
                    result = self.fetch(task, location, metadata)
                    if result and all(result[1]["non_null_hours"][v] == 24 for v in
                                      ("temperature_2m", "cloud_cover")):
                        self.store.state(key, {"cycle": cycle, "request_id": result[0],
                                              "target_date_kst": str(tomorrow)})
                    if self.stop.is_set():
                        return
            except QuotaWait:
                raise
            except (requests.RequestException, ValueError, KeyError) as exc:
                self.last_error = "metadata:" + model + ":" + type(exc).__name__

    def history_one(self):
        task = self.store.query("""SELECT task_id,location_id,model,lead_days,start_date,end_date,attempts
            FROM public.open_meteo_tasks WHERE state!='complete' AND due_at<=now()
            ORDER BY CASE WHEN state IN ('pending','error') THEN 0 ELSE 1 END,start_date,lead_days,
            CASE WHEN model='jma_gsm' THEN 0 ELSE 1 END,location_id
            LIMIT 1""").fetchone()
        if not task:
            self.store.conn.commit()
            return False
        values = dict(zip(("task_id", "location_id", "model", "lead_days", "start_date", "end_date", "attempts"), task))
        location = next((loc for loc in self.locations if loc["location_id"] == values["location_id"]), None)
        if location is None:
            self.store.query("UPDATE public.open_meteo_tasks SET due_at=now()+interval '1 day' WHERE task_id=%s", (task[0],))
            self.store.conn.commit()
            return False
        self.fetch({**values, "mode": "historical_fixed_lead"}, location)
        return True

    def report(self, state="running"):
        row = self.store.query("""SELECT count(*),min(target_at),max(target_at) FROM public.open_meteo_hourly""").fetchone()
        counts = self.store.query("SELECT state,count(*) FROM public.open_meteo_tasks GROUP BY state").fetchall()
        usage = self.store.query("SELECT coalesce(sum(cost),0) FROM public.open_meteo_calls WHERE at>now()-interval '24 hours'").fetchone()[0]
        locations = self.store.query("SELECT count(*),coalesce(sum(jsonb_array_length(members)),0) FROM public.open_meteo_locations").fetchone()
        result = {"state": state, "pid": os.getpid(), "started_at_utc": self.started.isoformat(),
                  "updated_at_utc": datetime.now(UTC).isoformat(), "database": "PostgreSQL",
                  "history_start_kst": str(self.args.start), "history_target_end_kst": str(self.args.end or datetime.now(KST).date()),
                  "location_count": locations[0], "coordinate_resource_members": locations[1],
                  "models": self.args.models, "variables": list(VARIABLES), "lead_days": self.args.lead_days,
                  "hourly_snapshot_rows": row[0], "first_target_utc": str(row[1]), "last_target_utc": str(row[2]),
                  "task_counts": dict(counts), "weighted_calls_last_24h": usage,
                  "daily_budget": self.args.daily_budget, "poll_seconds": self.args.poll_seconds,
                  "metadata": self.last_metadata, "last_error": self.last_error,
                  "time_contract": "Live: tomorrow 00..23 KST. Archive: fixed 24h/48h lead, not a fixed daily issue.",
                  "limitations": ["Registered coordinates only; missing plant locations are not inferred.",
                                  "NULL is missing, never zero. Partial archive responses are retried after 30 days.",
                                  "Model metadata is a run reference, not proof every seamless field uses that run.",
                                  "Hourly weather rows are snapshots, not a complete-fleet or training-completion claim."]}
        self.store.state("status", result)
        self.args.output.mkdir(parents=True, exist_ok=True)
        temp = self.args.output / ("collector_status." + str(os.getpid()) + ".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.args.output / "collector_status.json")
        return result

    def run(self):
        namespace = os.environ.get("COLLECTOR_SERVICE_SCHEMA", "public")
        locked = self.store.conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (namespace + ":open_meteo_collector",)).fetchone()[0]
        self.store.conn.commit()
        if not locked:
            raise RuntimeError("An Open-Meteo collector already owns this PostgreSQL database")
        self.args.output.mkdir(parents=True, exist_ok=True)
        started_seconds = time.time()
        last_report = 0.0
        try:
            while not self.stop.is_set():
                stopfile = self.args.output / "STOP"
                if stopfile.exists() and stopfile.stat().st_mtime >= started_seconds:
                    break
                try:
                    current = datetime.now(UTC)
                    if not self.last_seed or time.monotonic() - self.last_seed >= 3600:
                        self.seed(current.astimezone(KST).date())
                        self.last_seed = time.monotonic()
                    blocked = self.store.state("provider_block")
                    if blocked and datetime.fromisoformat(blocked["until"]) > current:
                        raise QuotaWait((datetime.fromisoformat(blocked["until"]) - current).total_seconds())
                    # Historical training data has priority. Live polling uses the
                    # reserved quota once pending history is drained or quota-limited.
                    fetched = self.history_one() if not self.args.no_history else False
                    if not fetched and (not self.last_live_check or time.monotonic() - self.last_live_check >= self.args.poll_seconds):
                        self.collect_live(current)
                        self.last_live_check = time.monotonic()
                    if time.monotonic() - last_report > 15:
                        self.report()
                        last_report = time.monotonic()
                    if self.args.once or (self.args.max_requests and self.calls >= self.args.max_requests):
                        break
                    self.stop.wait(self.args.delay if fetched else 10)
                except QuotaWait as exc:
                    blocked = self.store.state("provider_block")
                    if (not blocked or datetime.fromisoformat(blocked["until"]) <= datetime.now(UTC)) and (
                            not self.last_live_check or time.monotonic() - self.last_live_check >= self.args.poll_seconds):
                        try:
                            self.collect_live(datetime.now(UTC))
                            self.last_live_check = time.monotonic()
                        except QuotaWait:
                            pass
                    self.report("waiting_for_quota")
                    self.stop.wait(min(30, exc.seconds))
                except Exception as exc:
                    self.store.conn.rollback()
                    self.last_error = type(exc).__name__
                    self.report("retrying")
                    if self.args.once:
                        raise
                    self.stop.wait(30)
            return self.report("bounded_run_complete" if self.args.once or self.args.max_requests else "stopped")
        finally:
            self.session.close()
            self.store.close()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--models", nargs="+", choices=tuple(MODELS), default=list(MODELS))
    p.add_argument("--lead-days", nargs="+", type=int, choices=(1, 2), default=[1, 2])
    p.add_argument("--daily-budget", type=int, default=9000)
    p.add_argument("--poll-seconds", type=int, default=300)
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--output", type=Path, default=ROOT / "data/open_meteo")
    p.add_argument("--once", action="store_true")
    p.add_argument("--no-history", action="store_true")
    p.add_argument("--max-requests", type=int, default=0)
    return p


def main():
    p = parser()
    args = p.parse_args()
    if ((args.end and args.end < args.start) or not 1000 <= args.daily_budget <= 9000
            or args.poll_seconds < 60 or args.max_requests < 0 or args.delay < 0.5 or not math.isfinite(args.delay)):
        p.error("Invalid date range, quota or intervals")
    collector = Collector(args)
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: collector.stop.set())
    result = collector.run()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
