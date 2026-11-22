import json
import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.collect_public_history import APIError, Download, KST, UsageLedger
from scripts.maintain_public_data import (
    ManagedDownload,
    Singleton,
    completed_ranges,
    forecast_issues,
    observed_gap_months,
)
from scripts import maintain_public_data as maintenance


def test_old_onedrive_stop_is_ignored_but_a_new_stop_is_honored(tmp_path):
    path = tmp_path / "STOP"
    assert not maintenance.stop_requested(path, started_after=100)
    path.touch()
    os.utime(path, (99, 99))
    assert not maintenance.stop_requested(path, started_after=100)
    os.utime(path, (101, 101))
    assert maintenance.stop_requested(path, started_after=100)


def test_historical_and_live_workers_share_quota_and_reset_by_day(tmp_path):
    ledger = tmp_path / "usage.sqlite"
    history = ManagedDownload(tmp_path / "history", "iwest", "key", ledger, tmp_path / "STOP", call_limit=2)
    live = ManagedDownload(tmp_path / "live", "iwest", "key", ledger, tmp_path / "STOP", call_limit=2)
    history.claim_call("2026-11-21")
    live.claim_call("2026-11-21")
    with pytest.raises(APIError, match="LOCAL_DAILY_BUDGET"):
        live.claim_call("2026-11-21")
    live.claim_call("2026-11-22")
    assert live.usage.used("iwest", "2026-11-21") == 2
    assert live.usage.used("iwest", "2026-11-22") == 1
    history.close()
    live.close()


def test_importing_existing_calls_is_idempotent(tmp_path):
    job = Download(tmp_path / "history", "koen", "key", 0)
    job.claim_call("2026-11-21")
    job.close()
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    ledger.seed(tmp_path / "history")
    ledger.seed(tmp_path / "history")
    assert ledger.used("koen", "2026-11-21") == 1
    ledger.close()


def test_provider_quota_error_blocks_all_workers_until_next_day(tmp_path):
    job = ManagedDownload(tmp_path / "live", "iwest", "key", tmp_path / "usage.sqlite", tmp_path / "STOP")
    job.usage.block_today("iwest", "server quota")
    today = datetime.now(KST).date()
    with pytest.raises(APIError, match="LOCAL_DAILY_BUDGET"):
        job.claim_call(today.isoformat())
    job.claim_call((today + timedelta(days=1)).isoformat())
    job.close()


