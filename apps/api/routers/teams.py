"""Routers de equipos: /teams, /teams/{slug}, /filters, /summary."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from packages.baskonia_core import insights
from packages.baskonia_core.db import models
from packages.baskonia_core.services.calendar import past_games, upcoming_games

from .. import mappers
from ..deps import get_session, get_team, league_param, season_param
from ..schemas.teams import (
    AdvancedSummary,
    FiltersResponse,
    LeagueOption,
    SummaryResponse,
    TeamResponse,
)

router = APIRouter(tags=["teams"])


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(session: Session = Depends(get_session)) -> list[TeamResponse]:
    """Lista todos los equipos conocidos (endpoint 3)."""
    teams = session.query(models.Team).order_by(models.Team.name).all()
    return [
        TeamResponse(slug=t.slug, name=t.name, league=t.league)
        for t in teams
    ]


@router.get("/teams/{slug}", response_model=TeamResponse)
def get_team_detail(team: models.Team = Depends(get_team)) -> TeamResponse:
    """Detalle de un equipo por slug (endpoint 4)."""
    return TeamResponse(slug=team.slug, name=team.name, league=team.league)


@router.get("/teams/{slug}/filters", response_model=FiltersResponse)
def get_filters(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
) -> FiltersResponse:
    """Filtros disponibles para un equipo: temporadas y competiciones (endpoint 5)."""
    seasons = insights.list_seasons(session, team)
    leagues = insights.list_leagues(session, team)
    return FiltersResponse(
        seasons=seasons,
        default_season=insights.current_season(session, team),
        leagues=[
            LeagueOption(code=lg, label=insights.league_label(lg))
            for lg in leagues
        ],
    )


@router.get("/teams/{slug}/summary", response_model=SummaryResponse)
def get_summary(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int | None = Depends(season_param),
    league: str | None = Depends(league_param),
) -> SummaryResponse:
    """Resumen del equipo: identidad, filtros y medias avanzadas (endpoint 6)."""
    advanced = insights.team_advanced_summary(session, team, season=season, league=league)
    return SummaryResponse(
        team=mappers.team_ref(team),
        filters={"season": season, "league": league},
        advanced=AdvancedSummary(**advanced),
        games_played=len(past_games(session, team, season=season, league=league)),
        games_upcoming=len(upcoming_games(session, team)),
    )
