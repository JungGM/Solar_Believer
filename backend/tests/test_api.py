from fastapi.testclient import TestClient

from backend.app.db import get_session
from backend.app.jobs import run_job
from backend.app.config import Settings
from backend.app.main import app


def test_database_read_api_and_empty_states(factory):
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200
            for route in [
                "observations",
                "predictions",
                "settlements",
                "model-versions",
                "training-runs",
                "job-runs",
            ]:
                assert client.get(f"/api/v1/{route}").json() == []
            assert client.get("/api/v1/observations?limit=501").status_code == 422
            run_job("collect", factory=factory, settings=Settings(_env_file=None, pipeline_mode="demo"))
            assert client.get("/api/v1/summary").json()["demo_observations"] == 3
            assert len(client.get("/api/v1/observations").json()) == 3
            assert client.get("/api/v1/observations?scope_id=missing").json() == []
            assert client.get("/api/v1/job-runs").json()[0]["status"] == "completed"
    finally:
        app.dependency_overrides.clear()
