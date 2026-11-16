"""Implement these contracts when the real data/model/rules are agreed."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class ConfigurationRequired(RuntimeError):
    """A deliberately unconfigured integration, recorded as skipped by jobs."""


@dataclass(frozen=True)
class CollectedRecord:
    source: str
    record_key: str
    kind: str
    scope_id: str
    observed_at: datetime
    available_at: datetime
    values: dict
    issued_at: datetime | None = None
    is_demo: bool = False


class Collector(Protocol):
    def collect(self, start: datetime, end: datetime) -> list[CollectedRecord]: ...


class ForecastModel(Protocol):
    def predict(self, features: list[dict], cutoff: datetime) -> list[dict]:
        """Return scope_id, target_at and generation_kwh using cutoff-safe inputs."""
        ...


class Trainer(Protocol):
    def train(self, snapshot: str, cutoff: datetime, output_dir: str) -> dict:
        """Write a NEW artifact; return path, hash, feature schema and metrics."""
        ...

    def validate(self, candidate: dict, holdout_snapshot: str) -> dict:
        """Chronological validation against the current model; never auto-approve."""
        ...


class SettlementRule(Protocol):
    version: str

    def calculate(self, prediction: dict, market: dict) -> Decimal: ...
