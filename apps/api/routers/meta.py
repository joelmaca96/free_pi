"""Routers de meta: /health y /meta/data-freshness.

Consulta el esquema de scouting (`ScoutingRepository`) para los recuentos de
frescura de datos. No depende del modelo viejo (`models.*`/`services.*`).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from packages.baskonia_core.db.scouting.repository import ScoutingRepository

from ..deps import get_repository
from ..schemas.meta import DataFreshnessResponse, HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Estado de salud de la API."""
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/meta/data-freshness", response_model=DataFreshnessResponse)
def data_freshness(
    repo: ScoutingRepository = Depends(get_repository),
) -> DataFreshnessResponse:
    """Frescura de los datos: última fecha de partido y recuentos.

    Recuentos directos sobre el esquema de scouting (seasons/competitions/
    teams/players/games/upcoming_matchups).
    """
    engine = repo._engine
    with engine.connect() as conn:
        last_game = conn.execute(
            text("SELECT MAX(game_date) AS last_date FROM games")
        ).scalar_one_or_none()
        games_total = conn.execute(text("SELECT COUNT(*) FROM games")).scalar_one()
        players_total = conn.execute(text("SELECT COUNT(*) FROM players")).scalar_one()
        teams_total = conn.execute(text("SELECT COUNT(*) FROM teams")).scalar_one()
        upcoming_total = conn.execute(
            text("SELECT COUNT(*) FROM upcoming_matchups")
        ).scalar_one()

    return DataFreshnessResponse(
        last_game_date=last_game,
        games_total=games_total,
        players_total=players_total,
        teams_total=teams_total,
        upcoming_matchups_total=upcoming_total,
    )
