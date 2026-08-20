"""Router de la cola de scouting bajo demanda: /teams/{slug}/scout, /jobs/{id}.

La API solo inserta/lee filas en `ingest_jobs` (tabla de control, no de
dominio); nunca hace red ni importa `apps.ingest`. El worker
(`apps/ingest/worker.py`) es el único proceso que ejecuta el scraping real.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from packages.baskonia_core.db import models
from packages.baskonia_core.errors import JobNotFound

from .. import mappers
from ..deps import get_session, get_team
from ..schemas.jobs import JobResponse

router = APIRouter(tags=["jobs"])

_ACTIVE_STATUSES = ("queued", "running")


@router.post("/teams/{slug}/scout", response_model=JobResponse, status_code=202)
def enqueue_scout(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    last_n: int = Query(5, ge=1, le=15),
) -> JobResponse:
    """Encola el scouting de `team` (idempotente: si ya hay uno activo, lo devuelve)."""
    existing = (
        session.query(models.IngestJob)
        .filter(models.IngestJob.team_id == team.id, models.IngestJob.status.in_(_ACTIVE_STATUSES))
        .order_by(models.IngestJob.id.desc())
        .first()
    )
    if existing is not None:
        return mappers.job_ref(existing)

    job = models.IngestJob(
        team_id=team.id,
        last_n=last_n,
        status="queued",
        created_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return mappers.job_ref(job)


@router.get("/teams/{slug}/scout", response_model=JobResponse | None)
def get_latest_scout(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
) -> JobResponse | None:
    """Último trabajo de scouting de `team`, o `null` si nunca se ha pedido."""
    job = (
        session.query(models.IngestJob)
        .filter(models.IngestJob.team_id == team.id)
        .order_by(models.IngestJob.id.desc())
        .first()
    )
    return mappers.job_ref(job) if job is not None else None


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int, session: Session = Depends(get_session)) -> JobResponse:
    """Estado de un trabajo de scouting por id (para hacer polling tras encolar)."""
    job = session.query(models.IngestJob).filter_by(id=job_id).first()
    if job is None:
        raise JobNotFound(job_id)
    return mappers.job_ref(job)
