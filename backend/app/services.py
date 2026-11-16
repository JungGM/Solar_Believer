"""Small transactional services shared by worker jobs and explicit operator CLI."""

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .contracts import ConfigurationRequired
from .models import ModelVersion, Observation, utcnow


def store_records(session, records):
    count = 0
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    for record in records:
        for timestamp in (record.observed_at, record.available_at, record.issued_at):
            if timestamp is not None and timestamp.utcoffset() is None:
                raise ValueError("Collector timestamps must have a timezone")
        if record.kind == "weather_forecast" and record.issued_at is None:
            raise ValueError("Weather forecasts require issued_at")
        result = session.execute(
            insert(Observation)
            .values(**vars(record))
            .on_conflict_do_nothing(index_elements=["source", "record_key"])
            .returning(Observation.id)
        )
        # psycopg may report rowcount=-1 through SQLAlchemy ORM execution.
        count += len(result.scalars().all())
    return count


def cutoff_records(session, cutoff):
    """Future target weather is allowed only if issued and available before cutoff."""
    return session.scalars(
        select(Observation).where(
            Observation.available_at <= cutoff,
            (Observation.issued_at.is_(None) | (Observation.issued_at <= cutoff)),
            ~Observation.is_demo,
        )
    ).all()


def approved_model(session):
    model = session.scalar(select(ModelVersion).where(ModelVersion.active, ModelVersion.status == "approved"))
    if model is None:
        raise ConfigurationRequired("No approved active model; prediction was skipped.")
    return model


def checked_artifact(model, artifact_dir):
    root = Path(artifact_dir).resolve()
    artifact = (root / model.artifact_path).resolve()
    if not artifact.is_relative_to(root) or not artifact.is_file():
        raise ValueError("Model artifact must be an existing file inside MODEL_ARTIFACT_DIR")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != model.sha256:
        raise ValueError("Artifact checksum mismatch")
    return artifact


def approve_model(session, version, approver, artifact_dir):
    if not approver.strip():
        raise ValueError("An approver is required")
    model = session.scalar(select(ModelVersion).where(ModelVersion.version == version).with_for_update())
    if model is None or model.status != "validated" or not model.metrics or not model.feature_schema:
        raise ValueError("Only a validated model with metrics and feature schema can be approved")
    checked_artifact(model, artifact_dir)
    model.status = "approved"
    model.approved_by = approver
    model.approved_at = utcnow()


def activate_model(session, version, artifact_dir):
    # Lock the registry in PostgreSQL so concurrent operator commands serialize.
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text

        session.execute(text("LOCK TABLE model_versions IN EXCLUSIVE MODE"))
    model = session.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if model is None or model.status != "approved" or not model.approved_by:
        raise ValueError("Only explicitly approved models may be activated")
    checked_artifact(model, artifact_dir)
    for previous in session.scalars(select(ModelVersion).where(ModelVersion.active)):
        previous.active = False
    session.flush()
    model.active = True
