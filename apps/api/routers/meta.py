"""Routers de meta: /health y /meta/data-freshness."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from packages.baskonia_core.db import models

from ..deps import get_session
from ..schemas.meta import DataFreshnessResponse, HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Estado de salud de la API (endpoint 1)."""
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/meta/data-freshness", response_model=DataFreshnessResponse)
def data_freshness(session: Session = Depends(get_session)) -> DataFreshnessResponse:
    """Frescura de los datos: última fecha de partido y recuentos (endpoint 2)."""
    last_game = (
        session.query(models.Game).order_by(models.Game.id.desc()).first()
    )
    return DataFreshnessResponse(
        last_game_date=last_game.date if last_game else None,
        games_total=session.query(models.Game).count(),
        boxscores_total=session.query(models.BoxScore).count(),
        teams_total=session.query(models.Team).count(),
    )
