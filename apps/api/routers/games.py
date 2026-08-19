"""Routers de partidos: /teams/{slug}/games y /games/{id}/boxscore."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from packages.baskonia_core.db import models
from packages.baskonia_core.errors import GameNotFound, TeamNotFound
from packages.baskonia_core.services.boxscore import _team_stats_for_game, boxscore_rows
from packages.baskonia_core.services.calendar import past_games, upcoming_games
from packages.baskonia_core.services.roster import team_by_slug

from .. import mappers
from ..deps import get_session, get_team, league_param, season_param
from ..schemas.games import (
    BoxScoreResponse,
    BoxScoreRow,
    GameAdvanced,
    GamesResponse,
)

router = APIRouter(tags=["games"])


@router.get("/teams/{slug}/games", response_model=GamesResponse)
def list_games(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int | None = Depends(season_param),
    league: str | None = Depends(league_param),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> GamesResponse:
    """Partidos de un equipo (jugados y pendientes), paginados (endpoint 7)."""
    played = past_games(session, team, season=season, league=league)
    pending = upcoming_games(session, team)
    if league is not None:
        pending = [g for g in pending if g.league == league]
    if season is not None:
        pending = [g for g in pending if mappers._iso_date(g.date) is not None]

    items = []
    for game in played + pending:
        item = mappers.game_item(game, team)
        stats = _team_stats_for_game(session, game.id, team.id)
        if stats is not None:
            item.advanced = GameAdvanced(
                pace=stats.pace,
                off_rating=stats.off_rating,
                def_rating=stats.def_rating,
                net_rating=stats.net_rating,
            )
        items.append(item)

    total = len(items)
    return GamesResponse(
        items=items[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/games/{game_id}/boxscore", response_model=BoxScoreResponse)
def get_boxscore(
    game_id: int,
    session: Session = Depends(get_session),
    team_slug: str = Query(..., description="Slug del equipo cuyo box score se pide"),
) -> BoxScoreResponse:
    """Box score de un equipo en un partido (endpoint 16).

    `team_slug` es un query param obligatorio (no va en la ruta).
    """
    team = team_by_slug(session, team_slug)
    if team is None:
        raise TeamNotFound(team_slug)
    game = session.query(models.Game).filter_by(id=game_id).first()
    if game is None:
        raise GameNotFound(game_id)

    is_home = game.home_team_id == team.id
    opponent = game.away_team if is_home else game.home_team
    team_score = game.home_score if is_home else game.away_score
    opp_score = game.away_score if is_home else game.home_score

    rows = []
    for row in boxscore_rows(session, game_id, team.id):
        rows.append(
            BoxScoreRow(
                player_name=row.player_name,
                minutes=row.minutes,
                points=row.points,
                rebounds=row.rebounds,
                assists=row.assists,
                steals=row.steals,
                blocks=row.blocks,
                turnovers=row.turnovers,
                fg_made=row.fg_made,
                fg_attempted=row.fg_attempted,
                fg3_made=row.fg3_made,
                fg3_attempted=row.fg3_attempted,
                ft_made=row.ft_made,
                ft_attempted=row.ft_attempted,
                efg_pct=row.efg_pct,
                ts_pct=row.ts_pct,
            )
        )

    return BoxScoreResponse(
        game_id=game.id,
        team=mappers.team_ref(team),
        opponent=mappers.team_ref(opponent),
        date=mappers._iso_date(game.date) or game.date,
        league=game.league,
        team_score=team_score,
        opponent_score=opp_score,
        result=mappers._result_label(game, team),
        rows=rows,
    )
