import hashlib

import pytest
from sqlalchemy import select

from backend.app.models import ModelVersion
from backend.app.services import activate_model, approve_model, checked_artifact


def candidate(version, digest):
    return ModelVersion(
        version=version,
        artifact_path="test.bin",
        sha256=digest,
        feature_schema={"fields": ["temperature_c"]},
        metrics={"mae": 1.0},
        status="validated",
    )


def test_approval_activation_and_atomic_switch(factory, tmp_path):
    data = b"a test artifact, never loaded as a model"
    (tmp_path / "test.bin").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    with factory.begin() as session:
        session.add_all([candidate("v1", digest), candidate("v2", digest)])
    with factory.begin() as session:
        with pytest.raises(ValueError, match="approved"):
            activate_model(session, "v1", tmp_path)
        approve_model(session, "v1", "developer-b", tmp_path)
        session.flush()
        activate_model(session, "v1", tmp_path)
    with factory.begin() as session:
        approve_model(session, "v2", "developer-a", tmp_path)
        session.flush()
        activate_model(session, "v2", tmp_path)
    with factory() as session:
        active = session.scalars(select(ModelVersion).where(ModelVersion.active)).all()
        assert [m.version for m in active] == ["v2"]
        assert active[0].approved_by == "developer-a"


def test_hash_and_path_checks(tmp_path):
    (tmp_path / "test.bin").write_bytes(b"changed")
    model = candidate("v1", "0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        checked_artifact(model, tmp_path)
    model.artifact_path = "../outside.bin"
    with pytest.raises(ValueError, match="inside"):
        checked_artifact(model, tmp_path)
