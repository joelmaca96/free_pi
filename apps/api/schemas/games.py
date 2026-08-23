"""Schemas de los endpoints de partidos (games, boxscore, detail)."""
from pydantic import BaseModel

from .teams import TeamRef


class GameAdvanced(BaseModel):
    """Estadísticas avanzadas de un partido (four factors)."""

    ortg: float | None = None
    drtg: float | None = None
    net_rating: float | None = None
    efg_pct: float | None = None
    ts_pct: float | None = None
    tov_pct: float | None = None
    orb_pct: float | None = None
    ast_pct: float | None = None
    stl_pct: float | None = None
    blk_pct: float | None = None
    ft_rate: float | None = None
    ast_to_ratio: float | None = None


class GameItem(BaseModel):
    """Un partido de un equipo (jugado o pendiente)."""

    id: str
    date: str  # ISO-8601
    competition_name: str
    is_home: bool
    opponent: TeamRef
    team_score: int | None = None
    opponent_score: int | None = None
    result: str | None = None  # "W" | "L" | null
    pace: float | None = None
    advanced: GameAdvanced | None = None


class GamesResponse(BaseModel):
    """Lista paginada de partidos de un equipo."""

    items: list[GameItem]
    total: int
    limit: int
    offset: int


class BoxScoreRow(BaseModel):
    """Fila de box score de un jugador en un partido."""

    game_id: str
    player_id: str
    name: str
    team_id: str
    minutes: float | None = None
    pts: int | None = None
    reb: int | None = None
    ast: int | None = None
    efg_pct: float | None = None


class BoxScoreResponse(BaseModel):
    """Box score de un partido (filas de ambos equipos)."""

    game_id: str
    rows: list[BoxScoreRow]


class BaskoniaBlock(BaseModel):
    """Bloque de Baskonia dentro del detalle de partido."""

    is_home: bool
    opponent_id: str
    opponent_name: str
    score_for: int
    score_against: int


class Lineup(BaseModel):
    """Quinteto con su rendimiento en el partido."""

    id: int
    minutes: float | None = None
    plus_minus: int | None = None
    players: list[TeamRef]


class ZoneStat(BaseModel):
    """Estadística por zona de la cancha."""

    team_id: str
    team_name: str
    zone_id: int
    label: str
    fg_pct: float | None = None
    volume: int | None = None


class KeyEvent(BaseModel):
    """Evento clave del partido."""

    team_id: str
    team_name: str
    quarter: str | None = None
    game_clock: str | None = None
    label: str | None = None


class GameDetailResponse(BaseModel):
    """Detalle completo de un partido."""

    id: str
    season_label: str
    competition_name: str
    home_team: TeamRef
    away_team: TeamRef
    game_date: str
    home_score: int | None = None
    away_score: int | None = None
    pace: float | None = None
    narrative: str | None = None
    baskonia: BaskoniaBlock | None = None
    advanced: list[GameAdvanced]
    lineups: list[Lineup]
    zone_stats: list[ZoneStat]
    key_events: list[KeyEvent]
