"""Build model-facing hourly solar values, preserving the source archive."""
import csv
import gzip
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.generation_policy import POLICY_VERSION, is_zero_hour, model_generation


def prepare_file(source_path):
    source_path = Path(source_path)
    target = source_path.with_name("hourly_model_records.csv.gz")
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    counts = {"rows": 0, "night_rows": 0, "positive_values_zeroed": 0, "excluded_non_solar": 0}
    with gzip.open(source_path, "rt", encoding="utf-8-sig", newline="") as inp, gzip.open(tmp, "wt", encoding="utf-8-sig", newline="") as out:
        reader = csv.DictReader(inp)
        writer = csv.DictWriter(out, fieldnames=reader.fieldnames + ["value_for_model", "night_zero_applied", "generation_policy"])
        writer.writeheader()
        for row in reader:
            if row["source"] == "iwest" and "태양광" not in row["resource_name"]:
                counts["excluded_non_solar"] += 1
                continue
            if row["source"] not in ("koen", "kospo", "komipo", "iwest"):
                continue
            timestamp = datetime.fromisoformat(row["interval_start_kst"])
            try:
                value = float(row["value_raw"])
            except (TypeError, ValueError):
                value = None
            zero = is_zero_hour(timestamp) and value is not None
            processed = model_generation(value, timestamp)
            writer.writerow(dict(row, value_for_model="" if processed is None else processed,
                                 night_zero_applied=int(zero), generation_policy=POLICY_VERSION))
            counts["rows"] += 1
            counts["night_rows"] += int(zero)
            counts["positive_values_zeroed"] += int(zero and value > 0)
    tmp.replace(target)
    target.with_suffix(".json").write_text(json.dumps({"policy": POLICY_VERSION, "source": str(source_path.resolve()), **counts}, indent=2), encoding="utf-8")
    return counts


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for folder in (root / "data/api_history_20220101_20261031", root / "data/continuous_collection/live"):
        for source in ("koen", "kospo", "komipo", "iwest"):
            path = folder / source / "hourly_records.csv.gz"
            if path.exists():
                print(json.dumps({"source_file": str(path), **prepare_file(path)}), flush=True)
