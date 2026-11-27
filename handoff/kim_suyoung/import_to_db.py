"""
Validate and migrate existing responses/YYYY-MM/*.json.gz into main PostgreSQL without API re-calls.
Supports --validate-only (offline inspection & summary report), small-batch verification (--limit),
idempotent re-runs, parallel worker connections (--workers), and checkpoint tracking.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any

from db_writer import MainDBWriter, VARIABLES, load_db_target, parse_and_validate_envelope

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc


def atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def resolve_source_dir(explicit_source: Path | None) -> Path:
    if explicit_source is not None:
        return explicit_source.resolve()
    local_cfg = ROOT / "local_config.json"
    if local_cfg.exists():
        doc = json.loads(local_cfg.read_text(encoding="utf-8-sig"))
        if doc.get("output"):
            return Path(doc["output"]).resolve()
    raise ValueError("Specify --source or configure local_config.json")


def run_migration(
    source_dir: Path,
    validate_only: bool = False,
    config_path: Path | None = None,
    limit: int = 0,
    ignore_checkpoint: bool = False,
    report_path: Path | None = None,
    writer: MainDBWriter | None = None,
    workers: int = 8,
) -> dict[str, Any]:
    responses_root = source_dir / "responses"
    snapshot_files = sorted(responses_root.rglob("*.json.gz")) if responses_root.exists() else []
    if limit > 0:
        snapshot_files = snapshot_files[:limit]

    checkpoint_path = source_dir / "db_ingest_checkpoint.json"
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else {"ingested": {}, "failed": {}}
    )

    total_files = len(snapshot_files)
    valid_count = 0
    invalid_count = 0
    inserted_requests = 0
    duplicate_requests = 0
    db_failed_requests = 0
    inserted_hourly_rows = 0

    by_model_lead: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "expected_hours": 0, "complete": 0, "partial": 0, "unavailable": 0}
    )
    by_month: dict[str, int] = defaultdict(int)
    variable_nulls: dict[str, int] = dict.fromkeys(VARIABLES, 0)
    variable_non_nulls: dict[str, int] = dict.fromkeys(VARIABLES, 0)
    locations_seen: set[str] = set()
    validation_failures: list[dict[str, str]] = []

    target: dict[str, Any] = {}
    if not validate_only and writer is None:
        target = load_db_target(config_path)
        if not target.get("password"):
            if sys.stdin.isatty():
                target["password"] = getpass.getpass("DB password (hidden; never saved): ")
            else:
                raise RuntimeError("DB password required for live import (set POSTGRES_PASSWORD).")

    thread_local = threading.local()
    thread_writers: list[MainDBWriter] = []
    lock = threading.Lock()

    def get_thread_writer() -> MainDBWriter:
        if writer is not None:
            return writer
        w = getattr(thread_local, "writer", None)
        if w is None:
            w = MainDBWriter(target)
            thread_local.writer = w
            with lock:
                thread_writers.append(w)
        return w

    def process_one(idx: int, gz_path: Path) -> dict[str, Any]:
        rel_name = str(gz_path.relative_to(source_dir)).replace("\\", "/")
        try:
            parsed = parse_and_validate_envelope(gz_path)
        except Exception as exc:
            return {"status": "invalid", "rel_name": rel_name, "error": type(exc).__name__}

        if validate_only:
            return {"status": "validated", "rel_name": rel_name, "parsed": parsed}

        ft_id = parsed["file_task_id"]
        with lock:
            prev_cp = checkpoint["ingested"].get(ft_id)
        if not ignore_checkpoint and prev_cp and prev_cp.get("sha256") == parsed["sha256"]:
            return {"status": "checkpoint_dup", "rel_name": rel_name, "parsed": parsed}

        w = get_thread_writer()
        try:
            should_update_state = (idx == total_files - 1) or (idx % 100 == 0)
            try:
                res = w.ingest_parsed_envelope(
                    parsed, record_api_call=False, update_state=should_update_state
                )
            except TypeError:
                res = w.ingest_parsed_envelope(parsed, record_api_call=False)
            return {"status": "db_ok", "rel_name": rel_name, "parsed": parsed, "res": res}
        except Exception as exc:
            w.close()
            return {"status": "db_err", "rel_name": rel_name, "parsed": parsed, "error": type(exc).__name__}

    eff_workers = 1 if (writer is not None or validate_only or total_files <= 10) else max(1, workers)
    processed_so_far = 0

    try:
        with ThreadPoolExecutor(max_workers=eff_workers) as pool:
            futures = [pool.submit(process_one, idx, gz_path) for idx, gz_path in enumerate(snapshot_files)]
            for fut in as_completed(futures):
                out = fut.result()
                processed_so_far += 1
                st = out["status"]
                rel_name = out["rel_name"]
                if st == "invalid":
                    invalid_count += 1
                    validation_failures.append({"file": rel_name, "error": out["error"]})
                    checkpoint["failed"][rel_name] = {
                        "stage": "validation",
                        "error": out["error"],
                        "at_utc": datetime.now(UTC).isoformat(),
                    }
                    continue

                valid_count += 1
                parsed = out["parsed"]
                ml_key = f"{parsed['model']}:lead_{parsed['lead_days']}d"
                cov = parsed["coverage"]
                by_model_lead[ml_key]["files"] += 1
                by_model_lead[ml_key]["expected_hours"] += int(cov["expected_hours"])
                by_model_lead[ml_key][cov["state"]] += 1
                by_month[str(parsed["start_date"])[:7]] += 1
                locations_seen.add(parsed["location_id"])

                for v_name in VARIABLES:
                    variable_non_nulls[v_name] += int(cov["non_null_hours"][v_name])
                    variable_nulls[v_name] += int(parsed["null_counts"][v_name])

                if st == "validated":
                    continue
                elif st == "checkpoint_dup":
                    duplicate_requests += 1
                elif st == "db_ok":
                    res = out["res"]
                    if res["status"] == "inserted":
                        inserted_requests += 1
                    else:
                        duplicate_requests += 1
                    inserted_hourly_rows += int(res["hourly_inserted"])
                    ft_id = parsed["file_task_id"]
                    checkpoint["ingested"][ft_id] = {
                        "file": rel_name,
                        "file_task_id": ft_id,
                        "canonical_task_id": res["canonical_task_id"],
                        "request_id": res["request_id"],
                        "sha256": parsed["sha256"],
                        "state": cov["state"],
                        "imported_at_utc": datetime.now(UTC).isoformat(),
                    }
                    checkpoint["failed"].pop(rel_name, None)
                elif st == "db_err":
                    db_failed_requests += 1
                    checkpoint["failed"][rel_name] = {
                        "stage": "db_ingest",
                        "error": out["error"],
                        "at_utc": datetime.now(UTC).isoformat(),
                    }

                if not validate_only and processed_so_far % 100 == 0:
                    atomic_write_json(checkpoint_path, checkpoint)
                    print(
                        f"[progress] {processed_so_far}/{total_files} files "
                        f"(inserted={inserted_requests}, dup={duplicate_requests}, err={db_failed_requests})",
                        file=sys.stderr,
                        flush=True,
                    )
    finally:
        if not validate_only:
            atomic_write_json(checkpoint_path, checkpoint)
        for w in thread_writers:
            w.close()

    summary = {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "validate_only" if validate_only else "db_import",
        "snapshot_files": total_files,
        "validation_passed": valid_count,
        "validation_failed": invalid_count,
        "db_inserted_requests": inserted_requests,
        "db_duplicate_requests": duplicate_requests,
        "db_failed_requests": db_failed_requests,
        "db_inserted_hourly_rows": inserted_hourly_rows,
        "distinct_locations": len(locations_seen),
        "months_covered": dict(sorted(by_month.items(), reverse=True)),
        "by_model_and_lead": dict(sorted(by_model_lead.items())),
        "variable_non_null_hours": variable_non_nulls,
        "variable_null_hours": variable_nulls,
        "validation_failures": validation_failures[:20],
    }
    if report_path is not None:
        atomic_write_json(report_path, summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, help="Collector output folder (defaults to local_config.json)")
    p.add_argument("--config", type=Path, help="DB connection JSON path")
    p.add_argument("--validate-only", action="store_true", help="Validate files without connecting to DB")
    p.add_argument("--limit", type=int, default=0, help="Process at most N files (for small-batch verification)")
    p.add_argument("--workers", type=int, default=8, help="Number of parallel DB worker threads")
    p.add_argument("--ignore-checkpoint", action="store_true", help="Re-verify idempotency against DB directly")
    p.add_argument("--report", type=Path, help="Write JSON summary report to this path")
    args = p.parse_args()

    src = resolve_source_dir(args.source)
    summary = run_migration(
        source_dir=src,
        validate_only=args.validate_only,
        config_path=args.config,
        limit=args.limit,
        ignore_checkpoint=args.ignore_checkpoint,
        report_path=args.report,
        workers=args.workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (summary["validation_failed"] == 0 and summary["db_failed_requests"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
