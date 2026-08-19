"""Dependencias de FastAPI: sesión, resolución de equipo y filtros comunes.

Centraliza la resolución de objetos de dominio a partir de los parámetros de la
ruta/query, de modo que los routers no repitan lógica:

- `get_session`: abre una sesión SQLAlchemy por petición y la cierra al final.
- `get_team`: resuelve el slug de la ruta a un `models.Team`, o lanza
  `TeamNotFound` (→ 404) si no existe.
- `get_opponent`: igual para el slug de rival en las rutas de enfrentamiento.
- Filtros comunes (`season`, `league`) como dependencias reutilizables.
"""
from typing import Iterator

from fastapi import Depends, Path, Query
from sqlalchemy.orm import Session

from packages.baskonia_core.db import models
from packages.baskonia_core.db.session import create_session_factory
from packages.baskonia_core.errors import TeamNotFound
from packages.baskonia_core.services.roster import team_by_slug

from .settings import settings

# Fábrica de sesiones de la API (WAL, check_same_thread=False, pool_pre_ping).
_session_factory = create_session_factory(settings.database_url)


def get_session() -> Iterator[Session]:
    """Abre una sesión SQLAlchemy por petición y la cierra al terminar."""
    db = _session_factory()
    try:
        yield db
    finally:
        db.close()


def get_team(
    slug: str = Path(..., description="Slug del equipo (p.ej. 'vitoria')"),
    session: Session = Depends(get_session),
) -> models.Team:
    """Resuelve el slug de la ruta a un `models.Team`, o lanza `TeamNotFound`."""
    team = team_by_slug(session, slug)
    if team is None:
        raise TeamNotFound(slug)
    return team


def get_opponent(
    opponent_slug: str = Path(..., description="Slug del rival (p.ej. 'bilbao')"),
    session: Session = Depends(get_session),
) -> models.Team:
    """Resuelve el slug del rival a un `models.Team`, o lanza `TeamNotFound`."""
    team = team_by_slug(session, opponent_slug)
    if team is None:
        raise TeamNotFound(opponent_slug)
    return team


def season_param(
    season: int | None = Query(None, description="Año de inicio de temporada (p.ej. 2025)"),
) -> int | None:
    """Filtro global de temporada (omitir = sin filtrar)."""
    return season


def league_param(
    league: str | None = Query(None, description="Competición: acb | euroleague | supercopa"),
) -> str | None:
    """Filtro global de competición (omitir = sin filtrar)."""
    return league
