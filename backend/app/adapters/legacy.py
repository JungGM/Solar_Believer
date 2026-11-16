"""Reuse map for API/. No network calls or legacy imports at service startup."""

from ..contracts import ConfigurationRequired


class LegacyCollector:
    def collect(self, start, end):
        # Reuse API.kma_collector / API.kpx_collector directly after reviewing
        # missingness, issue time, region aggregation, units and failure handling.
        # Do not call KVPPOrchestrator._save: it can overwrite matching raw CSVs.
        raise ConfigurationRequired("Legacy API → DB mapping is pending; see docs/INTEGRATIONS.md.")


class ExistingModelAdapter:
    def predict(self, features, cutoff):
        raise ConfigurationRequired("No model artifact or feature schema exists in the baseline repository.")


class LegacyForecastCollector:
    def collect_issue(self, issued_at):
        # Reuse KMAForecastCollector.fetch_forecast for this date/time and the
        # selected PLANT grids after pagination, retry and response checks exist.
        # The legacy province representative grids are not the target plant map.
        # Keep each issue in a distinct source key; do not overwrite old issues.
        raise ConfigurationRequired("KMA forecast API → DB mapping and delayed-publication retries are pending.")


class MonthlyTrainer:
    def train(self, snapshot, cutoff, output_dir):
        raise ConfigurationRequired("Training code, data snapshot and validation policy are pending.")

    def validate(self, candidate, holdout_snapshot):
        raise ConfigurationRequired("Chronological validation policy is pending.")


class UnconfiguredSettlementRule:
    version = "unconfigured"

    def calculate(self, prediction, market):
        raise ConfigurationRequired("Settlement rules are pending; no amounts were calculated.")
