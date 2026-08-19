"""Routers de enfrentamientos: /schedule-difficulty, /narrative, /projection, /head-to-head."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from packages.baskonia_core import insights
from packages.baskonia_core.db import models
from packages.baskonia_core.services.calendar import upcoming_games
from packages.baskonia_core.services.matchup import head_to_head_games

from .. import mappers
from ..deps import get_opponent, get_session, get_team, league_param, season_param
from ..schemas.matchups import (
    HeadToHeadResponse,
    NarrativeResponse,
    Projection,
    ProjectionResponse,
    ScheduleDifficultyResponse,
)

router = APIRouter(tags=["matchups"])


@router.get("/teams/{slug}/schedule-difficulty", response_model=ScheduleDifficultyResponse)
def get_schedule_difficulty(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int | None = Depends(season_param),
    league: str | None = Depends(league_param),
    next_n: int = Query(5, ge=1, le=20),
) -> ScheduleDifficultyResponse:
    """Dificultad del próximo tramo de calendario (endpoint 12)."""
    if season is None:
        season = insights.current_season(session, team) or 0
    result = insights.schedule_difficulty(
        session, team, upcoming_games(session, team),
        season=season, next_n=next_n, league=league,
    )
    return ScheduleDifficultyResponse(
        games_considered=result["games_considered"],
        opponents_scouted=result["opponents_scouted"],
        avg_opponent_net_rating=result["avg_opponent_net_rating"],
        league=result["league"],
        opponents=[mappers.difficulty_opponent(o) for o in result["opponents"]],
    )


@router.get("/teams/{slug}/narrative", response_model=NarrativeResponse)
def get_narrative(
    team: models.Team = Depends(get_team),
    session: Session = Depends(get_session),
    season: int = Depends(season_param),
    league: str | None = Depends(league_param),
    recent_n: int = Query(5, ge=1, le=20),
) -> NarrativeResponse:
    """Narrativa de scouting en español (endpoint 13)."""
    if season is None:
        season = insights.current_season(session, team) or 0
    narrative = insights.scouting_narrative(
        session, team, season=season, recent_n=recent_n, league=league,
    )
    return NarrativeResponse(
        season=season,
        league=league,
        recent_n=recent_n,
        narrative=narrative,
    )


@router.get("/teams/{slug}/matchups/{opponent_slug}/projection", response_model=ProjectionResponse)
def get_projection(
    team: models.Team = Depends(get_team),
    opponent: models.Team = Depends(get_opponent),
    session: Session = Depends(get_session),
    season: int = Depends(season_param),
    league: str | None = Depends(league_param),
) -> ProjectionResponse:
    """Proyección de marcador entre dos equipos (endpoint 14)."""
    if season is None:
        season = insights.current_season(session, team) or 0
    proj = insights.project_next_matchup(
        session, team, opponent, season=season, league=league,
    )
    projection = None
    if proj is not None:
        projection = Projection(
            projected_possessions=proj["projected_possessions"],
            team_projected_rating=proj["team_projected_rating"],
            opp_projected_rating=proj["opp_projected_rating"],
            team_projected_score=proj["team_projected_score"],
            opp_projected_score=proj["opp_projected_score"],
            expected_margin=proj["team_projected_score"] - proj["opp_projected_score"],
        )
    return ProjectionResponse(
        team=mappers.team_ref(team),
        opponent=mappers.team_ref(opponent),
        season=season,
        projection=projection,
    )


@router.get("/teams/{slug}/matchups/{opponent_slug}/head-to-head", response_model=HeadToHeadResponse)
def get_head_to_head(
    team: models.Team = Depends(get_team),
    opponent: models.Team = Depends(get_opponent),
    session: Session = Depends(get_session),
    season: int | None = Depends(season_param),
    league: str | None = Depends(league_param),
) -> HeadToHeadResponse:
    """Enfrentamientos directos entre dos equipos (endpoint 15)."""
    games = head_to_head_games(session, team, opponent, season=season, league=league)
    return HeadToHeadResponse(
        team=mappers.team_ref(team),
        opponent=mappers.team_ref(opponent),
        items=[mappers.h2h_game(g, team) for g in games],
    )
