"""Schemas de los endpoints de equipos (teams, filters, summary, rating-trend)."""
from pydantic import BaseModel


class TeamRef(BaseModel):
    """Referencia a un equipo (id TEXT + nombre para mostrar)."""

    id: str
    name: str


class TeamResponse(TeamRef):
    """Equipo con su condición de equipo propio."""

    is_own_team: bool


class CompetitionOption(BaseModel):
    """Opción de competición para el selector de filtros."""

    id: int
    name: str


class FiltersResponse(BaseModel):
    """Filtros disponibles para un equipo (cabecera de la app)."""

    seasons: list[str]
    default_season: str | None
    competitions: list[CompetitionOption]


class AdvancedSummary(BaseModel):
    """Medias de estadísticas avanzadas de un equipo (cada clave puede ser null)."""

    avg_ortg: float | None = None
    avg_drtg: float | None = None
    avg_net_rating: float | None = None
    avg_efg_pct: float | None = None
    avg_ts_pct: float | None = None
    avg_tov_pct: float | None = None
    avg_orb_pct: float | None = None
    avg_ast_pct: float | None = None
    avg_stl_pct: float | None = None
    avg_blk_pct: float | None = None
    avg_ft_rate: float | None = None
    avg_ast_to_ratio: float | None = None


class SummaryResponse(BaseModel):
    """Resumen del equipo: identidad, filtros aplicados y medias avanzadas."""

    team: TeamRef
    filters: dict
    advanced: AdvancedSummary
    games_played: int
    games_upcoming: int


class RatingTrendItem(BaseModel):
    """Punto de la tendencia ORtg/DRtg de un equipo."""

    game_id: str
    game_date: str
    ortg: float | None = None
    drtg: float | None = None


class RatingTrendResponse(BaseModel):
    """Tendencia ORtg/DRtg de un equipo en sus últimos N partidos."""

    team_id: str
    last_n: int
    items: list[RatingTrendItem]
