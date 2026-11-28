from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .dashboard import router as dashboard_router
from .workspace import router as workspace_router
from .models import JobRun, ModelVersion, Observation, Plant, Prediction, Settlement, TrainingRun
from .schemas import (
    JobRunRead,
    ModelVersionRead,
    ObservationRead,
    PredictionRead,
    SettlementRead,
    TrainingRunRead,
)

app = FastAPI(title="Solar Believer API", version="0.1.0")
app.include_router(dashboard_router)
app.include_router(workspace_router)
DB = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready(session: DB):
    try:
        session.execute(text("SELECT 1"))
        session.execute(select(JobRun.id).limit(1))
        session.execute(select(Plant.id).limit(1))
    except SQLAlchemyError:
        raise HTTPException(503, "Database or schema unavailable") from None
    return {"status": "ready"}


@app.get("/api/v1/summary")
def summary(session: DB):
    return {
        "pipeline_mode": get_settings().pipeline_mode,
        "observations": session.scalar(select(func.count()).select_from(Observation)),
        "demo_observations": session.scalar(
            select(func.count()).select_from(Observation).where(Observation.is_demo)
        ),
        "predictions": session.scalar(select(func.count()).select_from(Prediction)),
        "settlements": session.scalar(select(func.count()).select_from(Settlement)),
        "active_model": session.scalar(select(ModelVersion.version).where(ModelVersion.active)),
        "settlement_rule": "unconfigured",
    }


@app.get("/api/v1/observations", response_model=list[ObservationRead])
def observations(session: DB, limit: Limit = 100, offset: Offset = 0, scope_id: str | None = None):
    query = select(Observation)
    if scope_id:
        query = query.where(Observation.scope_id == scope_id)
    return session.scalars(query.order_by(Observation.id.desc()).offset(offset).limit(limit)).all()


@app.get("/api/v1/predictions", response_model=list[PredictionRead])
def predictions(session: DB, limit: Limit = 100, offset: Offset = 0, scope_id: str | None = None):
    query = select(Prediction)
    if scope_id:
        query = query.where(Prediction.scope_id == scope_id)
    return session.scalars(query.order_by(Prediction.id.desc()).offset(offset).limit(limit)).all()


@app.get("/api/v1/settlements", response_model=list[SettlementRead])
def settlements(session: DB, limit: Limit = 100, offset: Offset = 0):
    return session.scalars(
        select(Settlement).order_by(Settlement.id.desc()).offset(offset).limit(limit)
    ).all()


@app.get("/api/v1/model-versions", response_model=list[ModelVersionRead])
def models(session: DB, limit: Limit = 100, offset: Offset = 0):
    return session.scalars(
        select(ModelVersion).order_by(ModelVersion.id.desc()).offset(offset).limit(limit)
    ).all()


@app.get("/api/v1/training-runs", response_model=list[TrainingRunRead])
def training(session: DB, limit: Limit = 100, offset: Offset = 0):
    return session.scalars(
        select(TrainingRun).order_by(TrainingRun.id.desc()).offset(offset).limit(limit)
    ).all()


@app.get("/api/v1/job-runs", response_model=list[JobRunRead])
def jobs(session: DB, limit: Limit = 100, offset: Offset = 0):
    return session.scalars(select(JobRun).order_by(JobRun.id.desc()).offset(offset).limit(limit)).all()


# Register the SPA catch-all after all API routes.
from .web_assets import router as frontend_router  # noqa: E402

app.include_router(frontend_router)
