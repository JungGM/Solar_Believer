from datetime import datetime, timedelta

import pytest

from backend.app.config import Settings
from backend.app.forecast_schedule import latest_forecast_issue
from backend.app.jobs import run_job
from backend.app.worker import build_scheduler
from backend.app.contracts import CollectedRecord
from backend.app.models import JobRun, Observation
from sqlalchemy import select


def test_eight_daily_forecast_attempts():
    scheduler = build_scheduler(Settings(_env_file=None))
    trigger = scheduler.get_job("forecast").trigger
    now = datetime.fromisoformat("2026-11-17T00:00:00+09:00")
    attempts = []
    previous = None
    for _ in range(8):
        previous = trigger.get_next_fire_time(previous, now)
        attempts.append(previous.strftime("%H:%M"))
        now = previous
    assert attempts == ["02:20", "05:20", "08:20", "11:20", "14:20", "17:20", "20:20", "23:20"]
    assert trigger.get_next_fire_time(previous, now).date() == (now + timedelta(days=1)).date()


@pytest.mark.parametrize(
    "cutoff,expected",
    [
        ("2026-11-17T05:19:59+09:00", "2026-11-17T02:00:00+09:00"),
        ("2026-11-17T05:20:00+09:00", "2026-11-17T05:00:00+09:00"),
        ("2026-11-17T00:10:00+09:00", "2026-11-16T23:00:00+09:00"),
        ("2026-11-16T20:20:00+00:00", "2026-11-17T05:00:00+09:00"),
    ],
)
def test_issue_selection_preserves_publication_date(cutoff, expected):
    assert latest_forecast_issue(datetime.fromisoformat(cutoff)) == datetime.fromisoformat(expected)


def test_live_forecast_without_adapter_is_skipped(factory):
    settings = Settings(_env_file=None, pipeline_mode="live")
    assert run_job("forecast", factory=factory, settings=settings) == "skipped"


def test_fake_forecast_response_keeps_each_issue_without_duplicates(factory, monkeypatch):
    from backend.app import jobs

    clock = [datetime.fromisoformat("2026-11-17T05:20:00+09:00")]
    requested = []

    def collect_issue(self, issued_at):
        requested.append(issued_at.hour)
        return [
            CollectedRecord(
                source="test-fixture",
                record_key=f"plant:test:{issued_at.isoformat()}:TMP",
                kind="weather_forecast",
                scope_id="plant:test",
                observed_at=issued_at + timedelta(hours=6),
                available_at=clock[0],
                issued_at=issued_at,
                values={"temperature_c": 20},
                is_demo=True,
            )
        ]

    monkeypatch.setattr(jobs, "utcnow", lambda: clock[0])
    monkeypatch.setattr(jobs.LegacyForecastCollector, "collect_issue", collect_issue)
    settings = Settings(_env_file=None, pipeline_mode="live")
    run_job("forecast", factory=factory, settings=settings)
    run_job("forecast", factory=factory, settings=settings)
    clock[0] = datetime.fromisoformat("2026-11-17T08:20:00+09:00")
    run_job("forecast", factory=factory, settings=settings)
    with factory() as session:
        assert len(session.scalars(select(Observation)).all()) == 2
        assert session.scalars(select(JobRun.row_count).order_by(JobRun.id)).all() == [1, 0, 1]
    assert requested == [5, 5, 8]
