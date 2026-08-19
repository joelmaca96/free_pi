"""Schemas de los endpoints de meta (health, data-freshness)."""
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Estado de salud de la API."""

    status: str
    version: str


class DataFreshnessResponse(BaseModel):
    """Frescura de los datos: última fecha de partido y recuentos."""

    last_game_date: str | None
    games_total: int
    boxscores_total: int
    teams_total: int
