import csv
import gzip
from datetime import datetime, timezone

import pytest

from backend.app.generation_policy import KST, model_generation
from scripts.prepare_generation import prepare_file


@pytest.mark.parametrize("hour", range(24))
def test_actual_and_prediction_share_exact_boundary_hours(hour):
    target = datetime(2026, 6, 1, hour, tzinfo=KST)
    expected = 0 if hour <= 5 or hour >= 22 else 12
    assert model_generation(12, target) == expected
    assert model_generation(12, target.astimezone(timezone.utc)) == expected
    assert model_generation(None, target) is None


def test_prepared_values_preserve_raw_and_daytime_missing(tmp_path):
    source = tmp_path / "hourly_records.csv.gz"
    with gzip.open(source, "wt", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source", "resource_name", "interval_start_kst", "value_raw"])
        writer.writeheader()
        for hour, value in [(5, "7"), (6, "8"), (12, ""), (22, "9")]:
            writer.writerow(dict(source="koen", resource_name="solar", interval_start_kst=f"2026-06-01T{hour:02}:00:00+09:00", value_raw=value))
    assert prepare_file(source)["positive_values_zeroed"] == 2
    with gzip.open(tmp_path / "hourly_model_records.csv.gz", "rt", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert [r["value_raw"] for r in rows] == ["7", "8", "", "9"]
    assert [r["value_for_model"] for r in rows] == ["0.0", "8.0", "", "0.0"]
