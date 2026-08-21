"""Routers de jugadores: /teams/{team_id}/players/form y /players/load.

Consume exclusivamente `ScoutingRepository` (`get_player_recent_form`,
`get_player_minutes_load`). No depende del modelo viejo
(`models.*`/`services.*`).
"""
from datetime import date

from fastapi import APIRouter, Depends, Query

from packages.baskonia_core.db.scouting.repository import ScoutingRepository
from packages.baskonia_core.errors import TeamNotFound

from .. import mappers
from ..deps import get_repository, get_team_id
from ..schemas.players import LoadResponse, PlayerFormResponse

router = APIRouter(tags=["players"])


def _team_exists(repo: ScoutingRepository, team_id: str) -> bool:
    """Comprueba si existe un equipo con el id dado."""
    from sqlalchemy import text

    with repo._engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM teams WHERE id = :tid"), {"tid": team_id}
        ).first()
    return row is not None


@router.get("/teams/{team_id}/players/form", response_model=PlayerFormResponse)
def get_player_form(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    player_id: str | None = Query(None, description="Id del jugador (opcional)"),
    last_n: int = Query(5, ge=1, le=20),
) -> PlayerFormResponse:
    """Forma reciente de un jugador (o de todos los de la plantilla si no se da id).

    Si `player_id` no se da, devuelve la forma del primer jugador de la
    plantilla del equipo (degradado documentado; la SPA debe pasar `player_id`).
    """
    if not _team_exists(repo, team_id):
        raise TeamNotFound(team_id)
    if player_id is None:
        roster = repo.get_roster(_latest_season(repo))
        if not roster:
            return PlayerFormResponse(player_id="", last_n=last_n, items=[])
        player_id = roster[0]["id"]
    rows = repo.get_player_recent_form(player_id, last_n=last_n)
    return PlayerFormResponse(
        player_id=player_id,
        last_n=last_n,
        items=[mappers.form_item(r) for r in rows],
    )


def _latest_season(repo: ScoutingRepository) -> str | None:
    """Devuelve la etiqueta de la temporada más reciente (o None)."""
    from sqlalchemy import text

    with repo._engine.connect() as conn:
        return conn.execute(
            text("SELECT label FROM seasons ORDER BY id DESC LIMIT 1")
        ).scalar_one_or_none()


@router.get("/teams/{team_id}/players/load", response_model=LoadResponse)
def get_load(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    window_days: int = Query(14, ge=1, le=90),
    as_of: str | None = Query(None, description="Fecha de referencia (ISO-8601)"),
) -> LoadResponse:
    """Carga de minutos por jugador en la ventana de días (transversal)."""
    if not _team_exists(repo, team_id):
        raise TeamNotFound(team_id)
    reference = as_of or date.today().isoformat()
    roster = repo.get_roster(_latest_season(repo)) if _latest_season(repo) else []
    items = []
    for p in roster:
        total = repo.get_player_minutes_load(
            p["id"], days=window_days, as_of=reference
        )
        items.append(mappers.load_item(p["id"], p["name"], total))
    items.sort(key=lambda i: i.total_minutes, reverse=True)
    return LoadResponse(
        window_days=window_days,
        as_of=reference,
        items=items,
    )
