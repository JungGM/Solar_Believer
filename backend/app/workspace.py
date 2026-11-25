"""Read-only asset history and weather views; no inferred commissioning/closure dates."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .dashboard import KST, UTC, aware, period_bounds
from .db import get_session
from .models import Observation, Plant, VppMembership, VppPortfolio

router = APIRouter(prefix="/api/v1/workspace", tags=["Workspace"])


@router.get("/plants")
def plants(session: Session = Depends(get_session)):
    now = datetime.now(UTC)
    history = (
        select(
            Observation.scope_id.label("plant_id"),
            Observation.source,
            func.min(Observation.observed_at).label("first_seen"),
            func.max(Observation.observed_at).label("last_seen"),
            func.max(Observation.collected_at).label("last_collected"),
            func.count().label("stored_rows"),
        )
        .where(
            ~Observation.is_demo, Observation.kind == "generation",
            Observation.available_at <= now, Observation.observed_at < now,
        )
        .group_by(Observation.scope_id, Observation.source)
        .subquery()
    )
    rows = session.execute(
        select(Plant, history.c.first_seen, history.c.last_seen,
               history.c.last_collected, history.c.stored_rows)
        .outerjoin(history, and_(history.c.plant_id == Plant.id,
                                 history.c.source == Plant.generation_source))
        .where(~Plant.is_demo).order_by(Plant.name, Plant.id)
    ).all()
    memberships = session.execute(
        select(VppMembership, VppPortfolio.name)
        .join(VppPortfolio, VppMembership.portfolio_id == VppPortfolio.id)
        .where(~VppPortfolio.is_demo).order_by(VppMembership.valid_from.desc())
    ).all()
    grouped = {}
    for member, name in memberships:
        grouped.setdefault(member.plant_id, []).append({
            "portfolio_id": member.portfolio_id, "portfolio_name": name,
            "valid_from": aware(member.valid_from),
            "valid_to": aware(member.valid_to) if member.valid_to else None,
        })
    items = []
    for plant, first, last, collected, count in rows:
        status = "no_data" if last is None else "stale" if aware(last) < now - timedelta(days=7) else "recent"
        items.append({
            "id": plant.id, "name": plant.name, "capacity_kw": float(plant.capacity_kw),
            "latitude": plant.latitude, "longitude": plant.longitude,
            "smp_zone": plant.smp_zone, "source": plant.generation_source,
            "first_seen": aware(first) if first else None,
            "last_seen": aware(last) if last else None,
            "last_collected": aware(collected) if collected else None,
            "stored_rows": count or 0, "status": status,
            "memberships": grouped.get(plant.id, []),
        })
    return {"plants": items, "as_of": now, "stale_after_days": 7,
            "history_policy": "DB에 저장된 발전 실측의 최초·최종 대상 시각입니다. 신규 설비·폐지·용량 변경을 확정하지 않습니다."}


@router.get("/weather")
def weather(
    selected_date: date | None = Query(default=None, alias="date"),
    scope_id: str | None = Query(default=None, max_length=100),
    session: Session = Depends(get_session),
):
    selected = selected_date or datetime.now(KST).date()
    # Same year bounds as the existing dashboard.
    try:
        start, end = period_bounds("day", selected)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    now = datetime.now(UTC)
    kinds = ["weather_forecast", "weather_observation"]
    base = [~Observation.is_demo, Observation.kind.in_(kinds), Observation.available_at <= now]
    scopes = session.scalars(select(Observation.scope_id).where(*base)
                             .distinct().order_by(Observation.scope_id)).all()
    query = select(Observation).where(*base, Observation.observed_at >= start,
                                      Observation.observed_at < end)
    if scope_id:
        query = query.where(Observation.scope_id == scope_id)
    rows = session.scalars(query.order_by(Observation.observed_at,
                                         Observation.scope_id, Observation.id.desc()).limit(501)).all()
    return {
        "date": selected, "scopes": scopes, "truncated": len(rows) > 500,
        "records": [{"id": r.id, "scope_id": r.scope_id, "source": r.source,
                     "kind": r.kind, "target_at": aware(r.observed_at),
                     "issued_at": aware(r.issued_at) if r.issued_at else None,
                     "available_at": aware(r.available_at), "values": r.values}
                    for r in rows[:500]],
        "mapping_status": "발전소·예보 격자·관측소 연결 검증 전입니다. 동일 지점·시간·단위가 확인되기 전에는 오차를 계산하지 않습니다.",
    }
