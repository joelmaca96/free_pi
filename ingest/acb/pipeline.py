"""Orquesta fetch -> parse -> load para la fuente ACB."""
import logging
from typing import Dict, List

from sqlalchemy.engine import Engine

from ingest.common.loader import load_game

from .client import AcbClient
from .parser import parse_and_resolve

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
