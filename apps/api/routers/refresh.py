"""Router de refresco bajo demanda de un partido: POST /games/{game_id}/refresh.

Dispara una recarga real del partido desde su fuente (`ingest.*.pipeline.
run_single_game`) con `fastapi.BackgroundTasks`, en el mismo proceso de la API
(ver `01_design.md` §1, Decisión 1 del gate humano). La petición responde 202 de
inmediato: el trabajo corre después en un hilo del threadpool de `anyio`, y el
cliente comprueba el resultado reconsultando los endpoints de lectura. Si el
fetch falla, esos endpoints siguen devolviendo `null`/`[]` — nunca un valor
fabricado ("degradado limpio").

Guardias en memoria (`_INFLIGHT`), por proceso: la API se despliega como un
único proceso `uvicorn` (sin `--workers N>1`); con varios workers estos guardias
no coordinarían entre procesos.
"""
import logging
import threading
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.engine import Engine

from packages.baskonia_core.db.scouting.repository import ScoutingRepository
from packages.baskonia_core.errors import GameNotFound, InvalidFilter

from .. import ingest_dispatch
from ..deps import get_game_id, get_repository
from ..schemas.refresh import RefreshResponse
from ..settings import settings

logger = logging.getLogger("baskonia.api.refresh")

router = APIRouter(tags=["refresh"])

# game_id -> time.monotonic() del momento en que se encoló el refresco.
_INFLIGHT: dict[str, float] = {}
_LOCK = threading.Lock()


def _prune_stale(now: float) -> None:
    """Descarta entradas huérfanas del guardia (llamar con `_LOCK` tomado).

    `_run_refresh_job` libera su entrada en un `finally`, así que en condiciones
    normales no queda ninguna. Este TTL es la red de seguridad para el caso en
    que la tarea nunca llegue a ejecutarse (p.ej. el middleware sustituye la
    respuesta por un 304 antes de que Starlette lance el background), que si no
    bloquearía ese `game_id` para siempre.
    """
    cooldown = settings.refresh_cooldown_seconds
    for game_id, started in list(_INFLIGHT.items()):
        if now - started > cooldown:
            _INFLIGHT.pop(game_id, None)


def _run_refresh_job(
    engine: Engine, source: str, season: int, external_id: str, game_id: str
) -> None:
    """Recarga un partido; ejecutado por `BackgroundTasks`, fuera de la petición.

    Nunca debe propagar una excepción (rompería el proceso de uvicorn con un
    error sin contexto) ni dejar `_INFLIGHT` colgado.
    """
    try:
        module = ingest_dispatch.PIPELINE_MODULES[source]
        summary = module.run_single_game(
            engine, season, ingest_dispatch.coerce_external_id(source, external_id)
        )
        logger.info("Refresco de %s terminado: %s", game_id, summary)
    except Exception:  # noqa: BLE001 - un refresco fallido no debe tumbar uvicorn
        logger.exception("Refresco en background falló para %s", game_id)
    finally:
        with _LOCK:
            _INFLIGHT.pop(game_id, None)


@router.post("/games/{game_id}/refresh", response_model=RefreshResponse, status_code=202)
def refresh_game(
    background_tasks: BackgroundTasks,
    game_id: str = Depends(get_game_id),
    season_label: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{4}$",
        description=(
            "Temporada del partido (p.ej. '2025-2026'). Solo necesario para "
            "cargar un partido que todavía no existe en `games` (descubierto "
            "con POST /discovery/missing-games)."
        ),
    ),
    repo: ScoutingRepository = Depends(get_repository),
) -> RefreshResponse:
    """Encola la recarga de un partido desde su fuente (202, fire-and-forget).

    Args:
        game_id: id del partido (`"<fuente>-<id externo>"`).
        season_label: temporada, obligatoria solo si el partido aún no existe.

    Returns:
        `RefreshResponse` con `triggered`, `already_in_progress` o
        `rejected_busy`.

    Raises:
        InvalidFilter: la fuente del `game_id` no admite refresco bajo demanda.
        GameNotFound: el partido no existe y no se pasó `season_label`.
    """
    source, _, external_id = game_id.partition("-")
    if source not in ingest_dispatch.SUPPORTED_SOURCES or not external_id:
        raise InvalidFilter(f"La fuente '{source}' no admite refresco bajo demanda.")

    existing_label = repo.get_game_season_label(game_id)
    if existing_label:
        season = int(existing_label.split("-")[0])
    elif season_label is not None:
        season = int(season_label.split("-")[0])
    else:
        raise GameNotFound(game_id)

    now = time.monotonic()
    with _LOCK:
        _prune_stale(now)
        if game_id in _INFLIGHT:
            return RefreshResponse(game_id=game_id, status="already_in_progress")
        if len(_INFLIGHT) >= settings.refresh_max_concurrent:
            return RefreshResponse(game_id=game_id, status="rejected_busy")
        _INFLIGHT[game_id] = now

    # Mismo engine cacheado en `app.state` que usan las lecturas: un `Engine` de
    # SQLAlchemy ya es un pool, y `PRAGMA busy_timeout` (ver
    # `create_scouting_engine`) evita el "database is locked" inmediato.
    background_tasks.add_task(
        _run_refresh_job, repo._engine, source, season, external_id, game_id
    )
    return RefreshResponse(game_id=game_id, status="triggered")
