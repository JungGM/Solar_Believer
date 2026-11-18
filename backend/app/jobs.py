import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .adapters.demo import DemoCollector
from .adapters.legacy import (
    ExistingModelAdapter,
    LegacyCollector,
    LegacyForecastCollector,
    MonthlyTrainer,
    UnconfiguredSettlementRule,
    sync_koen_plants_to_db,
)
from .config import get_settings
from .contracts import ConfigurationRequired
from .db import session_factory
from .forecast_schedule import latest_forecast_issue
from .models import JobRun, TrainingRun, utcnow
from .services import approved_model, checked_artifact, cutoff_records, store_records

logger = logging.getLogger(__name__)
JOB_TYPES = ("collect", "forecast", "predict", "settle", "retrain")


def execute_job(session, job_type, settings, cutoff):
    if job_type == "forecast":
        if settings.pipeline_mode != "live":
            raise ConfigurationRequired("Live weather forecast collection is not enabled.")
        issued_at = latest_forecast_issue(cutoff, settings.scheduler_timezone)
        rows = LegacyForecastCollector(api_key=settings.data_go_kr_api_key).collect_issue(issued_at)
        return "completed", store_records(session, rows), "Weather forecast issue stored."
    if job_type == "collect":
        if settings.pipeline_mode == "disabled":
            raise ConfigurationRequired(
                "Collection disabled; use explicit demo mode or implement live adapter."
            )
        if settings.pipeline_mode == "demo":
            collector = DemoCollector()
        elif settings.pipeline_mode == "live":
            collector = LegacyCollector(
                api_key=settings.data_go_kr_api_key,
                kma_key=settings.kma_apihub_key,
            )
        else:
            raise ValueError("PIPELINE_MODE must be disabled, demo or live")
        rows = collector.collect(cutoff - timedelta(days=settings.collection_lookback_days), cutoff)
        if settings.pipeline_mode == "live":
            sync_koen_plants_to_db(session)
        return (
            "completed",
            store_records(session, rows),
            "Collected records; demo rows are explicitly labeled.",
        )
    if job_type == "predict":
        model = approved_model(session)
        checked_artifact(model, settings.model_artifact_dir)
        records = cutoff_records(session, cutoff)
        ExistingModelAdapter().predict([r.values for r in records], cutoff)
        # The actual output mapping/persistence belongs here after schema review.
        raise ConfigurationRequired("Prediction output mapping is pending.")
    if job_type == "settle":
        UnconfiguredSettlementRule().calculate({}, {})
    if job_type == "retrain":
        training = TrainingRun(status="running", cutoff_at=cutoff)
        session.add(training)
        try:
            MonthlyTrainer().train("unconfigured", cutoff, settings.model_artifact_dir)
        except ConfigurationRequired as exc:
            training.status = "skipped"
            training.note = str(exc)
            return "skipped", 0, str(exc)
        raise ConfigurationRequired("Candidate registration and validation mapping are pending.")
    raise ValueError(f"Unknown job type: {job_type}")


def run_job(job_type, job_key=None, factory=None, settings=None):
    if job_type not in JOB_TYPES:
        raise ValueError("Unknown job type")
    factory = factory or session_factory()
    settings = settings or get_settings()
    key = job_key or f"manual:{job_type}:{uuid4()}"
    cutoff = utcnow()
    # Claim a durable key before work. A scheduler restart won't repeat a slot.
    try:
        with factory.begin() as session:
            run = JobRun(job_key=key, job_type=job_type, status="running")
            session.add(run)
            session.flush()
            run_id = run.id
    except IntegrityError:
        with factory() as session:
            existing = session.scalar(select(JobRun).where(JobRun.job_key == key))
            if existing is None:
                raise
            return existing.status
    try:
        with factory.begin() as session:
            status, count, message = execute_job(session, job_type, settings, cutoff)
    except ConfigurationRequired as exc:
        status, count, message = "skipped", 0, str(exc)
    except Exception as exc:
        # Never persist exception URLs, query strings or credentials from collectors.
        logger.error("%s job failed: %s", job_type, type(exc).__name__)
        status, count, message = "failed", 0, f"Integration error: {type(exc).__name__}"
    with factory.begin() as session:
        run = session.get(JobRun, run_id)
        run.status, run.row_count, run.message = status, count, message
        run.finished_at = utcnow()
    logger.info("%s: %s (%s)", job_type, status, key)
    return status
