from datetime import timedelta

from ..contracts import CollectedRecord


class DemoCollector:
    """Explicit synthetic infrastructure probe, never a real plant or market price."""

    def collect(self, start, end):
        timestamp = end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        return [
            CollectedRecord(
                source="demo",
                record_key=f"{kind}:{timestamp.isoformat()}",
                kind=kind,
                scope_id="demo-region",
                observed_at=timestamp,
                available_at=timestamp + timedelta(hours=1),
                values=values,
                is_demo=True,
            )
            for kind, values in [
                ("generation", {"generation_kwh": 12.5}),
                ("weather_observation", {"temperature_c": 20.0}),
                ("market", {"smp_krw_kwh": 100.0}),
            ]
        ]
