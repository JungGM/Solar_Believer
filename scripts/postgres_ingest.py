"""Project received source records into the existing PostgreSQL service tables."""

import csv
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
SERVICE_SOURCES = {
    "koen": "koen_solar",
    "kospo": "kospo_solar",
    "komipo": "komipo_solar",
    "iwest": "iwest_generation",
    "smp": "kpx_smp",
    "forecast": "kma_vilage_forecast",
}


def execute(conn, statement, params=()):
    namespace = os.environ.get("COLLECTOR_SERVICE_SCHEMA", "public")
    if not re.fullmatch(r"[a-z0-9_]+", namespace):
        raise ValueError("Invalid service schema")
    return conn.execute(statement.replace("public.", f'"{namespace}".'), params)


def ensure_service_tables(conn):
    namespace = os.environ.get("COLLECTOR_SERVICE_SCHEMA", "public")
    if namespace != "public":
        from scripts.postgres_store import TEST_SCHEMAS

        prefix = os.environ.get("COLLECTOR_TEST_PREFIX")
        if not prefix or not namespace.startswith(prefix + "_"):
            raise ValueError("Test service schema must be isolated")
        conn.execute('CREATE SCHEMA IF NOT EXISTS "' + namespace + '"')
        TEST_SCHEMAS.add(namespace)
        execute(
            conn,
            """CREATE TABLE IF NOT EXISTS public.observations(
            id BIGSERIAL PRIMARY KEY,source TEXT,record_key TEXT,kind TEXT,scope_id TEXT,
            observed_at TIMESTAMPTZ,issued_at TIMESTAMPTZ,available_at TIMESTAMPTZ,
            "values" JSON,is_demo BOOLEAN,collected_at TIMESTAMPTZ,UNIQUE(source,record_key))""",
        )
    execute(
        conn,
        """CREATE TABLE IF NOT EXISTS public.collector_resource_registry(
        source TEXT NOT NULL,resource TEXT NOT NULL,scope_id TEXT NOT NULL,name TEXT,
        first_delivery_date TEXT,last_delivery_date TEXT,metadata JSONB NOT NULL,
        PRIMARY KEY(source,resource))""",
    )
    execute(
        conn,
        """CREATE TABLE IF NOT EXISTS public.collector_migrations(
        namespace TEXT NOT NULL,object_name TEXT NOT NULL,row_count BIGINT NOT NULL,
        migrated_at TIMESTAMPTZ DEFAULT now(),PRIMARY KEY(namespace,object_name))""",
    )
    execute(
        conn,
        """CREATE TABLE IF NOT EXISTS public.weather_forecast_grids(
        task_id TEXT PRIMARY KEY,issued_at TIMESTAMPTZ NOT NULL,target_at TIMESTAMPTZ NOT NULL,
        variable TEXT NOT NULL,received_at TIMESTAMPTZ NOT NULL,body_gzip BYTEA NOT NULL,
        metadata JSONB NOT NULL)""",
    )


@lru_cache
def koen_metadata():
    path = ROOT / "data/raw/koen_plants_meta.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {(r["ippt"], str(int(r["hogi"]))): r for r in csv.DictReader(stream)}


def identity(source, resource, row):
    known = None
    if source == "koen":
        known = koen_metadata().get((str(row["ippt"]), str(int(row["hogi"]))))
    scope = known["plant_id"] if known else f"{source}:{hashlib.sha256(resource.encode()).hexdigest()[:24]}"
    name = next((str(row[k]) for k in ("ipptNam", "ipptnm", "ncodR", "genNm") if row.get(k)), resource)
    return scope, name, known


