"""Dependencias de FastAPI: repositorio de scouting y resolución de ids.

Centraliza la resolución de objetos de dominio a partir de los parámetros de la
ruta/query, de modo que los routers no repitan lógica:

- `get_repository`: construye un `ScoutingRepository` sobre el engine de
  scouting por petición (el engine se crea una vez por aplicación y se guarda
  en `app.state`).
- `get_team_id` / `get_game_id`: resuelven los ids TEXT de la ruta.
- `season_label_param`: filtro de temporada (`'2025-2026'`).
"""
from typing import Iterator

from fastapi import Depends, Path, Query, Request

from packages.baskonia_core.db.scouting.engine import create_scouting_engine
from packages.baskonia_core.db.scouting.repository import ScoutingRepository

from .settings import settings


def get_repository(request: Request) -> Iterator[ScoutingRepository]:
    """Construye un `ScoutingRepository` por petición sobre el engine de la app.

    El engine se crea una sola vez (al primer uso) y se cachea en
    `app.state.scouting_engine`; cada petición construye un repositorio nuevo
    sobre ese mismo engine (los repositorios son baratos y sin estado).
    """
    engine = getattr(request.app.state, "scouting_engine", None)
    if engine is None:
        engine = create_scouting_engine(settings.database_url)
        request.app.state.scouting_engine = engine
    yield ScoutingRepository(engine)


def get_team_id(
    team_id: str = Path(..., description="Id TEXT del equipo (p.ej. 'bas')"),
) -> str:
    """Resuelve el `team_id` TEXT de la ruta."""
    return team_id


def get_game_id(
    game_id: str = Path(..., description="Id TEXT del partido (p.ej. 'g1')"),
) -> str:
    """Resuelve el `game_id` TEXT de la ruta."""
    return game_id


def season_label_param(
    season_label: str | None = Query(
        None, description="Etiqueta de temporada (p.ej. '2025-2026')"
    ),
) -> str | None:
    """Filtro global de temporada (omitir = sin filtrar)."""
    return season_label
