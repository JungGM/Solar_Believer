"""Read-only portfolio dashboard. Missing generation is never replaced with zero."""

import calendar
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .generation_policy import model_generation, POLICY_VERSION
from .models import ModelVersion, Observation, Plant, Prediction, VppMembership, VppPortfolio, VppSettlement

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])
KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


def aware(value):
    # PostgreSQL preserves timezone; SQLite test storage is normalized to UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def period_bounds(period, selected):
    if not 2000 <= selected.year <= 2100:
        raise ValueError("Select a year between 2000 and 2100")
    if period == "year":
        start = datetime(selected.year, 1, 1, tzinfo=KST)
        end = datetime(selected.year + 1, 1, 1, tzinfo=KST)
    elif period == "month":
        start = datetime(selected.year, selected.month, 1, tzinfo=KST)
        end = start + timedelta(days=calendar.monthrange(selected.year, selected.month)[1])
    else:
        start = datetime.combine(selected, datetime.min.time(), tzinfo=KST)
        end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def model_info(model):
    return {"id": model.id, "version": model.version, "status": model.status, "active": model.active}


def real_options(session):
    portfolios = session.scalars(
        select(VppPortfolio).where(~VppPortfolio.is_demo).order_by(VppPortfolio.id)
    ).all()
    models = session.scalars(
        select(ModelVersion).order_by(ModelVersion.active.desc(), ModelVersion.id.desc())
    ).all()
    rules = session.scalars(
        select(VppSettlement.rule_version).distinct().order_by(VppSettlement.rule_version)
    ).all()
    return {
        "portfolios": [
            {
                "id": p.id,
                "name": p.name,
                "version": p.version,
                "target_capacity_kw": float(p.target_capacity_kw),
            }
            for p in portfolios
        ],
        "models": [model_info(m) for m in models],
        "settlement_rules": rules,
        "is_demo": False,
    }


