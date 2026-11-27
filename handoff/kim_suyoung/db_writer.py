"""
Standalone PostgreSQL writer and envelope validator for handoff/kim_suyoung.
Uses only Python standard library + psycopg (when connecting to DB).
Preserves reverse-chronological file collection and never modifies schema or non-Open-Meteo tables.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
VARIABLES = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "cloud_cover", "cloud_cover_low",
    "cloud_cover_mid", "cloud_cover_high", "wind_speed_10m", "wind_direction_10m", "pressure_msl",
    "precipitation", "snowfall", "snow_depth", "shortwave_radiation", "direct_radiation",
    "diffuse_radiation", "direct_normal_irradiance", "terrestrial_radiation", "sunshine_duration", "is_day",
)
EXPECTED_TARGET = ("100.102.164.7", 15432, "solar_believer")
TEAM_STATE_KEY = "handoff:kim_suyoung:db_ingest"


def identity(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def canonical_task_id(location_id: str, model: str, lead_days: int, start_date: str, end_date: str) -> str:
    """Match scripts/collect_open_meteo.py task_id generation scheme."""
    stream = identity([location_id, model, int(lead_days), list(VARIABLES)])
    return identity([stream, str(start_date), str(end_date)])


def canonical_request_id(
    c_task_id: str,
    location_id: str,
    model: str,
    lead_days: int,
    start_date: str,
    end_date: str,
    params: dict[str, Any],
    body_sha256: str,
    attempts: int = 1,
) -> str:
    """Deterministic request_id matching central Store.save structure."""
    normalized_task = {
        "task_id": c_task_id,
        "location_id": location_id,
        "model": model,
        "lead_days": int(lead_days),
        "start_date": date.fromisoformat(str(start_date)),
        "end_date": date.fromisoformat(str(end_date)),
        "attempts": int(attempts),
        "mode": "historical_fixed_lead",
    }
    return identity([normalized_task, params, body_sha256])


def parse_and_validate_envelope(gz_path: Path) -> dict[str, Any]:
    """
    Read a responses/YYYY-MM/*.json.gz envelope, verify SHA-256, KST/UTC hourly axis,
    variable arrays, units, and NULL counts without modifying or deleting the source file.
    """
    raw_gz = gz_path.read_bytes()
    envelope = json.loads(gzip.decompress(raw_gz).decode("utf-8"))
    if envelope.get("format_version") != 1:
        raise ValueError(f"Unsupported format_version: {envelope.get('format_version')}")

    task = envelope["task"]
    file_task_id = str(envelope["task_id"])
    params = envelope["params"]
    body_b64 = envelope["body_base64"]
    expected_sha = str(envelope["body_sha256"])

    body = base64.b64decode(body_b64)
    actual_sha = hashlib.sha256(body).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(f"SHA-256 mismatch in {gz_path.name}: {actual_sha} != {expected_sha}")

    document = json.loads(body.decode("utf-8"))
    hourly = document.get("hourly", {})
    times = hourly.get("time", [])
    if not isinstance(times, list) or not times or len(times) != len(set(times)):
        raise ValueError("Missing or duplicate hourly timestamps")

    lead_days = int(task["lead_days"])
    start_d = date.fromisoformat(str(task["start_date"]))
    end_d = date.fromisoformat(str(task["end_date"]))
    location_id = str(task["location_id"])
    model = str(task["model"])

    origin = datetime.combine(start_d, datetime.min.time(), UTC) - timedelta(hours=9)
    expected_hours = ((end_d - start_d).days + 1) * 24
    expected_unix = [int((origin + timedelta(hours=i)).timestamp()) for i in range(expected_hours)]
    if times != expected_unix:
        raise ValueError(f"Incomplete or mismatched KST hourly axis ({len(times)}/{expected_hours})")

    suffix = f"_previous_day{lead_days}"
    keys = {name: name + suffix for name in VARIABLES}
    hourly_units = document.get("hourly_units", {})
    units = {name: hourly_units.get(key) for name, key in keys.items()}

    non_null_counts: dict[str, int] = dict.fromkeys(VARIABLES, 0)
    null_counts: dict[str, int] = dict.fromkeys(VARIABLES, 0)
    hourly_rows: list[tuple[datetime, dict[str, Any], list[str]]] = []

    for key in keys.values():
        arr = hourly.get(key)
        if not isinstance(arr, list) or len(arr) != expected_hours:
            raise ValueError(f"Missing or unequal hourly variable array: {key}")

    for i, ts in enumerate(times):
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise ValueError("Expected Unix UTC hourly timestamps")
        target_at = datetime.fromtimestamp(ts, UTC)
        if target_at.minute or target_at.second or target_at.microsecond:
            raise ValueError("Non-hourly timestamp")
        values: dict[str, Any] = {}
        missing: list[str] = []
        for name, key in keys.items():
            val = hourly[key][i]
            if val is not None:
                if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                    raise ValueError(f"Nonfinite or invalid weather value for {name}")
                non_null_counts[name] += 1
            else:
                null_counts[name] += 1
                missing.append(name)
            values[name] = val
        hourly_rows.append((target_at, values, missing))

    if all(c == expected_hours for c in non_null_counts.values()):
        cov_state = "complete"
    elif any(c > 0 for c in non_null_counts.values()):
        cov_state = "partial"
    else:
        cov_state = "unavailable"

    coverage = {
        "expected_hours": expected_hours,
        "non_null_hours": non_null_counts,
        "state": cov_state,
    }

    c_task_id = canonical_task_id(location_id, model, lead_days, str(start_d), str(end_d))
    req_id = canonical_request_id(
        c_task_id, location_id, model, lead_days, str(start_d), str(end_d), params, actual_sha
    )

    requested_at = datetime.fromisoformat(str(envelope["requested_at_utc"])).astimezone(UTC)
    received_at = datetime.fromisoformat(str(envelope["received_at_utc"])).astimezone(UTC)

    return {
        "file_path": str(gz_path),
        "file_task_id": file_task_id,
        "canonical_task_id": c_task_id,
        "request_id": req_id,
        "location_id": location_id,
        "location": task.get("location") or {},
        "model": model,
        "mode": "historical_fixed_lead",
        "lead_days": lead_days,
        "start_date": start_d,
        "end_date": end_d,
        "requested_at": requested_at,
        "received_at": received_at,
        "params": params,
        "body_bytes": body,
        "body_gzip": gzip.compress(body, mtime=0),
        "sha256": actual_sha,
        "coverage": coverage,
        "null_counts": null_counts,
        "units": units,
        "hourly_rows": hourly_rows,
    }


def load_db_target(config_path: Path | None = None) -> dict[str, Any]:
    cfg_file = config_path
    if cfg_file is None:
        local_cfg = ROOT / "db_connection.local.json"
        cfg_file = local_cfg if local_cfg.exists() else (ROOT / "db_connection.example.json")
    config = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
    target = {
        "host": str(config["host"]).strip(),
        "port": int(config["port"]),
        "dbname": str(config["database"]).strip(),
        "user": str(config["user"]).strip(),
    }
    if (target["host"], target["port"], target["dbname"]) != EXPECTED_TARGET or not target["user"]:
        raise ValueError("Invalid config or unexpected main DB target.")
    password = os.environ.get("POSTGRES_PASSWORD", "") or str(config.get("password", ""))
    return {**target, "password": password}


class MainDBWriter:
    """
    Safe transactional writer to main PostgreSQL (100.102.164.7:15432 / solar_believer).
    Never runs schema DDL, migrations, or table drops.
    """

    def __init__(self, target: dict[str, Any], conn: Any = None):
        self.target = target
        self._conn = conn
        self._seen_locations: set[str] = set()

    def connect(self) -> Any:
        if self._conn is not None and not getattr(self._conn, "closed", False):
            return self._conn
        import psycopg

        if not self.target.get("password"):
            raise RuntimeError("DB password is not set (use POSTGRES_PASSWORD or db_connection.local.json)")
        self._conn = psycopg.connect(
            host=self.target["host"],
            port=self.target["port"],
            dbname=self.target["dbname"],
            user=self.target["user"],
            password=self.target["password"],
            connect_timeout=10,
            application_name="kim-suyoung-meteo-collector",
        )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def check_shared_quota(self, cost: float, now_utc: datetime, daily_budget: int = 8500) -> float:
        """Check shared public.open_meteo_calls table so two PCs do not exceed quota."""
        conn = self.connect()
        limits = [(60, 400), (3600, 4000), (86400, daily_budget)]
        wait_sec = 0.0
        with conn.cursor() as cur:
            for seconds, limit in limits:
                since = now_utc - timedelta(seconds=seconds)
                cur.execute(
                    "SELECT at, cost FROM public.open_meteo_calls WHERE at > %s ORDER BY at ASC",
                    (since,),
                )
                rows = cur.fetchall()
                total = sum(float(r[1]) for r in rows)
                if total + cost > limit and rows:
                    release = rows[0][0] + timedelta(seconds=seconds, microseconds=1)
                    wait_sec = max(wait_sec, (release - now_utc).total_seconds())
        conn.rollback()
        return max(0.0, wait_sec)

    def is_task_already_done_in_db(self, parsed: dict[str, Any]) -> str | None:
        """Check if main DB already has an identical completed/partial request for this task slice."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id FROM public.open_meteo_requests
                WHERE location_id = %s AND model = %s AND mode = %s AND lead_days = %s
                  AND (sha256 = %s OR (params->>'start_date' = %s AND params->>'end_date' = %s))
                LIMIT 1
                """,
                (
                    parsed["location_id"],
                    parsed["model"],
                    parsed["mode"],
                    parsed["lead_days"],
                    parsed["sha256"],
                    str(parsed["start_date"]),
                    str(parsed["end_date"]),
                ),
            )
            row = cur.fetchone()
        conn.rollback()
        return str(row[0]) if row else None

    def claim_task_and_reserve_quota(
        self, task: dict[str, Any], cost: float, now_utc: datetime, daily_budget: int = 8500
    ) -> tuple[str, float, int | None]:
        """
        Coordinate with central collector via public.open_meteo_tasks and public.open_meteo_calls:
        - Returns ('already_done', 0.0, None) if task is already complete/partial/unavailable in DB.
        - Returns ('quota_wait', wait_sec, None) if shared 24h/1h/1m budget is full.
        - Otherwise reserves call in public.open_meteo_calls and sets 5-min lease on open_meteo_tasks.
        """
        c_tid = canonical_task_id(
            str(task["location_id"]),
            str(task["model"]),
            int(task["lead_days"]),
            str(task["start_date"]),
            str(task["end_date"]),
        )
        conn = self.connect()
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT state, request_id, attempts, due_at FROM public.open_meteo_tasks WHERE task_id = %s",
                    (c_tid,),
                )
                row = cur.fetchone()
                if row and row[0] in ("complete", "partial", "unavailable") and row[1]:
                    return ("already_done", 0.0, None)

                # Also check open_meteo_requests directly by slice
                cur.execute(
                    """
                    SELECT request_id FROM public.open_meteo_requests
                    WHERE location_id = %s AND model = %s AND mode = 'historical_fixed_lead'
                      AND lead_days = %s AND params->>'start_date' = %s AND params->>'end_date' = %s
                    LIMIT 1
                    """,
                    (
                        str(task["location_id"]),
                        str(task["model"]),
                        int(task["lead_days"]),
                        str(task["start_date"]),
                        str(task["end_date"]),
                    ),
                )
                if cur.fetchone():
                    return ("already_done", 0.0, None)

                # Check shared call budget
                limits = [(60, 400), (3600, 4000), (86400, daily_budget)]
                wait_sec = 0.0
                for seconds, limit in limits:
                    since = now_utc - timedelta(seconds=seconds)
                    cur.execute(
                        "SELECT at, cost FROM public.open_meteo_calls WHERE at > %s ORDER BY at ASC",
                        (since,),
                    )
                    calls = cur.fetchall()
                    total = sum(float(r[1]) for r in calls)
                    if total + cost > limit and calls:
                        release = calls[0][0] + timedelta(seconds=seconds, microseconds=1)
                        wait_sec = max(wait_sec, (release - now_utc).total_seconds())
                if wait_sec > 0:
                    return ("quota_wait", max(1.0, wait_sec), None)

                cur.execute(
                    """
                    UPDATE public.open_meteo_tasks
                    SET attempts = attempts + 1, due_at = %s
                    WHERE task_id = %s
                    """,
                    (now_utc + timedelta(minutes=5), c_tid),
                )
                cur.execute(
                    """
                    INSERT INTO public.open_meteo_calls(at, cost, mode, status)
                    VALUES (%s, %s, 'historical_fixed_lead', 'reserved')
                    RETURNING id
                    """,
                    (now_utc, float(cost)),
                )
                call_id = int(cur.fetchone()[0])
                return ("reserved", 0.0, call_id)

    def ingest_parsed_envelope(
        self,
        parsed: dict[str, Any],
        record_api_call: bool = False,
        call_cost: float = 0.0,
        update_state: bool = True,
    ) -> dict[str, Any]:
        """
        Atomically write location, task, request, and hourly values in a single DB transaction.
        Returns {'status': 'inserted' | 'duplicate', 'request_id': ..., 'hourly_inserted': int}.
        """
        from psycopg.types.json import Jsonb

        conn = self.connect()
        loc = parsed["location"]
        c_task_id = parsed["canonical_task_id"]
        req_id = parsed["request_id"]
        cov = parsed["coverage"]
        cov_state = cov["state"]

        with conn.transaction():
            with conn.cursor() as cur:
                # 1. Upsert location metadata once per distinct location_id
                if loc and "latitude" in loc and "longitude" in loc and parsed["location_id"] not in self._seen_locations:
                    cur.execute(
                        """
                        INSERT INTO public.open_meteo_locations(location_id, latitude, longitude, members, updated_at)
                        VALUES (%s, %s, %s, %s, now())
                        ON CONFLICT (location_id) DO UPDATE
                        SET members = excluded.members, updated_at = now()
                        """,
                        (
                            parsed["location_id"],
                            float(loc["latitude"]),
                            float(loc["longitude"]),
                            Jsonb(loc.get("members", [])),
                        ),
                    )
                    self._seen_locations.add(parsed["location_id"])

                # 2. Check if identical request already exists in public.open_meteo_requests
                cur.execute(
                    """
                    SELECT request_id FROM public.open_meteo_requests
                    WHERE request_id = %s
                       OR (location_id = %s AND model = %s AND mode = %s AND lead_days = %s
                           AND sha256 = %s
                           AND params->>'start_date' = %s AND params->>'end_date' = %s)
                    LIMIT 1
                    """,
                    (
                        req_id,
                        parsed["location_id"],
                        parsed["model"],
                        parsed["mode"],
                        parsed["lead_days"],
                        parsed["sha256"],
                        str(parsed["start_date"]),
                        str(parsed["end_date"]),
                    ),
                )
                existing_req = cur.fetchone()
                is_duplicate = existing_req is not None
                effective_req_id = str(existing_req[0]) if existing_req else req_id

                # 3. Upsert public.open_meteo_tasks without downgrading 'complete'
                retry_due = parsed["received_at"] + timedelta(days=30 if cov_state == "partial" else 90)
                cur.execute(
                    """
                    INSERT INTO public.open_meteo_tasks(
                        task_id, location_id, model, lead_days, start_date, end_date,
                        state, attempts, due_at, request_id, error, coverage
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, NULL, %s)
                    ON CONFLICT (task_id) DO UPDATE
                    SET state = CASE
                            WHEN public.open_meteo_tasks.state = 'complete' THEN 'complete'
                            ELSE excluded.state
                        END,
                        request_id = COALESCE(public.open_meteo_tasks.request_id, excluded.request_id),
                        coverage = CASE
                            WHEN public.open_meteo_tasks.state = 'complete' THEN public.open_meteo_tasks.coverage
                            ELSE excluded.coverage
                        END,
                        error = NULL
                    """,
                    (
                        c_task_id,
                        parsed["location_id"],
                        parsed["model"],
                        parsed["lead_days"],
                        parsed["start_date"],
                        parsed["end_date"],
                        cov_state,
                        retry_due,
                        effective_req_id,
                        Jsonb({"expected_hours": cov["expected_hours"], "non_null_hours": cov["non_null_hours"]}),
                    ),
                )

                # 4. Insert request row if not already present
                metadata_doc = {
                    "handoff_source": "kim_suyoung",
                    "file_task_id": parsed["file_task_id"],
                    "canonical_task_id": c_task_id,
                }
                if not is_duplicate:
                    cur.execute(
                        """
                        INSERT INTO public.open_meteo_requests(
                            request_id, task_id, location_id, model, mode, lead_days,
                            requested_at, received_at, model_initialised_at, provider_available_at,
                            params, metadata, body_gzip, sha256, coverage
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s)
                        ON CONFLICT (request_id) DO NOTHING
                        """,
                        (
                            effective_req_id,
                            c_task_id,
                            parsed["location_id"],
                            parsed["model"],
                            parsed["mode"],
                            parsed["lead_days"],
                            parsed["requested_at"],
                            parsed["received_at"],
                            Jsonb(parsed["params"]),
                            Jsonb(metadata_doc),
                            parsed["body_gzip"],
                            parsed["sha256"],
                            Jsonb({"expected_hours": cov["expected_hours"], "non_null_hours": cov["non_null_hours"]}),
                        ),
                    )

                # 5. Insert hourly rows via server-side unnest (10 bind params instead of 8,184)
                hourly_rows = parsed["hourly_rows"]
                if hourly_rows:
                    target_ats = [r[0] for r in hourly_rows]
                    vars_json = [json.dumps(r[1], ensure_ascii=False) for r in hourly_rows]
                    missing_json = [json.dumps(r[2], ensure_ascii=False) for r in hourly_rows]
                    cur.execute(
                        """
                        INSERT INTO public.open_meteo_hourly(
                            request_id, location_id, model, mode, lead_days,
                            target_at, reference_at, received_at, variables, units, missing_variables
                        )
                        SELECT
                            %s, %s, %s, %s, %s,
                            t.target_at,
                            t.target_at - make_interval(days => %s),
                            %s,
                            t.vars::jsonb,
                            %s,
                            t.missing::jsonb
                        FROM unnest(%s::timestamptz[], %s::text[], %s::text[]) AS t(target_at, vars, missing)
                        ON CONFLICT (request_id, target_at) DO NOTHING
                        """,
                        (
                            effective_req_id,
                            parsed["location_id"],
                            parsed["model"],
                            parsed["mode"],
                            parsed["lead_days"],
                            int(parsed["lead_days"]),
                            parsed["received_at"],
                            Jsonb(parsed["units"]),
                            target_ats,
                            vars_json,
                            missing_json,
                        ),
                    )
                    hourly_inserted = max(0, int(cur.rowcount or 0))
                else:
                    hourly_inserted = 0

                # 6. Record actual live API call only when requested (never for historical file migration)
                if record_api_call and call_cost > 0:
                    cur.execute(
                        """
                        INSERT INTO public.open_meteo_calls(at, cost, mode, status)
                        VALUES (%s, %s, %s, 'received')
                        """,
                        (parsed["requested_at"], float(call_cost), parsed["mode"]),
                    )

                # 7. Update isolated team checkpoint in public.open_meteo_state
                if update_state:
                    cur.execute(
                        """
                        INSERT INTO public.open_meteo_state(key, document, updated_at)
                        VALUES (%s, %s, now())
                        ON CONFLICT (key) DO UPDATE
                        SET document = excluded.document, updated_at = now()
                        """,
                        (
                            TEAM_STATE_KEY,
                            Jsonb({
                                "last_request_id": effective_req_id,
                                "last_file_task_id": parsed["file_task_id"],
                                "last_canonical_task_id": c_task_id,
                                "last_start_date": str(parsed["start_date"]),
                                "last_end_date": str(parsed["end_date"]),
                                "updated_at_utc": datetime.now(UTC).isoformat(),
                            }),
                        ),
                    )

        return {
            "status": "duplicate" if (is_duplicate and hourly_inserted == 0) else "inserted",
            "request_id": effective_req_id,
            "canonical_task_id": c_task_id,
            "file_task_id": parsed["file_task_id"],
            "hourly_inserted": hourly_inserted,
        }
