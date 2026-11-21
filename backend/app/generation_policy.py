"""User-selected fixed hours for model/evaluation values; raw values stay intact."""
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))
ZERO_HOURS_KST = (0, 1, 2, 3, 4, 5, 22, 23)
FORECAST_HOURS_KST = tuple(range(6, 22))
POLICY_VERSION = "fixed_kst_00_05_22_23_zero_v1"


def is_zero_hour(interval_start):
    if interval_start.utcoffset() is None:
        raise ValueError("Generation interval start must have a timezone")
    return interval_start.astimezone(KST).hour in ZERO_HOURS_KST


def model_generation(value, interval_start):
    # Missing source rows/model predictions remain distinguishable from measured zero.
    if value is None:
        return None
    return 0.0 if is_zero_hour(interval_start) else value
