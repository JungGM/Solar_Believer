"""KMA short-range issue selection; publication time differs from availability."""

from datetime import timedelta
from zoneinfo import ZoneInfo

ISSUE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)


def latest_forecast_issue(cutoff, timezone="Asia/Seoul"):
    if cutoff.utcoffset() is None:
        raise ValueError("Forecast cutoff must have a timezone")
    # Twenty minutes is our polling buffer, not an API availability guarantee.
    eligible = cutoff.astimezone(ZoneInfo(timezone)) - timedelta(minutes=20)
    hours = [hour for hour in ISSUE_HOURS if hour <= eligible.hour]
    if not hours:
        eligible -= timedelta(days=1)
    hour = hours[-1] if hours else ISSUE_HOURS[-1]
    return eligible.replace(hour=hour, minute=0, second=0, microsecond=0)
