"""Routers de enfrentamientos: /projection, /head-to-head, /upcoming-matchups.

Consume exclusivamente `ScoutingRepository` (`get_upcoming_matchups`,
`get_games_for_team`, `get_rating_trend`). No depende del modelo viejo
(`models.*`/`services.*`).
"""
from fastapi import APIRouter, Depends, Path

from packages.baskonia_core.db.scouting.repository import ScoutingRepository
from packages.baskonia_core.errors import TeamNotFound

from .. import mappers
from ..deps import get_repository, get_team_id, season_label_param
from ..schemas.matchups import (
    HeadToHeadResponse,
    Projection,
    ProjectionResponse,
    UpcomingMatchup,
)
from ..schemas.teams import TeamRef

router = APIRouter(tags=["matchups"])


def _team_exists(repo: ScoutingRepository, team_id: str) -> bool:
    """Comprueba si existe un equipo con el id dado."""
    from sqlalchemy import text

    with repo._engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM teams WHERE id = :tid"), {"tid": team_id}
        ).first()
    return row is not None


def _team_name(repo: ScoutingRepository, team_id: str) -> str | None:
    """Devuelve el nombre de un equipo (o None si no existe)."""
    from sqlalchemy import text

    with repo._engine.connect() as conn:
        return conn.execute(
            text("SELECT name FROM teams WHERE id = :tid"), {"tid": team_id}
        ).scalar_one_or_none()


@router.get("/upcoming-matchups", response_model=list[UpcomingMatchup])
def list_upcoming_matchups(
    repo: ScoutingRepository = Depends(get_repository),
) -> list[UpcomingMatchup]:
    """Próximos rivales, ordenados por fecha de partido."""
    rows = repo.get_upcoming_matchups()
    return [mappers.upcoming_matchup(r) for r in rows]


@router.get(
    "/teams/{team_id}/matchups/{opponent_id}/projection",
    response_model=ProjectionResponse,
)
def get_projection(
    team_id: str = Depends(get_team_id),
    opponent_id: str = Path(..., description="Id TEXT del rival (p.ej. 'rm')"),
    repo: ScoutingRepository = Depends(get_repository),
    season_label: str | None = Depends(season_label_param),
) -> ProjectionResponse:
    """Proyección de marcador esperado entre dos equipos.

    Derivada de las tendencias ORtg/DRtg de ambos equipos y del próximo
    enfrentamiento registrado en `upcoming_matchups`. Si no hay datos
    suficientes, `projection` es null (degradado, no inventa).
    """
    if not _team_exists(repo, team_id):
        raise TeamNotFound(team_id)
    if not _team_exists(repo, opponent_id):
        raise TeamNotFound(opponent_id)

    team_trend = repo.get_rating_trend(team_id=team_id, last_n=8)
    opp_trend = repo.get_rating_trend(team_id=opponent_id, last_n=8)

    def _avg(rows, key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    team_ortg = _avg(team_trend, "ortg")
    team_drtg = _avg(team_trend, "drtg")
    opp_ortg = _avg(opp_trend, "ortg")
    opp_drtg = _avg(opp_trend, "drtg")

    projection = None
    if team_ortg is not None and opp_drtg is not None and team_drtg is not None and opp_ortg is not None:
        predicted_net = round((team_ortg - opp_drtg + opp_ortg - team_drtg) / 2, 2)
        projection = Projection(
            predicted_net_rating=predicted_net,
            predicted_pace=None,
            predicted_ortg=round((team_ortg + opp_drtg) / 2, 2),
            expected_margin=predicted_net,
        )

    return ProjectionResponse(
        team=TeamRef(id=team_id, name=_team_name(repo, team_id) or team_id),
        opponent=TeamRef(id=opponent_id, name=_team_name(repo, opponent_id) or opponent_id),
        projection=projection,
    )


@router.get(
    "/teams/{team_id}/matchups/{opponent_id}/head-to-head",
    response_model=HeadToHeadResponse,
)
def get_head_to_head(
    team_id: str = Depends(get_team_id),
    opponent_id: str = Path(..., description="Id TEXT del rival (p.ej. 'rm')"),
    repo: ScoutingRepository = Depends(get_repository),
    season_label: str | None = Depends(season_label_param),
) -> HeadToHeadResponse:
    """Enfrentamientos directos entre dos equipos."""
    if not _team_exists(repo, team_id):
        raise TeamNotFound(team_id)
    if not _team_exists(repo, opponent_id):
        raise TeamNotFound(opponent_id)

    team_games = repo.get_games_for_team(team_id, season_label=season_label)
    h2h = [
        g for g in team_games
        if g["home_team_id"] == opponent_id or g["away_team_id"] == opponent_id
    ]
    return HeadToHeadResponse(
        team=TeamRef(id=team_id, name=_team_name(repo, team_id) or team_id),
        opponent=TeamRef(id=opponent_id, name=_team_name(repo, opponent_id) or opponent_id),
        items=[mappers.h2h_game(g, team_id) for g in h2h],
    )
