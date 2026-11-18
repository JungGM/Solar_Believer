import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.app.config import get_settings
from backend.app.db import Base
from backend.app import models  # noqa: F401


@pytest.fixture(params=["sqlite", "postgres"])
def factory(request):
    if request.param == "sqlite":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False)
        engine.dispose()
        return
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Set RUN_POSTGRES_TESTS=1 for isolated PostgreSQL schema tests")
    schema = f"test_{uuid4().hex}"
    admin = create_engine(get_settings().database_url)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(get_settings().database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        # Only the freshly created random test schema is removed; public is untouched.
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
