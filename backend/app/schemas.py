from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ObservationRead(ReadModel):
    source: str
    kind: str
    scope_id: str
    observed_at: datetime
    issued_at: datetime | None
    available_at: datetime
    values: dict
    is_demo: bool


class PredictionRead(ReadModel):
    model_version_id: int
    scope_id: str
    target_at: datetime
    cutoff_at: datetime
    generation_kwh: Decimal


class SettlementRead(ReadModel):
    prediction_id: int
    rule_version: str
    amount_krw: Decimal
    is_simulation: bool
    details: dict


class ModelVersionRead(ReadModel):
    version: str
    status: str
    active: bool
    metrics: dict
    approved_by: str | None
    approved_at: datetime | None


class TrainingRunRead(ReadModel):
    status: str
    cutoff_at: datetime
    metrics: dict
    note: str
    created_at: datetime


class JobRunRead(ReadModel):
    job_type: str
    status: str
    message: str
    row_count: int
    started_at: datetime
    finished_at: datetime | None
