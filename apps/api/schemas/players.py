"""Schemas de los endpoints de jugadores (roster, form, load)."""
from pydantic import BaseModel

from .teams import TeamRef


class RosterPlayer(BaseModel):
    """Jugador de la plantilla con sus medias de temporada."""

    id: str
    name: str
    number: int | None = None
    position: str | None = None
    team_id: str
    active: bool = True
    photo_url: str | None = None
    height_cm: int | None = None
    birth_date: str | None = None
    nationality: str | None = None
    gp: int | None = None
    min_avg: float | None = None
    pts_avg: float | None = None
    reb_avg: float | None = None
    ast_avg: float | None = None
    efg_pct: float | None = None


class RosterResponse(BaseModel):
    """Plantilla de un equipo para una temporada."""

    team: TeamRef
    season_label: str
    players: list[RosterPlayer]


class PlayerFormItem(BaseModel):
    """Fila de forma reciente de un jugador en un partido."""

    game_id: str
    game_date: str
    pts: int | None = None
    reb: int | None = None
    ast: int | None = None
    efg_pct: float | None = None


class PlayerFormResponse(BaseModel):
    """Forma reciente de un jugador."""

    player_id: str
    last_n: int
    items: list[PlayerFormItem]


class LoadItem(BaseModel):
    """Carga de minutos de un jugador en la ventana."""

    player_id: str
    name: str
    total_minutes: float


class LoadResponse(BaseModel):
    """Carga de minutos por jugador (transversal a temporada/competición)."""

    window_days: int
    as_of: str
    items: list[LoadItem]