def test_refresh_updates_revisions_and_preserves_first_receipt_of_unchanged_values(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    row = dict(dgenYmd="2026-11-21", ippt="A", hogi="1", qhorGen01=1)
    bounds = date(2026, 11, 21), date(2026, 11, 21)
    job.save_records([row], "r1", {"fetched_at_utc": "2026-11-21T00:00:00+00:00"}, *bounds)
    job.save_records([row], "r2", {"fetched_at_utc": "2026-11-21T01:00:00+00:00"}, *bounds)
    assert job.db.execute("SELECT fetched FROM records").fetchone()[0] == "2026-11-21T00:00:00+00:00"
    job.save_records([dict(row, qhorGen01=2)], "r3", {"fetched_at_utc": "2026-11-21T02:00:00+00:00"}, *bounds)
    assert job.db.execute("SELECT count(*) FROM records").fetchone()[0] == 1
    assert job.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1
    assert job.db.execute("SELECT fetched FROM revisions").fetchone()[0] == "2026-11-21T00:00:00+00:00"
    job.close()


def test_cache_can_expire_for_periodic_refresh(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    calls = []

    class Response:
        status_code = 200
        content = (
            b'{"reponse":{"header":{"resultCode":"00"},"body":{"content":[{"value":1}],"totalElements":1}}}'
        )

    job.session.get = lambda *a, **k: calls.append(1) or Response()
    job.request({"page": 1})
    job.request({"page": 1})
    assert len(calls) == 1
    job.cache_seconds = 0
    job.request({"page": 1})
    assert len(calls) == 2
    job.close()


def test_forecast_recovery_uses_three_calendar_days_and_publication_buffer():
    before = forecast_issues(datetime(2026, 11, 21, 2, 19, tzinfo=KST))
    after = forecast_issues(datetime(2026, 11, 21, 2, 20, tzinfo=KST))
    assert before[0] == datetime(2026, 11, 20, 23, tzinfo=KST)
    assert after[0] == datetime(2026, 11, 21, 2, tzinfo=KST)
    assert min(x.date() for x in before) == date(2026, 11, 19)
    assert len(after) == 17


def test_forecast_issues_for_same_target_do_not_overwrite_each_other(tmp_path):
    job = Download(tmp_path, "forecast", "key", 0)
    row = dict(
        baseDate="20261121",
        baseTime="1700",
        nx=60,
        ny=127,
        fcstDate="20261122",
        fcstTime="0100",
        category="TMP",
        fcstValue="5",
    )
    job.save_records(
        [row, dict(row, baseTime="2000", fcstValue="6")], "r", {"fetched_at_utc": "2026-11-22T00:00:00+00:00"}, date.min, date.max
    )
    assert job.count() == 2
    job.close()


def test_detects_internal_missing_days_without_inventing_preexistence(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    row = dict(dgenYmd="2026-10-30", ippt="A", hogi="1", qhorGen01=1)
    job.save_records(
        [row, dict(row, dgenYmd="2026-11-02")], "r", {"fetched_at_utc": "2026-11-22T00:00:00+00:00"}, date.min, date.max
    )
    assert observed_gap_months(job) == {(2026, 10), (2026, 11)}
    job.close()


def test_partial_download_does_not_mark_a_month_complete(tmp_path):
    job = Download(tmp_path, "iwest", "key", 0)
    for page in [1, 2]:
        meta = dict(
            params=dict(startDate="20220101", endDate="20220131", pageNo=page, numOfRows=100),
            rows=100,
            total=250,
        )
        job.db.execute("INSERT INTO requests VALUES (?,?,?,?)", (str(page), "ok", "unused", json.dumps(meta)))
    assert completed_ranges(job) == set()
    meta["rows"] = 50
    job.db.execute("INSERT INTO requests VALUES (?,?,?,?)", ("3", "ok", "unused", json.dumps(meta)))
    assert len(completed_ranges(job)) == 1
    job.close()


def test_duplicate_runner_is_rejected_and_lock_is_released(tmp_path):
    first = Singleton(tmp_path / "collector.lock")
    with pytest.raises(RuntimeError, match="already running"):
        Singleton(tmp_path / "collector.lock")
    first.close()
    second = Singleton(tmp_path / "collector.lock")
    second.close()


def test_backfill_reserves_every_komipo_resource_for_future_live_cycles():
    args = SimpleNamespace(lookback_days=2, interval_minutes=60)
    midnight = datetime(2026, 11, 21, 0, 0, tzinfo=KST)
    assert maintenance.reserve_live_calls("komipo", args, midnight) == 24 * 26
    assert maintenance.reserve_live_calls("iwest", args, midnight) == 24


def test_western_live_reserve_covers_two_months_at_month_boundary():
    args = SimpleNamespace(lookback_days=2, interval_minutes=60)
    assert maintenance.reserve_live_calls("iwest", args, datetime(2026, 12, 1, 0, 0, tzinfo=KST)) == 48


def test_scheduler_wakes_at_daily_quota_reset_before_regular_interval():
    started = datetime(2026, 11, 21, 23, 56, tzinfo=KST)
    current = started + timedelta(minutes=1)
    assert maintenance.next_cycle_at(started, current, 60).astimezone(KST) == datetime(
        2026, 11, 22, 0, 0, 20, tzinfo=KST
    )


def test_scheduler_keeps_regular_interval_during_the_day():
    started = datetime(2026, 11, 21, 12, 0, tzinfo=KST)
    assert maintenance.next_cycle_at(started, started + timedelta(minutes=5), 60) == started + timedelta(
        hours=1
    )


def test_scheduler_runs_next_cycle_and_obeys_stop_file(tmp_path, monkeypatch):
    clock = [datetime(2026, 11, 21, 12, 0, tzinfo=KST)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    class Ledger:
        def __init__(self, path):
            pass

        def seed(self, path):
            pass

        def close(self):
            pass

    count = []

    def cycle(args, key):
        count.append(1)
        if len(count) == 2:
            (args.output / "STOP").touch()
        return {"koen": {"state": "updated"}}

    def sleep(seconds):
        clock[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(maintenance, "ROOT", tmp_path)
    monkeypatch.setattr(maintenance, "datetime", Clock)
    monkeypatch.setattr(maintenance, "now", lambda: clock[0].isoformat())
    monkeypatch.setattr(maintenance, "UsageLedger", Ledger)
    monkeypatch.setattr(maintenance, "dotenv_values", lambda p: {"DATA_GO_KR_API_KEY": "test-key"})
    monkeypatch.setattr(maintenance, "all_komipo_resources", lambda *a, **k: [])
    monkeypatch.setattr(maintenance, "run_cycle", cycle)
    monkeypatch.setattr(maintenance.time, "sleep", sleep)
    monkeypatch.setattr(
        maintenance.sys, "argv", ["collector", "--interval-minutes", "1", "--sources", "koen"]
    )
    assert maintenance.main() == 0
    status = json.loads(
        (tmp_path / "data/continuous_collection/collector_status.json").read_text(encoding="utf-8")
    )
    assert len(count) == 2 and status["cycles_completed"] == 2
    assert status["state"] == "stopped"