def numeric(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def ingest_daily_records(conn, source, records):
    if not records:
        return 0
    ensure_service_tables(conn)
    conn.execute("""CREATE TEMP TABLE IF NOT EXISTS collector_obs_stage(
        source TEXT,record_key TEXT,kind TEXT,scope_id TEXT,observed_at TIMESTAMPTZ,
        issued_at TIMESTAMPTZ,available_at TIMESTAMPTZ,"values" JSONB,is_demo BOOLEAN,
        collected_at TIMESTAMPTZ) ON COMMIT DELETE ROWS""")
    conn.execute("TRUNCATE pg_temp.collector_obs_stage")
    registry = {}
    count = 0
    with conn.cursor().copy("COPY pg_temp.collector_obs_stage FROM STDIN") as copy:
        for day, resource, payload, fetched, request_id in records:
            row = json.loads(payload) if isinstance(payload, str) else payload
            received = datetime.fromisoformat(fetched)
            if received.utcoffset() is None:
                raise ValueError("Received timestamp must include its timezone")
            if source == "forecast":
                issued = datetime.strptime(
                    row["baseDate"] + str(row["baseTime"]).zfill(4), "%Y%m%d%H%M"
                ).replace(tzinfo=KST)
                target = datetime.strptime(
                    row["fcstDate"] + str(row["fcstTime"]).zfill(4), "%Y%m%d%H%M"
                ).replace(tzinfo=KST)
                values = {
                    "category": row["category"],
                    "value_raw": row.get("fcstValue"),
                    "nx": int(row["nx"]),
                    "ny": int(row["ny"]),
                    "grid_mapping_status": "representative_grid",
                }
                copy.write_row(
                    (
                        SERVICE_SOURCES[source],
                        hashlib.sha256((day + ":" + resource).encode()).hexdigest(),
                        "weather_forecast",
                        f"grid:{row['nx']}:{row['ny']}",
                        target,
                        issued,
                        received,
                        json.dumps(values, ensure_ascii=False),
                        False,
                        received,
                    )
                )
                count += 1
                continue
            base = datetime.fromisoformat(day).replace(tzinfo=KST)
            if source == "smp":
                scope = "market:jeju" if "제주" in row["areaName"] else "market:land"
                hours = [int(row["hour"])]
            else:
                scope, name, known = identity(source, resource, row)
                previous = registry.get(resource)
                registry[resource] = (
                    scope,
                    name,
                    min(day, previous[2]) if previous else day,
                    max(day, previous[3]) if previous else day,
                    {
                        "provider_resource_key": resource,
                        "capacity_raw": row.get("qcap"),
                        "unit_status": "not_independently_verified",
                        "provider_metadata": known,
                        "is_solar_by_name": "태양광" in name,
                    },
                )
                hours = range(1, 25)
            for hour in hours:
                if not 1 <= hour <= 24:
                    raise ValueError("Provider hour outside 1..24")
                left = base + timedelta(hours=hour - 1)
                values = {
                    "source_resource": resource,
                    "delivery_date": day,
                    "provider_hour": hour,
                    "interval_start_kst": left.isoformat(),
                    "interval_end_kst": (left + timedelta(hours=1)).isoformat(),
                    "received_source": SERVICE_SOURCES[source],
                }
                if source == "smp":
                    values.update(smp_krw_kwh=numeric(row.get("smp")), unit="KRW/kWh")
                else:
                    prefix = {"koen": "qhorGen", "kospo": "qhorgen", "komipo": "q", "iwest": "q"}[source]
                    values.update(
                        generation_raw=numeric(row.get(f"{prefix}{hour:02d}")),
                        unit_status="not_independently_verified",
                    )
                key = f"{day}:{hashlib.sha256(resource.encode()).hexdigest()[:32]}:{hour:02d}"
                copy.write_row(
                    (
                        SERVICE_SOURCES[source],
                        key,
                        "market" if source == "smp" else "generation",
                        scope,
                        left,
                        None,
                        received,
                        json.dumps(values, ensure_ascii=False),
                        False,
                        received,
                    )
                )
                count += 1
    execute(
        conn,
        """INSERT INTO public.observations(source,record_key,kind,scope_id,observed_at,
        issued_at,available_at,"values",is_demo,collected_at)
        SELECT source,record_key,kind,scope_id,observed_at,issued_at,available_at,"values"::json,
        is_demo,collected_at FROM pg_temp.collector_obs_stage
        ON CONFLICT(source,record_key) DO UPDATE SET
        available_at=excluded.available_at,"values"=excluded."values",collected_at=excluded.collected_at
        WHERE observations."values"::jsonb IS DISTINCT FROM excluded."values"::jsonb""",
    )
    for resource, (scope, name, first, last, metadata) in registry.items():
        execute(
            conn,
            """INSERT INTO public.collector_resource_registry VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(source,resource) DO UPDATE SET name=excluded.name,
            first_delivery_date=least(collector_resource_registry.first_delivery_date,excluded.first_delivery_date),
            last_delivery_date=greatest(collector_resource_registry.last_delivery_date,excluded.last_delivery_date),
            metadata=excluded.metadata""",
            (source, resource, scope, name, first, last, Jsonb(metadata)),
        )
    return count


def ingest_forecast_grid(conn, task_id, params, compressed, metadata):
    ensure_service_tables(conn)
    issued = datetime.strptime(params["tmfc"], "%Y%m%d%H").replace(tzinfo=KST)
    target = datetime.strptime(params["tmef"], "%Y%m%d%H").replace(tzinfo=KST)
    execute(
        conn,
        """INSERT INTO public.weather_forecast_grids VALUES(%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(task_id) DO UPDATE SET received_at=excluded.received_at,
        body_gzip=excluded.body_gzip,metadata=excluded.metadata""",
        (
            task_id,
            issued,
            target,
            params["vars"],
            datetime.fromisoformat(metadata["fetched_at_utc"]),
            compressed,
            Jsonb(metadata),
        ),
    )
