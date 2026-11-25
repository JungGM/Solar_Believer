"""Report archive + ongoing collection against today's date, without row caps."""

import argparse
import json
import psycopg
from datetime import date, datetime
from pathlib import Path

from scripts.collect_public_history import KST, ROOT, dump, now
from scripts.postgres_store import open_store, store_exists

SOURCES = ("koen", "kospo", "komipo", "iwest", "smp")


def combined_source_status(history_output, output, source, start, target_end):
    records = set()
    for base in (Path(history_output), Path(output) / "live"):
        path = base / source
        if not store_exists(path):
            continue
        with open_store(path) as conn:
            records.update(conn.execute("SELECT day,resource FROM records WHERE day>=?", (str(start),)))
    current = {(day, resource) for day, resource in records if day <= str(target_end)}
    dates = {day for day, _ in current}
    all_dates = {day for day, _ in records}
    days = (target_end - start).days + 1
    path = Path(output) / f"{source}_status.json"
    latest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return dict(
        source=source,
        collection_target_start_kst=str(start),
        collection_target_end_kst=str(target_end),
        first_stored_date=min(all_dates) if all_dates else None,
        latest_stored_date=max(all_dates) if all_dates else None,
        latest_nonfuture_stored_date=max(dates) if dates else None,
        stored_records_through_today=len(current),
        future_published_records=len(records) - len(current),
        stored_dates_through_today=len(dates),
        target_days=days,
        date_coverage_percent=len(dates) / days * 100,
        dates_without_any_record=days - len(dates),
        raw_resource_keys=len({resource for _, resource in records}),
        latest_poll_state=latest.get("state"),
        latest_poll_finished_at_utc=latest.get("finished_at_utc"),
        latest_requested_end_kst=latest.get("requested_end_kst"),
        latest_poll_errors=latest.get("errors", []),
        latest_poll_error=latest.get("error"),
    )


def write_collection_summary(history_output, output, start=date(2022, 1, 1), target_end=None):
    target_end = target_end or datetime.now(KST).date()
    report = dict(
        generated_at_utc=now(),
        collection_target_start_kst=str(start),
        collection_target_end_kst=str(target_end),
        resource_count_limit=None,
        database="PostgreSQL",
        scope="combined historical and ongoing records; archival directory date is not the collection end date",
        date_coverage_definition="dates with at least one record / dates from target start through today",
        sources={},
    )
    for source in SOURCES:
        try:
            report["sources"][source] = combined_source_status(
                history_output, output, source, start, target_end
            )
        except (psycopg.Error, OSError, ValueError) as exc:
            report["sources"][source] = {"state": "status_read_error", "error": type(exc).__name__}
    dump(Path(output) / "latest_collection_summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-output", type=Path, default=ROOT / "data/api_history_20220101_20261031")
    parser.add_argument("--output", type=Path, default=ROOT / "data/continuous_collection")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    args = parser.parse_args()
    report = write_collection_summary(args.history_output, args.output, args.start)
    for source, status in report["sources"].items():
        print(json.dumps({"source": source, **status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
