"""PostgreSQL persistence for immutable Open-Meteo forecast snapshots."""

import gzip
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from scripts.postgres_ingest import ensure_service_tables, execute
from scripts.postgres_store import connect_postgres

UTC = timezone.utc
DDL = """
CREATE TABLE IF NOT EXISTS public.open_meteo_locations(
 location_id TEXT PRIMARY KEY,latitude DOUBLE PRECISION NOT NULL,longitude DOUBLE PRECISION NOT NULL,
 members JSONB NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS public.open_meteo_tasks(
 task_id TEXT PRIMARY KEY,location_id TEXT NOT NULL,model TEXT NOT NULL,lead_days INTEGER NOT NULL,
 start_date DATE NOT NULL,end_date DATE NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0,due_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 request_id TEXT,error TEXT,coverage JSONB);
CREATE INDEX IF NOT EXISTS open_meteo_tasks_due ON public.open_meteo_tasks(state,due_at,start_date);
CREATE TABLE IF NOT EXISTS public.open_meteo_calls(
 id BIGSERIAL PRIMARY KEY,at TIMESTAMPTZ NOT NULL,cost DOUBLE PRECISION NOT NULL,
 mode TEXT NOT NULL,status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS open_meteo_calls_at ON public.open_meteo_calls(at);
CREATE TABLE IF NOT EXISTS public.open_meteo_requests(
 request_id TEXT PRIMARY KEY,task_id TEXT,location_id TEXT NOT NULL,model TEXT NOT NULL,
 mode TEXT NOT NULL,lead_days INTEGER,requested_at TIMESTAMPTZ NOT NULL,received_at TIMESTAMPTZ NOT NULL,
 model_initialised_at TIMESTAMPTZ,provider_available_at TIMESTAMPTZ,
 params JSONB NOT NULL,metadata JSONB NOT NULL,body_gzip BYTEA NOT NULL,sha256 TEXT NOT NULL,
 coverage JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS open_meteo_requests_live ON public.open_meteo_requests(mode,model,received_at);
CREATE TABLE IF NOT EXISTS public.open_meteo_hourly(
 request_id TEXT NOT NULL REFERENCES public.open_meteo_requests(request_id),
 location_id TEXT NOT NULL,model TEXT NOT NULL,mode TEXT NOT NULL,lead_days INTEGER,
 target_at TIMESTAMPTZ NOT NULL,reference_at TIMESTAMPTZ,
 received_at TIMESTAMPTZ NOT NULL,variables JSONB NOT NULL,units JSONB NOT NULL,
 missing_variables JSONB NOT NULL,PRIMARY KEY(request_id,target_at));
CREATE INDEX IF NOT EXISTS open_meteo_hourly_target ON public.open_meteo_hourly(location_id,model,target_at);
CREATE TABLE IF NOT EXISTS public.open_meteo_state(
 key TEXT PRIMARY KEY,document JSONB NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def parse_hourly(document, variables, lead_days, start, end):
    """Preserve nulls and UTC timestamps; reject truncated or malformed hourly arrays."""
    hourly = document.get("hourly", {})
    times = hourly.get("time", [])
    if not times or len(times) != len(set(times)):
        raise ValueError("Missing or duplicate hourly timestamps")
    suffix = f"_previous_day{lead_days}" if lead_days else ""
    keys = {name: name + suffix for name in variables}
    for key in keys.values():
        if not isinstance(hourly.get(key), list) or len(hourly[key]) != len(times):
            raise ValueError("Missing or unequal hourly variable array: " + key)
    units = {name: document.get("hourly_units", {}).get(key) for name, key in keys.items()}
    rows, coverage, seen = [], dict.fromkeys(variables, 0), set()
    for i, timestamp in enumerate(times):
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("Expected Unix UTC hourly timestamps")
        target = datetime.fromtimestamp(timestamp, UTC)
        local_day = (target + timedelta(hours=9)).date()
        if not start <= local_day <= end:
            continue
        if target.minute or target.second or target.microsecond:
            raise ValueError("Non-hourly timestamp")
        seen.add(target)
        values = {}
        for name, key in keys.items():
            value = hourly[key][i]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise ValueError("Nonfinite or invalid weather value")
            values[name] = value
            coverage[name] += value is not None
        rows.append((target, values, [name for name, value in values.items() if value is None]))
    expected = ((end - start).days + 1) * 24
    if len(rows) != expected or len(seen) != expected:
        raise ValueError(f"Incomplete hourly axis: {len(rows)}/{expected}")
    # Exact UTC axis is necessary for joining to the source's hour-ending intervals.
    origin = datetime.combine(start, datetime.min.time(), UTC) - timedelta(hours=9)
    if seen != {origin + timedelta(hours=i) for i in range(expected)}:
        raise ValueError("Hourly axis contains a gap")
    return rows, units, {"expected_hours": expected, "non_null_hours": coverage}


class Store:
    def __init__(self):
        self.conn = connect_postgres()
        ensure_service_tables(self.conn)
        for statement in DDL.split(";"):
            if statement.strip():
                execute(self.conn, statement)
        self.conn.commit()

    def query(self, statement, params=()):
        return execute(self.conn, statement, params)

    def state(self, key, document=None):
        if document is None:
            row = self.query("SELECT document FROM public.open_meteo_state WHERE key=%s", (key,)).fetchone()
            return row[0] if row else None
        self.query("""INSERT INTO public.open_meteo_state(key,document) VALUES(%s,%s)
            ON CONFLICT(key) DO UPDATE SET document=excluded.document,updated_at=now()""", (key, Jsonb(document)))
        self.conn.commit()

    def save(self, task, params, body, variables, requested, received, metadata=None):
        metadata = metadata or {}
        document = json.loads(body)
        rows, units, coverage = parse_hourly(
            document, variables, task.get("lead_days", 0), task["start_date"], task["end_date"]
        )
        request_id = identity([task, params, hashlib.sha256(body).hexdigest()])
        initialised = metadata.get("last_run_initialisation_time")
        available = metadata.get("last_run_availability_time")
        initialised = datetime.fromtimestamp(initialised, UTC) if initialised else None
        available = datetime.fromtimestamp(available, UTC) if available else None
        lead = task.get("lead_days", 0)
        mode = task["mode"]
        self.query("""INSERT INTO public.open_meteo_requests VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (request_id, task.get("task_id"), task["location_id"], task["model"], mode, lead or None,
             requested, received, initialised, available, Jsonb(params), Jsonb(metadata), gzip.compress(body),
             hashlib.sha256(body).hexdigest(), Jsonb(coverage)))
        with self.conn.cursor() as cursor:
            # Explicit destination names retain the isolated test-schema contract.
            import os
            from psycopg import sql

            namespace = os.environ.get("COLLECTOR_SERVICE_SCHEMA", "public")
            statement = sql.SQL("""INSERT INTO {}.open_meteo_hourly VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""").format(sql.Identifier(namespace))
            cursor.executemany(statement, [
                (request_id, task["location_id"], task["model"], mode, lead or None, target,
                 target - timedelta(days=lead) if lead else initialised, received,
                 Jsonb(values), Jsonb(units), Jsonb(missing))
                for target, values, missing in rows
            ])
        if mode == "live_forecast":
            # Keep each received forecast version. Metadata refers to the provider's
            # current run, not a guarantee that every seamless value is from that run.
            for target, values, missing in rows:
                self.query("""INSERT INTO public.observations(source,record_key,kind,scope_id,observed_at,
                    issued_at,available_at,"values",is_demo,collected_at)
                    VALUES(%s,%s,'weather_forecast',%s,%s,NULL,%s,%s,false,%s)
                    ON CONFLICT(source,record_key) DO NOTHING""",
                    ("open_meteo_" + task["model"], identity([request_id, target.isoformat()]),
                     "weatherloc:" + task["location_id"], target, received,
                     Jsonb({"variables": values, "units": units, "missing_variables": missing,
                            "request_id": request_id, "model_initialisation_reference":
                            initialised.isoformat() if initialised else None,
                            "time_contract": "radiation/precipitation preceding hour; temperature instant"}), received))
        if task.get("task_id"):
            counts = coverage["non_null_hours"].values()
            state = "complete" if all(n == len(rows) for n in counts) else "partial" if any(counts) else "unavailable"
            recent = task["end_date"] >= (received + timedelta(hours=9)).date() - timedelta(days=3)
            retry = received + (timedelta(hours=6) if recent else timedelta(days=30 if state == "partial" else 90))
            self.query("""UPDATE public.open_meteo_tasks SET state=%s,request_id=%s,coverage=%s,
                error=NULL,due_at=%s WHERE task_id=%s""",
                (state, request_id, Jsonb(coverage), retry, task["task_id"]))
        self.conn.commit()
        return request_id, coverage

    def close(self):
        self.conn.close()
