from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.dashboard import period_bounds
from backend.app.db import get_session
from backend.app.main import app
from backend.app.contracts import CollectedRecord
from backend.app.services import store_records
from backend.app.models import (
    ModelVersion,
    Observation,
    Plant,
    Prediction,
    VppMembership,
    VppPortfolio,
    VppSettlement,
)

START = datetime(2025, 12, 31, 15, tzinfo=timezone.utc)  # 2026-01-01 00:00 KST


@pytest.fixture
def dashboard_client(factory):
    with factory.begin() as session:
        session.add_all(
            [
                Plant(
                    id="plant:a",
                    name="A",
                    capacity_kw=10000,
                    latitude=36,
                    longitude=127,
                    smp_zone="land",
                    generation_source="meter",
                ),
                Plant(
                    id="plant:b",
                    name="B",
                    capacity_kw=10000,
                    latitude=35,
                    longitude=128,
                    smp_zone="land",
                    generation_source="meter",
                ),
                Plant(
                    id="plant:c",
                    name="C",
                    capacity_kw=5000,
                    latitude=None,
                    longitude=None,
                    smp_zone="jeju",
                    generation_source="meter",
                ),
                VppPortfolio(id=1, slug="one", name="Group 1", version="v1"),
                VppPortfolio(id=2, slug="two", name="Group 2", version="v1"),
                ModelVersion(
                    id=1,
                    version="v1",
                    artifact_path="none",
                    sha256="0" * 64,
                    feature_schema={},
                    status="candidate",
                ),
                ModelVersion(
                    id=2,
                    version="v2",
                    artifact_path="none",
                    sha256="0" * 64,
                    feature_schema={},
                    status="candidate",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                VppMembership(portfolio_id=1, plant_id="plant:a", valid_from=START),
                VppMembership(portfolio_id=1, plant_id="plant:b", valid_from=START),
                VppMembership(portfolio_id=2, plant_id="plant:c", valid_from=START),
            ]
        )

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def observation(scope, kind, value, index=0, source="meter", available=None):
    return Observation(
        source=source,
        record_key=f"{scope}:{kind}:{index}",
        scope_id=scope,
        kind=kind,
        observed_at=START,
        available_at=available or START + timedelta(hours=1),
        values=value,
    )


def forecast(plant, value, model=1, lead=1):
    return Prediction(
        model_version_id=model,
        scope_id=plant,
        target_at=START,
        cutoff_at=START - timedelta(hours=lead),
        generation_kwh=value,
    )


def query(client, **kwargs):
    response = client.get(
        "/api/v1/dashboard",
        params={
            "date": "2026-01-01",
            "portfolio_id": 1,
            "model_version_id": 1,
            **kwargs,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_portfolio_model_and_error_aggregate_before_absolute(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all(
            [
                observation("plant:a", "generation", {"generation_kwh": 10}),
                observation("plant:b", "generation", {"generation_kwh": 20}),
                forecast("plant:a", 15),
                forecast("plant:b", 15),
                forecast("plant:a", 18, model=2),
                forecast("plant:b", 18, model=2),
            ]
        )
    result = query(dashboard_client)
    assert {p["id"] for p in result["plants"]} == {"plant:a", "plant:b"}
    assert result["summary"]["actual_kwh"] == 30
    assert result["summary"]["predicted_kwh"] == 30
    assert result["summary"]["mae_kwh"] == 0  # opposite site errors cancel in this VPP hour
    assert result["summary"]["paired_hours"] == 1
    assert result["series"][0]["absolute_error_kwh"] == 0
    assert query(dashboard_client, model_version_id=2)["summary"]["predicted_kwh"] == 36


def test_missing_labels_stay_null_and_zero_is_valid(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all(
            [
                observation("plant:a", "generation", {"generation_kwh": 0}),
                forecast("plant:a", 0),
                forecast("plant:b", 0),
            ]
        )
    result = query(dashboard_client)
    assert result["summary"]["actual_kwh"] is None
    assert result["summary"]["predicted_kwh"] == 0
    assert result["summary"]["mae_kwh"] is None
    assert result["series"][0]["actual_kwh"] is None
    assert result["plants"][0]["actual_kwh"] == 0
    # Month bins require all constituent portfolio hours; partial totals are not charted as full days.
    assert query(dashboard_client, period="month")["series"][0]["predicted_kwh"] is None


def test_independent_vpp_errors_do_not_cancel(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all(
            [
                observation("plant:a", "generation", {"generation_kwh": 10}),
                observation("plant:b", "generation", {"generation_kwh": 20}),
                observation("plant:c", "generation", {"generation_kwh": 20}),
                forecast("plant:a", 20),
                forecast("plant:b", 20),
                forecast("plant:c", 10),
            ]
        )
    result = dashboard_client.get("/api/v1/dashboard?date=2026-01-01&model_version_id=1").json()
    assert result["summary"]["actual_kwh"] == result["summary"]["predicted_kwh"] == 50
    assert result["summary"]["mae_kwh"] == 20  # |+10| + |-10|, not |+10 - 10|


def test_forecast_cutoff_revisions_and_canonical_meter_source(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all(
            [
                observation("plant:a", "generation", {"generation_kwh": 3}, index=1),
                observation(
                    "plant:a",
                    "generation",
                    {"generation_kwh": 4},
                    index=2,
                    available=START + timedelta(hours=2),
                ),
                observation("plant:a", "generation", {"generation_kwh": 999}, index=3, source="other"),
                observation("plant:b", "generation", {"generation_kwh": 6}),
                forecast("plant:a", 1, lead=2),
                forecast("plant:a", 4, lead=1),
                forecast("plant:a", 999, lead=-1),
                forecast("plant:b", 6),
            ]
        )
    result = query(dashboard_client)
    assert result["series"][0]["predicted_kwh"] == 10
    assert result["series"][0]["actual_kwh"] == 10


def test_smp_zones_and_monetary_rule_versions_are_not_mixed(factory, dashboard_client):
    with factory.begin() as session:
        session.add_all(
            [
                observation("market:land", "market", {"smp_krw_kwh": 100}, source="kpx_smp"),
                observation("market:jeju", "market", {"smp_krw_kwh": 200}, source="kpx_smp"),
                VppSettlement(
                    portfolio_id=1,
                    model_version_id=1,
                    target_at=START,
                    rule_version="test-a",
                    amount_krw=100,
                    loss_krw=5,
                    is_simulation=True,
                    details={},
                ),
                VppSettlement(
                    portfolio_id=1,
                    model_version_id=1,
                    target_at=START,
                    rule_version="test-b",
                    amount_krw=900,
                    loss_krw=None,
                    is_simulation=True,
                    details={},
                ),
            ]
        )
    result = query(dashboard_client)
    assert result["summary"]["smp_land"] == 100
    assert result["summary"]["smp_jeju"] is None
    assert result["summary"]["settlement_krw"] is None
    result = query(dashboard_client, settlement_rule_version="test-a")
    assert result["summary"]["settlement_krw"] == 100
    assert result["summary"]["loss_krw"] == 5
    assert result["settlement"]["is_simulation"] is True
    assert query(dashboard_client, portfolio_id=2)["summary"]["smp_jeju"] == 200


def test_membership_interval_and_missing_coordinates(factory, dashboard_client):
    with factory.begin() as session:
        membership = session.scalar(select(VppMembership).where(VppMembership.plant_id == "plant:b"))
        membership.valid_to = START + timedelta(hours=1)
    result = query(dashboard_client)
    assert next(p for p in result["plants"] if p["id"] == "plant:b")["expected_hours"] == 1
    other = query(dashboard_client, portfolio_id=2)
    assert len(other["plants"]) == 1  # location missing does not remove it from totals/table
    assert any("좌표" in warning for warning in other["warnings"])


def test_collector_kst_timestamps_are_normalized_for_period_queries(factory, dashboard_client):
    from zoneinfo import ZoneInfo

    local = START.astimezone(ZoneInfo("Asia/Seoul"))
    with factory.begin() as session:
        store_records(
            session,
            [
                CollectedRecord(
                    "meter",
                    plant,
                    "generation",
                    plant,
                    local,
                    local + timedelta(hours=1),
                    {"generation_kwh": 5},
                )
                for plant in ("plant:a", "plant:b")
            ],
        )
    result = query(dashboard_client)
    assert result["series"][0]["label"] == "00:00"
    assert result["series"][0]["actual_kwh"] == 10


def test_empty_unknown_and_demo_is_read_only(factory, dashboard_client):
    for path in ["portfolio_id=99", "model_version_id=99", "period=week", "date=1900-01-01"]:
        assert dashboard_client.get(f"/api/v1/dashboard?{path}").status_code == 422
    before = {}
    with factory() as session:
        for table in (Plant, Prediction, Observation, VppSettlement):
            before[table] = session.scalar(select(func.count()).select_from(table))
    response = dashboard_client.get("/api/v1/dashboard?demo=true&date=2026-01-01&portfolio_id=1")
    assert response.status_code == 200
    result = response.json()
    assert result["is_demo"] and result["summary"]["capacity_kw"] == 20000
    assert result["summary"]["plant_count"] == 4
    assert result["summary"]["settlement_krw"] is None
    assert len(result["series"]) == 24
    with factory() as session:
        for table, count in before.items():
            assert session.scalar(select(func.count()).select_from(table)) == count


def test_demo_group_and_model_filters_do_not_change_underlying_actuals(dashboard_client):
    all_data = dashboard_client.get("/api/v1/dashboard?demo=true&date=2026-01-01").json()
    group = dashboard_client.get("/api/v1/dashboard?demo=true&date=2026-01-01&portfolio_id=2").json()
    other_model = dashboard_client.get(
        "/api/v1/dashboard?demo=true&date=2026-01-01&model_version_id=2"
    ).json()
    all_actuals = {p["id"]: p["actual_kwh"] for p in all_data["plants"]}
    assert all(p["actual_kwh"] == all_actuals[p["id"]] for p in group["plants"])
    assert other_model["summary"]["actual_kwh"] == all_data["summary"]["actual_kwh"]
    assert other_model["summary"]["predicted_kwh"] != all_data["summary"]["predicted_kwh"]


@pytest.mark.parametrize(
    "period,selected,hours",
    [
        ("day", date(2026, 1, 1), 24),
        ("month", date(2024, 2, 3), 29 * 24),
        ("year", date(2024, 4, 1), 366 * 24),
    ],
)
def test_kst_period_boundaries(period, selected, hours):
    start, end = period_bounds(period, selected)
    assert start.hour == 15
    assert (end - start).total_seconds() / 3600 == hours
