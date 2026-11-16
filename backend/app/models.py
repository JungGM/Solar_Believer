"""UTC timestamps, explicit units, immutable model artifacts and simulation labels."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("source", "record_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80))
    record_key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), index=True)
    scope_id: Mapped[str] = mapped_column(String(80), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    values: Mapped[dict] = mapped_column(JSON)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingRun(Base):
    __tablename__ = "training_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_snapshot: Mapped[str | None] = mapped_column(String(500))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint("status IN ('candidate','validated','approved','retired')"),
        CheckConstraint("NOT active OR status = 'approved'"),
        Index(
            "uq_single_active_model",
            "active",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(100), unique=True)
    training_run_id: Mapped[int | None] = mapped_column(ForeignKey("training_runs.id"))
    artifact_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    feature_schema: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("model_version_id", "scope_id", "target_at", "cutoff_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    scope_id: Mapped[str] = mapped_column(String(80), index=True)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("prediction_id", "rule_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"))
    rule_version: Mapped[str] = mapped_column(String(100))
    amount_krw: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(String(150), unique=True)
    job_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(500), default="")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
