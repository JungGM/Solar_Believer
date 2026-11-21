# Imported pytest fixtures are intentionally used as function parameters.
# ruff: noqa: F811
from datetime import timedelta

from sqlalchemy import func, select

from backend.app.models import Observation, Prediction
from backend.tests.test_dashboard import START, dashboard_client, forecast, observation, query  # noqa: F401


def test_day_ahead_excludes_same_day_and_late_inserted_forecasts(factory, dashboard_client):
    with factory.begin() as session:
        valid = forecast("plant:a", 10, lead=19)  # Previous day 17:00 KST.
        valid.created_at = START - timedelta(hours=7)
        at_deadline = forecast("plant:b", 20, lead=18)  # 18:00 KST inclusive.
        at_deadline.created_at = START - timedelta(hours=6)
        same_day = forecast("plant:a", 999, lead=0)
        same_day.created_at = START
        late_inserted = forecast("plant:a", 888, lead=18)
        late_inserted.created_at = START - timedelta(hours=5)
        session.add_all([valid, at_deadline, same_day, late_inserted])
    result = query(dashboard_client, prediction_policy="day_ahead")
    assert result["summary"]["predicted_kwh"] == 30
    assert result["prediction_selection"] == "day_ahead"
    assert result["prediction_generated_at"] is not None
    assert query(dashboard_client)["summary"]["predicted_kwh"] == 1019


def test_plants_include_unassigned_no_data_and_zero_generation(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all([
            observation("plant:a", "generation", {"generation_kwh": 0}),
            observation("plant:a", "generation", {"generation_kwh": 9}, index=2, source="wrong"),
        ])
    response = dashboard_client.get("/api/v1/workspace/plants")
    assert response.status_code == 200
    plants = {p["id"]: p for p in response.json()["plants"]}
    assert len(plants) == 3
    assert plants["plant:a"]["stored_rows"] == 1
    assert plants["plant:a"]["first_seen"] is not None
    assert plants["plant:b"]["status"] == "no_data"
    assert plants["plant:c"]["latitude"] is None
    assert plants["plant:a"]["memberships"][0]["portfolio_id"] == 1
    assert "폐지" in response.json()["history_policy"]


def test_weather_date_scope_and_demo_isolation(factory, dashboard_client):
    with factory.begin() as session:
        records = [
            observation("region:11", "weather_observation", {"temperature_c": 0}, source="kma"),
            observation("plant:a", "weather_forecast", {"TMP": "1"}, source="kma"),
            observation("region:11", "weather_observation", {"temperature_c": 100}, index=3, source="kma"),
            observation("region:11", "weather_observation", {"temperature_c": 99}, index=4, source="kma"),
        ]
        records[1].issued_at = START - timedelta(hours=6)
        records[2].is_demo = True
        records[3].available_at = START + timedelta(days=36500)
        session.add_all(records)
    result = dashboard_client.get("/api/v1/workspace/weather?date=2026-01-01").json()
    assert len(result["records"]) == 2
    assert set(result["scopes"]) == {"region:11", "plant:a"}
    filtered = dashboard_client.get("/api/v1/workspace/weather?date=2026-01-01&scope_id=region:11").json()
    assert len(filtered["records"]) == 1
    assert filtered["records"][0]["values"]["temperature_c"] == 0
    assert dashboard_client.get("/api/v1/workspace/weather?date=2026-01-02").json()["records"] == []
    assert dashboard_client.get("/api/v1/workspace/weather?date=1999-01-01").status_code == 422


def test_workspace_reads_do_not_write(factory, dashboard_client):
    with factory() as session:
        before = session.scalar(select(func.count()).select_from(Observation))
    assert dashboard_client.get("/api/v1/workspace/plants").status_code == 200
    assert dashboard_client.get("/api/v1/workspace/weather").status_code == 200
    assert query(dashboard_client, demo=True, prediction_policy="day_ahead")["is_demo"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == before
        assert session.scalar(select(func.count()).select_from(Prediction)) == 0


def test_vpp_reduction_uses_same_complete_hours(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all([
            observation("plant:a", "generation", {"generation_kwh": 10}),
            observation("plant:b", "generation", {"generation_kwh": 20}),
            forecast("plant:a", 15), forecast("plant:b", 15),
        ])
    result = query(dashboard_client)
    assert result["summary"]["mae_kwh"] == 0
    assert result["summary"]["individual_mae_kwh"] == 10
    assert result["series"][12]["individual_absolute_error_kwh"] == 10
    assert result["series"][13]["individual_absolute_error_kwh"] is None
