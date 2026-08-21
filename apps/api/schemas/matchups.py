"""Schemas de los endpoints de enfrentamientos (difficulty, projection, h2h, narrative, upcoming)."""
from pydantic import BaseModel

from .teams import TeamRef


class DifficultyOpponent(BaseModel):
    """Rival considerado en la dificultad de calendario."""

    opponent_id: str
    opponent_name: str
    match_date: str  # ISO-8601
    is_home: bool
    predicted_net_rating: float | None = None
    predicted_pace: float | None = None
    predicted_ortg: float | None = None
    has_scouting_data: bool = False
    key_player_note: str | None = None
    h2h_wins: int | None = None
    h2h_losses: int | None = None
    h2h_last_result: str | None = None


class ScheduleDifficultyResponse(BaseModel):
    """Dificultad del próximo tramo de calendario."""

    games_considered: int
    opponents_scouted: int
    avg_opponent_net_rating: float | None = None
    opponents: list[DifficultyOpponent]


class Projection(BaseModel):
    """Proyección de marcador esperado entre dos equipos."""

    predicted_net_rating: float | None = None
    predicted_pace: float | None = None
    predicted_ortg: float | None = None
    expected_margin: float | None = None


class ProjectionResponse(BaseModel):
    """Proyección de un enfrentamiento (projection puede ser null)."""

    team: TeamRef
    opponent: TeamRef
    projection: Projection | None = None


class HeadToHeadGame(BaseModel):
    """Un enfrentamiento directo entre dos equipos."""

    id: str
    date: str  # ISO-8601
    competition_name: str
    team_score: int | None = None
    opponent_score: int | None = None
    result: str | None = None  # "W" | "L" | null


class HeadToHeadResponse(BaseModel):
    """Enfrentamientos directos entre dos equipos."""

    team: TeamRef
    opponent: TeamRef
    items: list[HeadToHeadGame]


class UpcomingMatchup(BaseModel):
    """Próximo partido de un equipo (de upcoming_matchups)."""

    id: int
    opponent: TeamRef
    competition_name: str
    match_date: str  # ISO-8601
    is_home: bool
    predicted_net_rating: float | None = None
    predicted_pace: float | None = None
    predicted_ortg: float | None = None
    has_scouting_data: bool = False
    key_player_note: str | None = None
    h2h_wins: int | None = None
    h2h_losses: int | None = None
    h2h_last_result: str | None = None


class NarrativeResponse(BaseModel):
    """Narrativa de scouting (único campo en español de la API)."""

    team_id: str
    narrative: str | None = None
