"""Schemas de los endpoints de jugadores (roster, form, streaks, load)."""
from pydantic import BaseModel

from .teams import TeamRef


class RosterPlayer(BaseModel):
    """Jugador de la plantilla actual con su forma reciente."""

    name: str
    number: str | None = None
    position: str | None = None
    photo_url: str | None = None
    form: dict | None = None


class RosterResponse(BaseModel):
    """Plantilla actual de un equipo."""

    team: TeamRef
    players: list[RosterPlayer]


class PlayerFormItem(BaseModel):
    """Fila de forma reciente por jugador (mapeo 1:1 con player_recent_form)."""

    player_name: str
    games: int
    avg_minutes: float | None = None
    avg_pts: float | None = None
    avg_pts_per36: float | None = None
    avg_efg_pct: float | None = None
    avg_ts_pct: float | None = None
    avg_plus_minus: float | None = None
    avg_turnovers: float | None = None
    fg3a_rate: float | None = None
    ft_rate: float | None = None


class PlayerFormResponse(BaseModel):
    """Forma reciente por jugador."""

    last_n: int
    items: list[PlayerFormItem]


class StreakItem(BaseModel):
    """Racha de un jugador dentro de una temporada."""

    player_name: str
    games_season: int
    recent_avg_pts: float | None = None
    season_avg_pts: float | None = None
    season_std_pts: float | None = None
    z_score_pts: float | None = None
    recent_avg_ts_pct: float | None = None
    season_avg_ts_pct: float | None = None
    season_std_ts_pct: float | None = None
    z_score_ts: float | None = None
    label: str  # "hot" | "cold" | "neutral"


class StreaksResponse(BaseModel):
    """Rachas de los jugadores de un equipo en una temporada."""

    season: int
    recent_n: int
    min_season_games: int
    items: list[StreakItem]


class LoadItem(BaseModel):
    """Carga de minutos de un jugador en la ventana."""

    player_name: str
    games: int
    total_minutes: float
    avg_minutes: float


class LoadResponse(BaseModel):
    """Carga de minutos por jugador (transversal a temporada/competición)."""

    window_days: int
    games_in_window: int
    note: str
    items: list[LoadItem]
