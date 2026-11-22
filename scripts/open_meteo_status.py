"""Read actual Open-Meteo PostgreSQL progress and variable coverage."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.collect_open_meteo import MODELS, VARIABLES, call_cost
from scripts.postgres_store import connect_postgres


def snapshot():
    with connect_postgres() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        locations, members = conn.execute("SELECT count(*),coalesce(sum(jsonb_array_length(members)),0) FROM public.open_meteo_locations").fetchone()
        tasks = conn.execute("SELECT state,count(*) FROM public.open_meteo_tasks GROUP BY state").fetchall()
        requests = conn.execute("""SELECT mode,model,lead_days,count(*),sum((coverage->>'expected_hours')::bigint),
            min(params->>'start_date'),max(params->>'end_date')
            FROM public.open_meteo_requests GROUP BY mode,model,lead_days ORDER BY mode,model,lead_days""").fetchall()
        series = []
        for mode, model, lead, count, hours, first, last in requests:
            variables = {key: int(value) for key, value in conn.execute("""SELECT v.key,sum(v.value::text::bigint)
                FROM public.open_meteo_requests r,
                LATERAL jsonb_each(r.coverage->'non_null_hours') v
                WHERE mode=%s AND model=%s AND coalesce(lead_days,0)=%s GROUP BY v.key""", (mode, model, lead or 0)).fetchall()}
            series.append({"mode": mode, "model": model, "lead_days": lead, "received_responses": count,
                           "hourly_snapshot_rows": int(hours), "first_requested_date_kst": first,
                           "last_requested_date_kst": last, "non_null_variable_hours": variables})
        pending = conn.execute("SELECT start_date,end_date FROM public.open_meteo_tasks WHERE state IN ('pending','error')").fetchall()
        row = conn.execute("SELECT document FROM public.open_meteo_state WHERE key='status'").fetchone()
        hourly = conn.execute("SELECT count(*) FROM public.open_meteo_hourly").fetchone()[0]
        raw = conn.execute("SELECT count(*),coalesce(sum(octet_length(body_gzip)),0) FROM public.open_meteo_requests").fetchone()
        usage = conn.execute("SELECT coalesce(sum(cost),0) FROM public.open_meteo_calls WHERE at>now()-interval '24 hours'").fetchone()[0]
        provider_meta = conn.execute("SELECT document FROM public.open_meteo_state WHERE key='provider_metadata'").fetchone()
        registry = conn.execute("SELECT count(*) FROM public.collector_resource_registry WHERE source!='forecast'").fetchone()[0]
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "database": "PostgreSQL",
            "priority": "historical_training_forecasts_first", "registered_coordinate_locations": locations,
            "coordinate_resource_members": members, "other_registered_source_keys_not_mapped_here": max(0, registry - members),
            "models": list(MODELS), "requested_variables": list(VARIABLES), "task_counts": dict(tasks),
            "received_http_responses": raw[0], "compressed_response_bytes": raw[1],
            "hourly_snapshot_rows": hourly, "series": series, "weighted_calls_last_24h": usage,
            "pending_weighted_cost_conservative": sum(call_cost(a, b) for a, b in pending),
            "collector_status": row[0] if row else None, "provider_metadata": provider_meta[0] if provider_meta else None,
            "interpretation": "Snapshot rows are location/model/lead/time combinations. Non-null coverage is per variable. NULL and unavailable original forecasts are never filled with observed weather or zeros."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = snapshot()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
