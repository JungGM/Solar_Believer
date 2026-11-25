"""Collector storage tests use isolated PostgreSQL schemas, never SQLite."""

from uuid import uuid4

import pytest
from psycopg import sql

from scripts.postgres_store import TEST_SCHEMAS, TEST_STORES, connect_postgres


@pytest.fixture(autouse=True)
def isolated_collector_database(monkeypatch):
    prefix = "tcol_" + uuid4().hex[:12]
    monkeypatch.setenv("COLLECTOR_TEST_PREFIX", prefix)
    monkeypatch.setenv("COLLECTOR_SERVICE_SCHEMA", prefix + "_service")
    yield
    for store in list(TEST_STORES):
        if store.schema.startswith(prefix + "_"):
            store.close()
            TEST_STORES.remove(store)
    with connect_postgres() as conn:
        for schema in list(TEST_SCHEMAS):
            if schema.startswith(prefix + "_"):
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
                TEST_SCHEMAS.remove(schema)