def load_real(session, start, end, portfolio_id, model_id, rule, prediction_policy="latest"):
    options = real_options(session)
    if portfolio_id is not None and portfolio_id not in [p["id"] for p in options["portfolios"]]:
        raise ValueError("Unknown portfolio")
    if model_id is None and options["models"]:
        model_id = options["models"][0]["id"]
    if model_id is not None and model_id not in [m["id"] for m in options["models"]]:
        raise ValueError("Unknown model version")
    if rule is not None and rule not in options["settlement_rules"]:
        raise ValueError("Unknown settlement rule version")
    # Never silently combine different monetary rule versions.
    if rule is None and len(options["settlement_rules"]) == 1:
        rule = options["settlement_rules"][0]
    portfolios = [p for p in options["portfolios"] if portfolio_id is None or p["id"] == portfolio_id]
    ids = [p["id"] for p in portfolios]
    members = session.scalars(
        select(VppMembership).where(
            VppMembership.portfolio_id.in_(ids),
            VppMembership.valid_from < end,
            (VppMembership.valid_to.is_(None) | (VppMembership.valid_to > start)),
        )
    ).all()
    plant_ids = {m.plant_id for m in members}
    plants = session.scalars(select(Plant).where(Plant.id.in_(plant_ids), ~Plant.is_demo)).all()
    plant_map = {p.id: p for p in plants}
    now = datetime.now(UTC)
    actual, predicted, prices, settlements = {}, {}, {}, {}
    # Canonical source is registered per plant. Revisions use latest known availability.
    observations = session.scalars(
        select(Observation)
        .where(
            ~Observation.is_demo,
            Observation.observed_at >= start,
            Observation.observed_at < end,
            Observation.available_at <= now,
            (Observation.scope_id.in_(plant_ids) | Observation.scope_id.in_(["market:land", "market:jeju"])),
        )
        .order_by(Observation.available_at, Observation.id)
    ).all()
    for row in observations:
        timestamp = aware(row.observed_at)
        if row.kind == "generation" and row.scope_id in plant_map and timestamp < now:
            plant = plant_map[row.scope_id]
            value = number(row.values.get("generation_kwh"))
            if row.source == plant.generation_source and value is not None and value >= 0:
                actual[(plant.id, timestamp)] = value
        elif row.kind == "market" and row.source == "kpx_smp":
            value = number(row.values.get("smp_krw_kwh"))
            if value is not None:
                prices[(row.scope_id.removeprefix("market:"), timestamp)] = value
    prediction_times = {}
    if model_id is not None:
        rows = session.scalars(
            select(Prediction)
            .where(
                Prediction.model_version_id == model_id,
                Prediction.scope_id.in_(plant_ids),
                Prediction.target_at >= start,
                Prediction.target_at < end,
                Prediction.cutoff_at <= Prediction.target_at,
                Prediction.cutoff_at <= now,
            )
            .order_by(Prediction.cutoff_at, Prediction.id)
        ).all()
        for row in rows:
            if prediction_policy == "day_ahead":
                # Service comparison convention, not an official submission deadline.
                target_day = aware(row.target_at).astimezone(KST).date()
                deadline = datetime.combine(target_day - timedelta(days=1), datetime.min.time(), KST)
                deadline += timedelta(hours=18)
                if aware(row.cutoff_at) > deadline or aware(row.created_at) > min(deadline, now):
                    continue
            value = number(row.generation_kwh)
            if value is not None and value >= 0:
                predicted[(row.scope_id, aware(row.target_at))] = value
                prediction_times[(row.scope_id, aware(row.target_at))] = aware(row.created_at)
        if rule:
            rows = session.scalars(
                select(VppSettlement).where(
                    VppSettlement.portfolio_id.in_(ids),
                    VppSettlement.model_version_id == model_id,
                    VppSettlement.rule_version == rule,
                    VppSettlement.target_at >= start,
                    VppSettlement.target_at < end,
                )
            ).all()
            for row in rows:
                settlements[(row.portfolio_id, aware(row.target_at))] = {
                    "amount": float(row.amount_krw),
                    "loss": number(row.loss_krw),
                    "simulation": row.is_simulation,
                }
    return {
        "options": options,
        "portfolios": portfolios,
        "model_id": model_id,
        "rule": rule,
        "plants": [
            {
                "id": p.id,
                "name": p.name,
                "capacity_kw": float(p.capacity_kw),
                "latitude": p.latitude,
                "longitude": p.longitude,
                "smp_zone": p.smp_zone,
            }
            for p in plants
        ],
        "members": [
            {
                "plant_id": m.plant_id,
                "portfolio_id": m.portfolio_id,
                "from": aware(m.valid_from),
                "to": aware(m.valid_to) if m.valid_to else None,
            }
            for m in members
            if m.plant_id in plant_map
        ],
        "actual": actual,
        "predicted": predicted,
        "prices": prices,
        "settlements": settlements,
        "is_demo": False,
        "prediction_generated_at": max(prediction_times.values()).isoformat() if prediction_times else None,
    }


def demo_options():
    return {
        "portfolios": [
            {"id": 1, "name": "서해 포트폴리오", "version": "DEMO-01", "target_capacity_kw": 20000},
            {"id": 2, "name": "남부 포트폴리오", "version": "DEMO-01", "target_capacity_kw": 20000},
        ],
        "models": [
            {"id": 1, "version": "DEMO · 기준 모델", "status": "demo", "active": False},
            {"id": 2, "version": "DEMO · 비교 모델", "status": "demo", "active": False},
        ],
        "settlement_rules": [],
        "is_demo": True,
    }


