"""Verify raw/service counts in one PostgreSQL snapshot, without local DB reads."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql

from scripts.postgres_store import connect_postgres, schema_name
from scripts.postgres_ingest import SERVICE_SOURCES

ROOT = Path(__file__).resolve().parents[1]


def verify():
    report = dict(
        checked_at_utc=datetime.now(timezone.utc).isoformat(), database="PostgreSQL", sources={}, passed=True
    )
    with connect_postgres() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for source, service in SERVICE_SOURCES.items():
            history = schema_name(ROOT / "data/api_history_20220101_20261031" / source)
            recent = schema_name(ROOT / "data/continuous_collection/live" / source)
            union = sql.SQL(
                "SELECT day,resource FROM {}.records UNION SELECT day,resource FROM {}.records"
            ).format(sql.Identifier(history), sql.Identifier(recent))
            raw = conn.execute(
                sql.SQL("SELECT count(*),min(day),max(day) FROM ({}) t").format(union)
            ).fetchone()
            actual = conn.execute(
                "SELECT count(*) FROM public.observations WHERE source=%s", (service,)
            ).fetchone()[0]
            expected = raw[0] * (1 if source in ("smp", "forecast") else 24)
            item = dict(
                raw_records=raw[0],
                first_day=raw[1],
                last_day=raw[2],
                expected_service_rows=expected,
                actual_service_rows=actual,
                passed=expected == actual,
            )
            report["sources"][source] = item
            report["passed"] &= item["passed"]
        datasets = {
            name: count
            for name, count in conn.execute(
                "SELECT dataset,count(*) FROM public.source_dataset_rows GROUP BY dataset"
            )
        }
        expected = {
            name: count
            for name, count in conn.execute(
                "SELECT object_name,row_count FROM public.collector_migrations WHERE namespace='source_datasets'"
            )
        }
        report.update(
            source_datasets=datasets,
            source_dataset_total=sum(datasets.values()),
            source_datasets_match=datasets == expected,
            weather_forecast_grids=conn.execute(
                "SELECT count(*) FROM public.weather_forecast_grids"
            ).fetchone()[0],
            weather_grid_payloads_present=conn.execute(
                "SELECT count(*) FROM public.weather_forecast_grids WHERE octet_length(body_gzip)>0"
            ).fetchone()[0],
            registered_source_resource_keys=conn.execute(
                "SELECT count(*) FROM public.collector_resource_registry"
            ).fetchone()[0],
            runtime_state_files=conn.execute(
                "SELECT count(*) FROM public.collector_runtime_state"
            ).fetchone()[0],
        )
        report["passed"] &= (
            report["source_datasets_match"]
            and report["weather_forecast_grids"] == report["weather_grid_payloads_present"]
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reports/postgres_ingestion_20261122.json")
    args = parser.parse_args()
    report = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
