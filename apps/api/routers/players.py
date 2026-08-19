"""Routers de jugadores: /roster, /players/form, /streaks, /load."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from packages.baskonia_core import insights
from packages.baskonia_core.db import models
from packages.baskonia_core.services.calendar import games_in_window
from packages.baskonia_core.services.roster import current_roster

from .. import mappers
from ..deps import get_session, get_team, league_param, season_param
from ..schemas.players import (
    LoadResponse,
    PlayerFormResponse,
    RosterPlayer,
    RosterResponse,
    StreaksResponse,
)

router = APIRouter(tags=["players"])


@router.get("/teams/{slug}/roster", response_model=RosterResponse)
def get_roster(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int | None = Depends(season_param),
    league: str | None = Depends(league_param),
) -> RosterResponse:
    """Plantilla actual de un equipo con la forma reciente de cada jugador (endpoint 8)."""
    players = current_roster(session, team)
    form_by_name = {
        r["player_name"]: r
        for r in insights.player_recent_form(session, team, last_n=5, season=season, league=league)
    }
    return RosterResponse(
        team=mappers.team_ref(team),
        players=[
            RosterPlayer(
                name=p.name,
                number=p.number,
                position=p.position,
                photo_url=p.photo_url,
                form=form_by_name.get(p.name),
            )
            for p in players
        ],
    )


@router.get("/teams/{slug}/players/form", response_model=PlayerFormResponse)
def get_player_form(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int | None = Depends(season_param),
    league: str | None = Depends(league_param),
    last_n: int = Query(5, ge=1, le=20),
) -> PlayerFormResponse:
    """Forma reciente por jugador (endpoint 9)."""
    rows = insights.player_recent_form(session, team, last_n=last_n, season=season, league=league)
    return PlayerFormResponse(
        last_n=last_n,
        items=[mappers.form_item(r) for r in rows],
    )


@router.get("/teams/{slug}/players/streaks", response_model=StreaksResponse)
def get_streaks(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int = Depends(season_param),
    league: str | None = Depends(league_param),
    recent_n: int = Query(5, ge=1, le=20),
    min_season_games: int = Query(6, ge=1, le=50),
) -> StreaksResponse:
    """Rachas de los jugadores en una temporada (endpoint 10)."""
    if season is None:
        season = insights.current_season(session, team) or 0
    rows = insights.player_form_zscore(
        session, team, season=season, recent_n=recent_n,
        min_season_games=min_season_games, league=league,
    )
    return StreaksResponse(
        season=season,
        recent_n=recent_n,
        min_season_games=min_season_games,
        items=[mappers.streak_item(r) for r in rows],
    )


@router.get("/teams/{slug}/players/load", response_model=LoadResponse)
def get_load(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    window_days: int = Query(14, ge=1, le=90),
) -> LoadResponse:
    """Carga de minutos por jugador en la ventana de días (endpoint 11)."""
    games = games_in_window(session, team, window_days)
    rows = insights.player_load(session, team, games)
    return LoadResponse(
        window_days=window_days,
        games_in_window=len(games),
        note="Carga transversal a temporada/competición (ventana de días).",
        items=[mappers.load_item(r) for r in rows],
    )
