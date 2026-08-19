"""Schemas de los endpoints de partidos (games, boxscore)."""
from pydantic import BaseModel

from .teams import TeamRef


class GameAdvanced(BaseModel):
    """Estadísticas avanzadas de un equipo en un partido."""

    pace: float | None = None
    off_rating: float | None = None
    def_rating: float | None = None
    net_rating: float | None = None


class GameItem(BaseModel):
    """Un partido de un equipo (jugado o pendiente)."""

    id: int
    date: str  # ISO-8601
    league: str
    is_home: bool
    opponent: TeamRef
    team_score: int | None = None
    opponent_score: int | None = None
    result: str | None = None  # "W" | "L" | null
    notes: str | None = None
    advanced: GameAdvanced | None = None
    has_boxscore: bool = False


class GamesResponse(BaseModel):
    """Lista paginada de partidos de un equipo."""

    items: list[GameItem]
    total: int
    limit: int
    offset: int


class BoxScoreRow(BaseModel):
    """Fila de box score de un jugador en un partido."""

    player_name: str
    minutes: str | None = None
    points: int | None = None
    rebounds: int | None = None
    assists: int | None = None
    steals: int | None = None
    blocks: int | None = None
    turnovers: int | None = None
    fg_made: int | None = None
    fg_attempted: int | None = None
    fg3_made: int | None = None
    fg3_attempted: int | None = None
    ft_made: int | None = None
    ft_attempted: int | None = None
    efg_pct: float | None = None
    ts_pct: float | None = None


class BoxScoreResponse(BaseModel):
    """Box score de un equipo en un partido."""

    game_id: int
    team: TeamRef
    opponent: TeamRef
    date: str
    league: str
    team_score: int | None = None
    opponent_score: int | None = None
    result: str | None = None
    rows: list[BoxScoreRow]
