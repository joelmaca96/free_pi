"""Routers de partidos: /games/{game_id} y /games/{game_id}/boxscore.

Consume exclusivamente `ScoutingRepository` (`get_game_detail`,
`get_game_boxscore`). No depende del modelo viejo (`models.*`/`services.*`).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from packages.baskonia_core.db.scouting.repository import ScoutingRepository
from packages.baskonia_core.errors import GameNotFound

from .. import mappers
from ..deps import get_game_id, get_repository
from ..schemas.games import (
    BoxScoreResponse,
    GameDetailResponse,
)

router = APIRouter(tags=["games"])


@router.get("/games/{game_id}", response_model=GameDetailResponse)
def get_game_detail(
    game_id: str = Depends(get_game_id),
    repo: ScoutingRepository = Depends(get_repository),
) -> GameDetailResponse:
    """Detalle completo de un partido (resultado, advanced, lineups, zonas, eventos)."""
    detail = repo.get_game_detail(game_id)
    if detail is None:
        raise GameNotFound(game_id)

    baskonia = None
    if detail.get("baskonia") is not None:
        b = detail["baskonia"]
        baskonia = {
            "is_home": b["is_home"],
            "opponent_id": b["opponent_id"],
            "opponent_name": b["opponent_name"],
            "score_for": b["score_for"],
            "score_against": b["score_against"],
        }

    season_label = detail.get("season_label")
    if season_label is None and detail.get("season_id") is not None:
        with repo._engine.connect() as conn:
            season_label = conn.execute(
                text("SELECT label FROM seasons WHERE id = :sid"),
                {"sid": detail["season_id"]},
            ).scalar_one_or_none()

    return GameDetailResponse(
        id=detail["id"],
        season_label=season_label or "",
        competition_name=detail["competition_name"],
        home_team=mappers.team_ref(detail["home_team_id"], detail["home_team_name"]),
        away_team=mappers.team_ref(detail["away_team_id"], detail["away_team_name"]),
        game_date=detail["game_date"],
        home_score=detail["home_score"],
        away_score=detail["away_score"],
        pace=detail.get("pace"),
        narrative=detail.get("narrative"),
        baskonia=baskonia,
        advanced=[mappers.game_advanced(a) for a in detail["advanced"]],
        lineups=[mappers.lineup(l) for l in detail["lineups"]],
        zone_stats=[mappers.zone_stat(z) for z in detail["zone_stats"]],
        key_events=[mappers.key_event(e) for e in detail["key_events"]],
    )


@router.get("/games/{game_id}/boxscore", response_model=BoxScoreResponse)
def get_boxscore(
    game_id: str = Depends(get_game_id),
    repo: ScoutingRepository = Depends(get_repository),
) -> BoxScoreResponse:
    """Box score de un partido (filas de ambos equipos, sin filtrar por equipo)."""
    if repo.get_game_detail(game_id) is None:
        raise GameNotFound(game_id)
    rows = repo.get_game_boxscore(game_id)
    return BoxScoreResponse(
        game_id=game_id,
        rows=[mappers.boxscore_row(r) for r in rows],
    )
