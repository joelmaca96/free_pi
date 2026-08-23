"""Router de discovery: POST /discovery/missing-games.

Compara el calendario real de una fuente/temporada contra `games` y **solo
reporta** qué partidos faltan por cargar. Decisión explícita del diseño
(`01_design.md` §4.2): este endpoint nunca encola una carga por sí mismo — cada
partido descubierto se carga con una llamada individual a
`POST /games/{game_id}/refresh?season_label=...`, que ya está acotada por sus
guardias de concurrencia. Así no se repite el patrón que causó el 58/344 de
Euroliga (un backfill masivo golpeando la fuente sin control).

Es una ruta **síncrona** (no `BackgroundTasks`): tiene que devolver la lista al
llamante y el diseño descarta mantener un canal de estado persistente. Bloquea
el hilo que la atiende hasta ~20s en el peor caso (ACB, ~40 páginas de
calendario); es aceptable porque es una acción explícita y poco frecuente, y
FastAPI ejecuta las rutas síncronas en el threadpool de `anyio` sin bloquear el
event loop.
"""
import logging
import threading
from typing import Literal

from fastapi import APIRouter, Depends, Query

from packages.baskonia_core.db.scouting.repository import ScoutingRepository

from .. import ingest_dispatch
from ..deps import get_repository
from ..schemas.discovery import MissingGamesResponse

logger = logging.getLogger("baskonia.api.discovery")

router = APIRouter(tags=["discovery"])

# Un lock por (fuente, temporada): dos discoveries de la misma clave se
# serializan (la segunda espera y recalcula) en vez de golpear la fuente en
# paralelo. No se rechaza la segunda ni se cachea el resultado: discovery es
# idempotente y barato de repetir, y así no hace falta un error de dominio nuevo.
_DISCOVERY_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key: tuple[str, str]) -> threading.Lock:
    """Lock de serialización de la clave `(source, season_label)`."""
    with _LOCKS_GUARD:
        return _DISCOVERY_LOCKS.setdefault(key, threading.Lock())


@router.post("/discovery/missing-games", response_model=MissingGamesResponse)
def discover_missing_games(
    source: Literal["acb", "euroleague"] = Query(..., description="Fuente de ingesta a consultar"),
    season_label: str = Query(
        ..., pattern=r"^\d{4}-\d{4}$", description="Temporada a consultar (p.ej. '2025-2026')"
    ),
    repo: ScoutingRepository = Depends(get_repository),
) -> MissingGamesResponse:
    """Lista los partidos jugados de la fuente/temporada que faltan en `games`.

    `source`/`season_label` inválidos los rechaza la validación de FastAPI con
    un 422 `problem+json` (ver `apps/api/errors.py`), sin código propio aquí.

    Args:
        source: fuente de ingesta (`acb` o `euroleague`).
        season_label: temporada en formato `'2025-2026'`.

    Returns:
        `MissingGamesResponse` con los ids ya prefijados (`"<source>-<id>"`),
        listos para `POST /games/{game_id}/refresh?season_label=...`.
    """
    season = int(season_label.split("-")[0])
    module = ingest_dispatch.PIPELINE_MODULES[source]

    with _lock_for((source, season_label)):
        missing = module.discover_missing_games(repo._engine, season)

    logger.info(
        "Discovery %s %s: %d partidos ausentes de games", source, season_label, len(missing)
    )
    return MissingGamesResponse(
        source=source,
        season_label=season_label,
        missing_game_ids=[f"{source}-{external_id}" for external_id in missing],
    )
