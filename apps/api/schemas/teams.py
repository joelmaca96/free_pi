"""Schemas de los endpoints de equipos (teams, filters, summary)."""
from pydantic import BaseModel


class TeamRef(BaseModel):
    """Referencia a un equipo (slug + nombre para mostrar)."""

    slug: str
    name: str


class TeamResponse(TeamRef):
    """Equipo con su liga de origen."""

    league: str


class LeagueOption(BaseModel):
    """Opción de competición para el selector de filtros."""

    code: str
    label: str


class FiltersResponse(BaseModel):
    """Filtros disponibles para un equipo (cabecera de la app)."""

    seasons: list[int]
    default_season: int | None
    leagues: list[LeagueOption]


class AdvancedSummary(BaseModel):
    """Medias de estadísticas avanzadas de un equipo (cada clave puede ser null)."""

    avg_pace: float | None = None
    avg_off_rating: float | None = None
    avg_def_rating: float | None = None
    avg_net_rating: float | None = None
    avg_efg_pct: float | None = None
    avg_ts_pct: float | None = None


class SummaryResponse(BaseModel):
    """Resumen del equipo: identidad, filtros aplicados y medias avanzadas."""

    team: TeamRef
    filters: dict
    advanced: AdvancedSummary
    games_played: int
    games_upcoming: int
