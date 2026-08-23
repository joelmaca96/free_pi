"""Orquesta fetch -> parse -> load para la fuente ACB."""
import logging
from typing import Dict, List

from sqlalchemy.engine import Engine

from ingest.common.loader import list_existing_external_ids, load_game

from .client import AcbClient
from .parser import SOURCE, parse_and_resolve

logger = logging.getLogger(__name__)


def run(engine: Engine, season: int, client: AcbClient = None) -> Dict[str, List[str]]:
    """Descarga y carga todos los partidos finalizados de una temporada ACB.

    Devuelve un resumen `{"loaded": [...ids...], "failed": [...ids...]}` para
    que el orquestador (`ingest/run_all.py`) pueda reportar qué falló sin que
    un partido roto tumbe el resto.
    """
    client = client or AcbClient()
    summary: Dict[str, List[str]] = {"loaded": [], "failed": []}

    game_ids = client.fetch_season_game_ids(season)
    logger.info("ACB %s: %d partidos finalizados encontrados", season, len(game_ids))

    for game_id in game_ids:
        try:
            raw = client.fetch_game(game_id)
            with engine.begin() as conn:
                game = parse_and_resolve(conn, raw)
                load_game(conn, game)
            summary["loaded"].append(game_id)
        except Exception:  # noqa: BLE001 - un partido roto no debe tumbar el resto
            logger.exception("ACB: fallo cargando el partido %s", game_id)
            summary["failed"].append(game_id)

    logger.info("ACB %s: %d cargados, %d fallidos", season, len(summary["loaded"]), len(summary["failed"]))
    return summary


def run_single_game(
    engine: Engine, season: int, game_id: str, client: AcbClient = None
) -> Dict[str, List[str]]:
    """Descarga y recarga UN partido ACB (exista o no ya en `games`), idempotente.

    Llama antes a `fetch_season_finished_matches(season)`: `AcbClient.fetch_game`
    necesita el resumen de jornada del partido (equipo local/visitante, fecha)
    que solo se cachea al listar el calendario, así que ese primer paso no es
    coste extra evitable.

    Args:
        engine: engine SQLAlchemy sobre el esquema de scouting.
        season: año de inicio de temporada (`2025` = "2025-2026").
        game_id: id externo del partido en acb.com (sin prefijo de fuente).
        client: cliente ACB a reutilizar (uno nuevo si se omite).

    Returns:
        Mismo contrato que `run()`: `{"loaded": [...], "failed": [...]}`. Si
        `game_id` no está en el calendario de esa temporada o el fetch falla,
        queda en `"failed"` — nunca propaga una excepción al llamante.
    """
    client = client or AcbClient()
    summary: Dict[str, List[str]] = {"loaded": [], "failed": []}
    game_id = str(game_id)

    try:
        known_ids = {str(match["id"]) for match in client.fetch_season_finished_matches(season)}
        if game_id not in known_ids:
            raise ValueError(
                f"ACB: el partido {game_id} no aparece como finalizado en la temporada {season}."
            )
        raw = client.fetch_game(game_id)
        with engine.begin() as conn:
            game = parse_and_resolve(conn, raw)
            load_game(conn, game)
        summary["loaded"].append(game_id)
    except Exception:  # noqa: BLE001 - un refresco fallido no debe propagarse al llamante
        logger.exception("ACB: fallo refrescando el partido %s (temporada %s)", game_id, season)
        summary["failed"].append(game_id)

    return summary


def discover_missing_games(engine: Engine, season: int, client: AcbClient = None) -> List[str]:
    """Ids de partidos ACB finalizados de `season` que aún no están en `games`.

    Reusa solo el primer paso (barato) de `run()`: recorrer el calendario de la
    edición. NO descarga boxscore/tiros/play-by-play de ningún partido, así que
    el coste es el del listado (~40 páginas con `ACB_REQUEST_DELAY`), no el del
    backfill.

    Args:
        engine: engine SQLAlchemy sobre el esquema de scouting.
        season: año de inicio de temporada (`2025` = "2025-2026").
        client: cliente ACB a reutilizar (uno nuevo si se omite).

    Returns:
        Ids externos (sin prefijo de fuente), en el orden del calendario.
    """
    client = client or AcbClient()
    finished_ids = [str(match["id"]) for match in client.fetch_season_finished_matches(season)]
    existing = list_existing_external_ids(engine, SOURCE, season)
    missing = [game_id for game_id in finished_ids if game_id not in existing]
    logger.info(
        "ACB %s: %d finalizados en el calendario, %d ya en BD, %d ausentes",
        season, len(finished_ids), len(existing), len(missing),
    )
    return missing
