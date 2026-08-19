"""Schemas de los endpoints de enfrentamientos (difficulty, projection, h2h, narrative)."""
from pydantic import BaseModel

from .teams import TeamRef


class DifficultyOpponent(BaseModel):
    """Rival considerado en la dificultad de calendario."""

    opponent_name: str
    date: str  # ISO-8601
    net_rating: float | None = None


class ScheduleDifficultyResponse(BaseModel):
    """Dificultad del próximo tramo de calendario."""

    games_considered: int
    opponents_scouted: int
    avg_opponent_net_rating: float | None = None
    league: str | None = None
    opponents: list[DifficultyOpponent]


class Projection(BaseModel):
    """Proyección de marcador esperado entre dos equipos."""

    projected_possessions: float
    team_projected_rating: float
    opp_projected_rating: float
    team_projected_score: float
    opp_projected_score: float
    expected_margin: float


class ProjectionResponse(BaseModel):
    """Proyección de un enfrentamiento (projection puede ser null)."""

    team: TeamRef
    opponent: TeamRef
    season: int
    projection: Projection | None = None


class HeadToHeadGame(BaseModel):
    """Un enfrentamiento directo entre dos equipos."""

    id: int
    date: str  # ISO-8601
    league: str
    team_score: int | None = None
    opponent_score: int | None = None
    result: str | None = None  # "W" | "L" | null


class HeadToHeadResponse(BaseModel):
    """Enfrentamientos directos entre dos equipos."""

    team: TeamRef
    opponent: TeamRef
    items: list[HeadToHeadGame]


class NarrativeResponse(BaseModel):
    """Narrativa de scouting (único campo en español de la API)."""

    season: int
    league: str | None = None
    recent_n: int
    narrative: str | None = None
