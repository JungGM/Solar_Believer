"""Verify downloaded HTTP bodies, checkpoint records and exported row counts."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.postgres_store import open_store
from scripts.collect_public_history import (
    ENDPOINTS, KOMIPO_GUIDE_URL, KOMIPO_RESOURCES, ROOT, clean_day, dump,
    now, parse_response, record_identity,
)


def audit_source(root, source, start, end):
    directory = root / source
    report = json.loads((directory / "coverage.json").read_text(encoding="utf-8"))
    conn = open_store(directory)
    expected = set()
    raw_rows, verified = 0, 0
    page_groups = defaultdict(lambda: {"returned_rows": 0, "advertised_total": None, "pages": []})
    for rid, status, path, metadata in conn.execute("SELECT * FROM requests"):
        meta = json.loads(metadata)
        with gzip.open(directory / path, "rb") as stream:
            raw = stream.read()
        assert hashlib.sha256(raw).hexdigest() == meta["sha256"], (source, "checksum", path)
        verified += 1
        if status != "ok":
            continue
        rows, total, _ = parse_response(raw)
        params = meta["params"]
        grouping = json.dumps({k: v for k, v in params.items() if k not in {"page", "pageNo", "size", "numOfRows"}}, sort_keys=True)
        group = page_groups[grouping]
        group["returned_rows"] += len(rows)
        group["advertised_total"] = total
        group["pages"].append(params.get("pageNo", params.get("page", 1)))
        raw_rows += len(rows)
        if source == "forecast":
            continue
        for row in rows:
            day, resource, _ = record_identity(source, row)
            if start <= day <= end:
                expected.add((day.isoformat(), resource))
    actual = set(conn.execute("SELECT day,resource FROM records"))
    assert expected == actual, (source, "checkpoint differs from retained responses", len(expected), len(actual))
    assert len(actual) == report["stored_records"], (source, "report count")
    conn.close()
    with (directory / "daily_records.csv").open(encoding="utf-8-sig", newline="") as stream:
        daily_count = sum(1 for _ in csv.DictReader(stream))
    assert daily_count == len(actual), (source, "daily export count")
    monthly = Counter(day[:7] for day, _ in actual)
    hourly_count = missing = negative = non_numeric = 0
    if source != "forecast":
        with gzip.open(directory / "hourly_records.csv.gz", "rt", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                hourly_count += 1
                assert start <= clean_day(row["delivery_date"]) <= end
                assert 1 <= int(row["provider_hour"]) <= 24
                if row["is_missing"] == "1":
                    missing += 1
                    continue
                try:
                    value = float(row["value_raw"].replace(",", ""))
                    if not math.isfinite(value):
                        non_numeric += 1
                    negative += value < 0
                except ValueError:
                    non_numeric += 1
        assert hourly_count == report["hourly_rows"], (source, "hourly report count")
        assert hourly_count == len(actual) * (1 if source == "smp" else 24)
        assert missing == report["missing_hour_values"]
    resources = report.get("resources", [])
    if resources:
        fields = sorted({key for row in resources for key in row})
        with (directory / "resource_roster.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(resources)
    incomplete = [dict(request=json.loads(key), **value) for key, value in page_groups.items()
                  if value["advertised_total"] is not None and value["returned_rows"] != value["advertised_total"]]
    audit = {"source": source, "status": report["status"], "error": report["error"],
             "verified_response_files": verified, "raw_response_rows_including_outside_period": raw_rows,
             "stored_records": len(actual), "hourly_rows": hourly_count,
             "resource_key_count": report["resource_count"],
             "first_day": min((d for d, _ in actual), default=None),
             "last_day": max((d for d, _ in actual), default=None),
             "monthly_records": dict(sorted(monthly.items())),
             "missing_hour_values": missing, "negative_values_retained": negative,
             "non_finite_or_non_numeric_values": non_numeric,
             "incomplete_request_groups": incomplete, "verified_at_utc": now(),
             "identity_note": "IWest keys use provider name+capacity; SMP keys are zone+hour. These counts are not verified physical generator counts."}
    dump(directory / "integrity_audit.json", audit)
    return audit, report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/api_history_20220101_20261031")
    args = parser.parse_args()
    root = args.output
    config_path = root / "run_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    start, end = date.fromisoformat(config["start"]), date.fromisoformat(config["end"])
    audits, reports = {}, {}
    for source in ENDPOINTS:
        audits[source], reports[source] = audit_source(root, source, start, end)
        print(json.dumps({k: audits[source][k] for k in ("source", "status", "stored_records", "hourly_rows", "first_day", "last_day", "resource_key_count")}, ensure_ascii=False), flush=True)
    config.update(sources=list(ENDPOINTS), endpoints=ENDPOINTS,
                  komipo_resource_scope=KOMIPO_RESOURCES, komipo_roster_basis=KOMIPO_GUIDE_URL)
    dump(config_path, config)
    dump(root / "collection_report.json", {"requested_start": str(start), "requested_end": str(end), "sources": reports, "updated_at_utc": now()})
    dump(root / "integrity_audit.json", {"passed": True, "sources": audits, "verified_at_utc": now()})
    with (root / "collection_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["source", "status", "first_day", "last_day", "resource_key_count", "stored_records", "hourly_rows", "missing_hour_values", "negative_values_retained", "non_finite_or_non_numeric_values"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: result[k] for k in fields} for result in audits.values())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