def load_demo(start, end, portfolio_id, model_id, rule):
    options = demo_options()
    model_id = model_id or 1
    if model_id not in (1, 2) or portfolio_id not in (None, 1, 2) or rule:
        raise ValueError("Unknown demo selection")
    coords = [
        (126.55, 36.80),
        (126.77, 36.13),
        (126.68, 35.92),
        (127.05, 36.38),
        (126.71, 34.81),
        (127.12, 35.08),
        (128.13, 35.17),
        (128.52, 35.68),
    ]
    portfolios = [p for p in options["portfolios"] if portfolio_id is None or p["id"] == portfolio_id]
    ids = {p["id"] for p in portfolios}
    plants, members, actual, predicted, prices = [], [], {}, {}, {}
    capacities = [5500, 4500, 6000, 4000] * 2
    for index, (lon, lat) in enumerate(coords):
        group = 1 if index < 4 else 2
        if group not in ids:
            continue
        plant_id = f"demo:plant:{index + 1}"
        plants.append(
            {
                "id": plant_id,
                "name": f"가상 발전소 {'A' if group == 1 else 'B'}-{index % 4 + 1}",
                "capacity_kw": capacities[index],
                "latitude": lat,
                "longitude": lon,
                "smp_zone": "land",
            }
        )
        members.append({"plant_id": plant_id, "portfolio_id": group, "from": start, "to": end})
    timestamp = start
    while timestamp < end:
        local = timestamp.astimezone(KST)
        solar = max(0, math.sin(math.pi * (local.hour - 6) / 12)) if 6 <= local.hour <= 18 else 0
        cloud = 0.67 + 0.16 * math.sin(local.timetuple().tm_yday * 0.63)
        for plant in plants:
            index = int(plant["id"].rsplit(":", 1)[1]) - 1
            output = plant["capacity_kw"] * solar * cloud * (0.96 + index * 0.006)
            actual[(plant["id"], timestamp)] = round(output, 3)
            factor = (0.95 if model_id == 1 else 1.025) + 0.06 * math.sin(local.hour + index)
            predicted[(plant["id"], timestamp)] = round(output * factor, 3)
        prices[("land", timestamp)] = round(105 + 13 * math.sin((local.hour - 8) / 4) + local.day % 7, 2)
        timestamp += timedelta(hours=1)
    return {
        "options": options,
        "portfolios": portfolios,
        "plants": plants,
        "members": members,
        "model_id": model_id,
        "rule": None,
        "actual": actual,
        "predicted": predicted,
        "prices": prices,
        "settlements": {},
        "is_demo": True,
    }


def total(values):
    values = [v for v in values if v is not None]
    return round(sum(values), 4) if values else None


