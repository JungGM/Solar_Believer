from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.config import Settings
from backend.app.contracts import CollectedRecord
from backend.app.jobs import run_job
from backend.app.models import JobRun, Observation, TrainingRun
from backend.app.services import cutoff_records, store_records
from backend.app.worker import build_scheduler


def test_demo_ingestion_and_durable_job_key(factory):
    settings = Settings(_env_file=None, pipeline_mode="demo")
    assert run_job("collect", "demo-slot", factory, settings) == "completed"
    assert run_job("collect", "demo-slot", factory, settings) == "completed"
    # A new manual job within the same hour also cannot duplicate source records.
    run_job("collect", "another-slot", factory, settings)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 3
        assert all(r.is_demo for r in session.scalars(select(Observation)))
        assert session.scalar(select(func.count()).select_from(JobRun)) == 2
        assert session.scalars(select(JobRun.row_count).order_by(JobRun.id)).all() == [3, 0]


@pytest.mark.parametrize("job_type", ["collect", "predict", "settle", "retrain"])
def test_unconfigured_jobs_are_explicitly_skipped(factory, job_type):
    settings = Settings(_env_file=None)
    assert run_job(job_type, factory=factory, settings=settings) == "skipped"
    with factory() as session:
        assert session.scalar(select(JobRun)).finished_at is not None
        assert session.scalar(select(func.count()).select_from(Observation)) == 0
        if job_type == "retrain":
            assert session.scalar(select(TrainingRun)).status == "skipped"


def test_failed_job_rolls_back_partial_data_and_redacts_error(factory, monkeypatch):
    from backend.app import jobs

    def fail(session, *_):
        session.add(TrainingRun(status="running", cutoff_at=datetime.now(timezone.utc)))
        session.flush()
        raise RuntimeError("https://example.test/?serviceKey=SECRET")

    monkeypatch.setattr(jobs, "execute_job", fail)
    assert run_job("collect", factory=factory, settings=Settings(_env_file=None)) == "failed"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(TrainingRun)) == 0
        assert "SECRET" not in session.scalar(select(JobRun)).message


def test_cutoff_excludes_late_forecasts_and_demo(factory):
    cutoff = datetime(2026, 11, 17, tzinfo=timezone.utc)
    with factory.begin() as session:
        for index, (available, issued, demo) in enumerate(
            [
                (cutoff, cutoff, False),
                (cutoff + timedelta(hours=1), cutoff, False),
                (cutoff, cutoff + timedelta(hours=1), False),
                (cutoff, cutoff, True),
            ]
        ):
            store_records(
                session,
                [
                    CollectedRecord(
                        source="test",
                        record_key=str(index),
                        kind="weather_forecast",
                        scope_id="region:11",
                        observed_at=cutoff + timedelta(days=1),
                        available_at=available,
                        issued_at=issued,
                        values={"temperature_c": 20},
                        is_demo=demo,
                    )
                ],
            )
        assert len(cutoff_records(session, cutoff)) == 1


def test_forecasts_require_issue_time(factory):
    now = datetime.now(timezone.utc)
    with factory.begin() as session, pytest.raises(ValueError, match="issued_at"):
        store_records(session, [CollectedRecord("test", "1", "weather_forecast", "11", now, now, {})])


def test_monthly_schedule_is_once_per_month_in_kst():
    scheduler = build_scheduler(Settings(_env_file=None))
    trigger = scheduler.get_job("retrain").trigger
    first = trigger.get_next_fire_time(None, datetime(2026, 11, 17, tzinfo=timezone.utc))
    second = trigger.get_next_fire_time(first, first)
    assert (first.month, first.day, first.hour) == (12, 1, 3)
    assert (second.month, second.day, second.hour) == (1, 1, 3)
    assert str(trigger.timezone) == "Asia/Seoul"
