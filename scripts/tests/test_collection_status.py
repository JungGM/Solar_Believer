from scripts.postgres_store import open_store
from datetime import date

from scripts.collection_status import combined_source_status, write_collection_summary


def store(base, source, rows):
    path = base / source / "checkpoint.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_store(path.parent) as conn:
        conn.executemany("INSERT INTO records(day,resource) VALUES(?,?)", rows)


def test_archive_cutoff_does_not_hide_november_and_duplicate_rows_are_not_counted_twice(tmp_path):
    history, output = tmp_path / "history", tmp_path / "ongoing"
    store(history, "koen", [("2026-10-31", "A"), ("2026-10-31", "B")])
    store(output / "live", "koen", [("2026-10-31", "A"), ("2026-11-21", "A")])
    result = combined_source_status(history, output, "koen", date(2026, 10, 31), date(2026, 11, 22))
    assert result["collection_target_end_kst"] == "2026-11-22"
    assert result["latest_stored_date"] == "2026-11-21"
    assert result["stored_records_through_today"] == 3
    assert result["stored_dates_through_today"] == 2
    assert result["target_days"] == 23


def test_announced_next_day_smp_is_separate_from_today_coverage(tmp_path):
    history, output = tmp_path / "history", tmp_path / "ongoing"
    store(output / "live", "smp", [("2026-11-22", "land:1"), ("2026-11-23", "land:1")])
    result = combined_source_status(history, output, "smp", date(2026, 11, 22), date(2026, 11, 22))
    assert result["latest_stored_date"] == "2026-11-23"
    assert result["latest_nonfuture_stored_date"] == "2026-11-22"
    assert result["future_published_records"] == 1
    assert result["date_coverage_percent"] == 100


def test_combined_report_keeps_a_new_resource_without_a_fixed_count_limit(tmp_path):
    history, output = tmp_path / "history", tmp_path / "ongoing"
    store(output / "live", "kospo", [("2026-11-22", str(i)) for i in range(350)])
    report = write_collection_summary(history, output, date(2026, 11, 22), date(2026, 11, 22))
    assert report["sources"]["kospo"]["raw_resource_keys"] == 350
    assert report["resource_count_limit"] is None
    assert (output / "latest_collection_summary.json").exists()