def average(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def aggregate(snapshot, period, selected, start, end):
    snapshot = dict(snapshot)
    for field in ("actual", "predicted"):
        snapshot[field] = {key: model_generation(value, key[1]) for key, value in snapshot[field].items()}
    plants = {p["id"]: p.copy() for p in snapshot["plants"]}
    plant_rows = {
        p: {"actual": [], "predicted": [], "errors": [], "expected": 0, "groups": set()} for p in plants
    }
    hours, buckets, duplicate_hours = [], defaultdict(list), 0
    timestamp = start
    while timestamp < end:
        memberships = [
            m
            for m in snapshot["members"]
            if m["from"] <= timestamp and (m["to"] is None or timestamp < m["to"])
        ]
        groups_by_plant, plants_by_group = defaultdict(set), defaultdict(set)
        for membership in memberships:
            groups_by_plant[membership["plant_id"]].add(membership["portfolio_id"])
            plants_by_group[membership["portfolio_id"]].add(membership["plant_id"])
        active = set(groups_by_plant)
        portfolio_ids = {m["portfolio_id"] for m in memberships}
        if len(memberships) > len(active):
            duplicate_hours += 1
        expected = len(active)
        actual = [snapshot["actual"].get((p, timestamp)) for p in active]
        predicted = [snapshot["predicted"].get((p, timestamp)) for p in active]
        a = sum(actual) if expected and all(v is not None for v in actual) else None
        p = sum(predicted) if expected and all(v is not None for v in predicted) else None
        error = None
        individual_error = None
        if a is not None and p is not None and len(memberships) == len(active):
            individual_error = sum(abs(snapshot["predicted"][(plant, timestamp)]
                                       - snapshot["actual"][(plant, timestamp)]) for plant in active)
            # Net within each VPP first; independent VPPs cannot cancel each other.
            error = sum(
                abs(
                    sum(snapshot["predicted"][(plant, timestamp)] for plant in group_plants)
                    - sum(snapshot["actual"][(plant, timestamp)] for plant in group_plants)
                )
                for group_plants in plants_by_group.values()
            )
        zones = {plants[p]["smp_zone"] for p in active}
        results = [snapshot["settlements"].get((group, timestamp)) for group in portfolio_ids]
        complete_money = (
            bool(results) and all(v is not None for v in results) and not (len(memberships) > len(active))
        )
        row = {
            "timestamp": timestamp.astimezone(KST).isoformat(),
            "expected": bool(expected),
            "actual_kwh": a,
            "predicted_kwh": p,
            "absolute_error_kwh": error,
            "individual_absolute_error_kwh": individual_error,
            "smp_land": snapshot["prices"].get(("land", timestamp)) if "land" in zones else None,
            "smp_jeju": snapshot["prices"].get(("jeju", timestamp)) if "jeju" in zones else None,
            "settlement_krw": sum(r["amount"] for r in results) if complete_money else None,
            "loss_krw": sum(r["loss"] for r in results)
            if complete_money and all(r["loss"] is not None for r in results)
            else None,
        }
        for plant_id in active:
            entry = plant_rows[plant_id]
            entry["expected"] += 1
            entry["groups"].update(groups_by_plant[plant_id])
            actual_value = snapshot["actual"].get((plant_id, timestamp))
            predicted_value = snapshot["predicted"].get((plant_id, timestamp))
            entry["actual"].append(actual_value)
            entry["predicted"].append(predicted_value)
            if actual_value is not None and predicted_value is not None:
                entry["errors"].append(abs(predicted_value - actual_value))
        local = timestamp.astimezone(KST)
        key = local.strftime("%H:00" if period == "day" else "%d일" if period == "month" else "%m월")
        buckets[key].append(row)
        hours.append(row)
        timestamp += timedelta(hours=1)
    series = []
    energy_fields = (
        "actual_kwh", "predicted_kwh", "absolute_error_kwh",
        "individual_absolute_error_kwh", "settlement_krw", "loss_krw",
    )
    for label, rows in buckets.items():
        expected_rows = [r for r in rows if r["expected"]]
        item = {"label": label, "timestamp": rows[0]["timestamp"], "expected_hours": len(expected_rows)}
        for field in energy_fields:
            values = [r[field] for r in expected_rows]
            item[field] = total(values) if values and all(v is not None for v in values) else None
        for field in ("smp_land", "smp_jeju"):
            item[field] = average(r[field] for r in rows)
        item["paired_hours"] = sum(r["absolute_error_kwh"] is not None for r in rows)
        series.append(item)
    details = []
    for plant_id, plant in plants.items():
        entry = plant_rows[plant_id]
        details.append(
            {
                **plant,
                "portfolio_ids": sorted(entry["groups"]),
                "actual_kwh": total(entry["actual"]),
                "predicted_kwh": total(entry["predicted"]),
                "mae_kwh": average(entry["errors"]),
                "expected_hours": entry["expected"],
                "actual_hours": sum(v is not None for v in entry["actual"]),
                "predicted_hours": sum(v is not None for v in entry["predicted"]),
            }
        )
    expected_hours = sum(r["expected"] for r in hours)
    warnings = []
    if duplicate_hours:
        warnings.append(
            "구성원 중복 적용 구간이 있습니다. 발전량은 발전소별 1회 집계하며 해당 구간 오차와 금액은 표시하지 않습니다."
        )
    unmapped = sum(p["latitude"] is None or p["longitude"] is None for p in details)
    if unmapped:
        warnings.append(f"좌표 미등록 발전소 {unmapped}개는 지도에서 제외되고 표와 집계에는 포함됩니다.")
    if expected_hours and any(
        r["expected"] and (r["actual_kwh"] is None or r["predicted_kwh"] is None) for r in hours
    ):
        warnings.append(
            "누락된 실측·예측을 0으로 채우지 않습니다. 불완전한 일·월 합계는 그래프에 빈 구간으로 표시합니다."
        )
    monetary_rows = list(snapshot["settlements"].values())
    return {
        "is_demo": snapshot["is_demo"],
        "period": period,
        "selected_date": selected.isoformat(),
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": "Asia/Seoul",
        "model_version_id": snapshot["model_id"],
        "portfolios": snapshot["portfolios"],
        "plants": details,
        "series": series,
        "warnings": warnings,
        "prediction_policy": "대상 시각까지 생성된 최신 예측 · 모델별 동일 선택 정책",
        "generation_policy": POLICY_VERSION,
        "error_policy": "VPP 내 합산 후 절대오차 계산 · 여러 VPP는 절대오차를 합산",
        "settlement": {
            "rule_version": snapshot["rule"],
            "status": "available"
            if monetary_rows
            else "awaiting_results"
            if snapshot["rule"]
            else "unconfigured",
            "is_simulation": all(r["simulation"] for r in monetary_rows) if monetary_rows else None,
        },
        "summary": {
            "plant_count": len(details),
            "capacity_kw": sum(p["capacity_kw"] for p in details),
            "portfolio_count": len(snapshot["portfolios"]),
            "predicted_kwh": total(r["predicted_kwh"] for r in hours),
            "actual_kwh": total(r["actual_kwh"] for r in hours),
            "mae_kwh": average(r["absolute_error_kwh"] for r in hours),
            "individual_mae_kwh": average(r["individual_absolute_error_kwh"] for r in hours),
            "expected_hours": expected_hours,
            "predicted_hours": sum(r["predicted_kwh"] is not None for r in hours),
            "actual_hours": sum(r["actual_kwh"] is not None for r in hours),
            "paired_hours": sum(r["absolute_error_kwh"] is not None for r in hours),
            "smp_land": average(r["smp_land"] for r in hours),
            "smp_jeju": average(r["smp_jeju"] for r in hours),
            "settlement_krw": total(r["settlement_krw"] for r in hours),
            "loss_krw": total(r["loss_krw"] for r in hours),
        },
    }


@router.get("/options")
def options(demo: bool = False, session: Session = Depends(get_session)):
    return demo_options() if demo else real_options(session)


@router.get("")
def dashboard(
    period: Literal["day", "month", "year"] = "day",
    selected_date: date | None = Query(default=None, alias="date"),
    portfolio_id: int | None = None,
    model_version_id: int | None = None,
    settlement_rule_version: str | None = Query(default=None, max_length=100),
    demo: bool = False,
    prediction_policy: Literal["latest", "day_ahead"] = "latest",
    session: Session = Depends(get_session),
):
    selected = selected_date or datetime.now(KST).date()
    try:
        start, end = period_bounds(period, selected)
        snapshot = (
            load_demo(start, end, portfolio_id, model_version_id, settlement_rule_version)
            if demo
            else load_real(
                session, start, end, portfolio_id, model_version_id,
                settlement_rule_version, prediction_policy,
            )
        )
        result = aggregate(snapshot, period, selected, start, end)
        result["prediction_generated_at"] = snapshot.get("prediction_generated_at")
        result["prediction_selection"] = prediction_policy
        if prediction_policy == "day_ahead":
            result["prediction_policy"] = "전일 18:00 KST까지 생성·기준 시각이 확인된 예측 · 서비스 비교 기준"
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
