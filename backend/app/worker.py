"""One scheduler per Compose project; schedules use Asia/Seoul by default."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .jobs import run_job


def scheduled_job(job_type):
    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    slot = now.strftime("%Y-%m") if job_type == "retrain" else now.strftime("%Y-%m-%dT%H:%M")
    status = run_job(job_type, job_key=f"scheduled:{job_type}:{slot}")
    if job_type == "predict" and status == "completed":
        run_job("settle", job_key=f"scheduled:settle:{slot}")


def build_scheduler(settings):
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    for job_type, expression in [
        ("collect", settings.collection_cron),
        ("predict", settings.prediction_cron),
        ("retrain", settings.retraining_cron),
    ]:
        scheduler.add_job(
            scheduled_job,
            CronTrigger.from_crontab(expression, timezone=settings.scheduler_timezone),
            args=[job_type],
            id=job_type,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scheduler = build_scheduler(get_settings())
    logging.info("Scheduler ready; live integrations remain disabled until configured.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
