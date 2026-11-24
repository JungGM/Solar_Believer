import gzip
import hashlib
import json
from datetime import date, datetime, timedelta

import pytest

from scripts.collect_kma_hub import Archive, MAX_BODY, OBS_COLUMNS, WaitForQuota, forecast_tasks, validate
from scripts.collect_public_history import KST


GRID = ("-99," * (149 * 253 - 1) + "7.5").encode()


def test_live_only_preserves_history_and_moves_recent_window_on_rollover(tmp_path, monkeypatch):
    from scripts import collect_kma_hub as hub

    clock = [datetime(2026, 11, 22, 12, tzinfo=KST)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(hub, "datetime", Clock)
    archive = Archive(tmp_path, "secret", live_only=True)
    for day in (date(2022, 1, 1), date(2026, 11, 21), date(2026, 11, 23)):
        archive.seed_day("forecast", day)
    assert archive.next_task("forecast")["day"] == "2026-11-23"
    archive.db.execute("UPDATE tasks SET state='ok' WHERE day='2026-11-23'")
    archive.db.commit()
    assert archive.next_task("forecast")["day"] == "2026-11-21"
    clock[0] += timedelta(days=1)
    assert archive.next_task("forecast") is None
    assert archive.db.execute("SELECT count(*) FROM tasks WHERE day='2022-01-01' AND state='pending'").fetchone()[0] == 224
    archive.live_only = False
    assert archive.next_task("forecast")["day"] == "2022-01-01"
    archive.close()


def test_forecast_only_does_not_enqueue_observations(tmp_path):
    archive = Archive(tmp_path, "secret")
    archive.seed_history(date(2022, 1, 1), date(2022, 1, 1), 8, forecast_only=True)
    archive.seed_live(datetime(2026, 11, 22, 12, tzinfo=KST), 8, forecast_only=True)
    assert archive.db.execute("SELECT count(*) FROM tasks WHERE kind='observation'").fetchone()[0] == 0
    assert archive.next_task("forecast") is not None
    archive.close()


def test_new_start_excludes_old_queue_without_blocking_new_history(tmp_path):
    archive = Archive(tmp_path, "secret")
    archive.seed_history(date(2022, 1, 1), date(2022, 1, 31), 8, forecast_only=True)
    archive.start_day = date(2026, 6, 1)
    archive.seed_history(archive.start_day, date(2026, 10, 31), 8, forecast_only=True)
    assert archive.next_task("forecast")["day"] == "2026-06-01"
    assert archive.db.execute("SELECT count(*) FROM tasks WHERE day<'2026-06-01'").fetchone()[0] == 7 * 224
    archive.seed_live(datetime(2026, 11, 22, 12, tzinfo=KST), 8, forecast_only=True)
    assert archive.next_task("forecast")["day"] == "2026-11-23"
    archive.close()


def test_history_first_refills_training_days_before_latest_issue(tmp_path):
    archive = Archive(tmp_path, "secret")
    archive.start_day = date(2026, 6, 1)
    archive.history_first = True
    archive.seed_live(datetime(2026, 11, 22, 12, tzinfo=KST), 8, forecast_only=True)
    archive.seed_history(archive.start_day, date(2026, 11, 22), 8, forecast_only=True)
    assert archive.next_task("forecast")["day"] == "2026-06-01"
    archive.db.execute("UPDATE tasks SET state='ok' WHERE day BETWEEN '2026-06-01' AND '2026-06-07'")
    archive.db.commit()
    archive.seed_history(archive.start_day, date(2026, 11, 22), 8, forecast_only=True)
    assert archive.next_task("forecast")["day"] == "2026-06-08"
    archive.close()


class Response:
    status_code = 200

    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.raw


def test_target_date_uses_previous_year_run_and_preserves_all_variables():
    tasks = list(forecast_tasks(date(2022, 1, 1)))
    assert len(tasks) == 224
    assert len({task[0] for task in tasks}) == 224
    assert {p["tmfc"] for _, p in tasks} == {"2021123108"}
    assert {p["tmef"] for _, p in tasks} == {f"20220101{h:02}" for h in range(6,22)}


def test_restart_retires_previously_queued_night_requests(tmp_path):
    archive = Archive(tmp_path, "key")
    params = dict(tmfc="2021123108", tmef="2022010105", vars="TMP")
    archive.db.execute("INSERT INTO tasks(id,kind,day,params,priority) VALUES(?,?,?,?,?)",
                       ("old-night", "forecast", "2022-01-01", json.dumps(params), 0))
    archive.db.commit()
    archive.close()
    resumed = Archive(tmp_path, "key")
    assert resumed.next_task("forecast") is None
    assert resumed.db.execute("SELECT state FROM tasks WHERE id='old-night'").fetchone()[0] == "excluded_by_hour_policy"
    resumed.close()


def test_validation_rejects_http_200_error_pages_and_missing_is_not_zero():
    with pytest.raises(ValueError, match="INVALID_GRID_LENGTH"):
        validate(b"<html>permission denied</html>", "forecast", {})
    summary, _, has_data = validate(GRID + b", \n", "forecast", {})
    assert has_data and summary["valid_cells"] == 1
    _, _, has_data = validate(("-99," * (149 * 253)).rstrip(",").encode(), "forecast", {})
    assert not has_data


def test_observations_retain_sentinels_and_reject_wrong_valid_time():
    params = {"tm": "202201010000"}
    fields = ["202201010000", "108"] + ["-9.0"] * (len(OBS_COLUMNS) - 2)
    raw = ("#START7777\n" + " ".join(fields) + "\n#END7777").encode()
    summary, rows, present = validate(raw, "observation", params)
    assert present and summary["rows"] == 1 and rows[0]["SI"] == "-9.0"
    with pytest.raises(ValueError, match="INVALID_OBSERVATION_ROW"):
        validate(raw, "observation", {"tm": "202201010100"})


def test_quota_reserves_interrupted_bytes_and_new_day_resets(tmp_path):
    archive = Archive(tmp_path, "secret", daily_calls=1, daily_bytes=MAX_BODY * 3)
    archive.claim()
    with pytest.raises(WaitForQuota):
        archive.claim()
    assert archive.usage()["bytes"] == MAX_BODY
    yesterday = str(date.fromisoformat(archive.usage()["day_kst"]) - timedelta(days=1))
    archive.db.execute("UPDATE calls SET day=?", (yesterday,))
    archive.db.commit()
    archive.claim()
    assert archive.usage()["calls"] == 1
    archive.close()


def test_resume_validates_raw_hash_and_never_persists_key(tmp_path, monkeypatch):
    archive = Archive(tmp_path, "TEST_SECRET_KEY", delay=0)
    archive.seed_day("forecast", date(2022, 1, 1))
    monkeypatch.setattr(archive.session, "get", lambda *a, **k: Response(GRID))
    archive.fetch_one("forecast")
    task = archive.db.execute("SELECT * FROM tasks WHERE state='ok'").fetchone()
    task_id, relative = task["id"], task["path"]
    raw = gzip.decompress((tmp_path / relative).read_bytes())
    metadata = json.loads((tmp_path / relative).with_suffix(".json").read_text())
    assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
    assert "TEST_SECRET_KEY" not in json.dumps(metadata)
    archive.close()
    resumed = Archive(tmp_path, "TEST_SECRET_KEY", delay=0)
    resumed.seed_day("forecast", date(2022, 1, 1))
    assert resumed.next_task("forecast")["id"] != task_id
    assert resumed.usage()["calls"] == 1
    assert resumed.usage()["bytes"] == len(GRID)
    resumed.close()


def test_bad_body_is_retryable_and_not_counted_as_success(tmp_path, monkeypatch):
    archive = Archive(tmp_path, "secret", delay=0)
    archive.seed_day("forecast", date(2022, 1, 1))
    monkeypatch.setattr(archive.session, "get", lambda *a, **k: Response(b"bad body"))
    archive.fetch_one("forecast")
    task = archive.db.execute("SELECT * FROM tasks WHERE attempts=1").fetchone()
    assert task["state"] == "retry" and task["error"] == "INVALID_GRID_LENGTH"
    assert task["path"] is None
    archive.close()
