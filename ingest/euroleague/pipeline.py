"""Orquesta fetch -> parse -> load para la fuente Euroliga."""
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

import requests
from sqlalchemy.engine import Engine

from ingest.common.loader import load_game

from .adapter import build_raw_game
from .client import EuroleagueClient
from .parser import parse_and_resolve

logger = logging.getLogger(__name__)

# `euroleague_api` no aplica ningún rate-limit propio: pedir una temporada
# completa (~400 partidos x 4 endpoints) sin pausas dispara un 429 de
# `live.euroleague.net` a partir de la primera decena de partidos
# (verificado en vivo, ver resumen del turno). Pausa entre partidos +
# reintento con backoff en cada llamada.
REQUEST_DELAY = float(os.getenv("EUROLEAGUE_REQUEST_DELAY", "3.0"))
MAX_RETRIES = int(os.getenv("EUROLEAGUE_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("EUROLEAGUE_RETRY_BACKOFF", "10.0"))


def _to_records(df) -> list:
    """Convierte un DataFrame de `euroleague_api` a `list[dict]` (o lo deja igual si ya lo es)."""
    return df.to_dict("records") if hasattr(df, "to_dict") else list(df)


def _to_single_dict(df_or_dict) -> dict:
    if hasattr(df_or_dict, "iloc"):
        return df_or_dict.iloc[0].to_dict()
    return dict(df_or_dict)


def _with_retry(fn: Callable[[], Any]) -> Any:
    """Reintenta con backoff ante un 429 (rate limit); relanza cualquier otro error."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except requests.exceptions.HTTPError as exc:
            is_rate_limited = exc.response is not None and exc.response.status_code == 429
            if not is_rate_limited or attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF_SECONDS * attempt
            logger.warning("Euroliga: 429 (intento %d/%d), esperando %.0fs...", attempt, MAX_RETRIES, wait)
            time.sleep(wait)


def run(engine: Engine, season: int, client: Optional[EuroleagueClient] = None) -> Dict[str, List[str]]:
    """Descarga y carga todos los partidos jugados de una temporada de Euroliga."""
    client = client or EuroleagueClient()
    summary: Dict[str, List[str]] = {"loaded": [], "failed": []}

    schedule_df = client.fetch_season_game_codes(season)
    played_column = "played" if hasattr(schedule_df, "columns") and "played" in schedule_df.columns else None
    schedule_records = _to_records(schedule_df)
    # "gamecode" en el calendario es el código compuesto ("E2025_7"), no el
    # parámetro que aceptan boxscore/shots/metadata/pbp - ese es "game" (int).
    # "played" llega como texto "true"/"false", no booleano.
    game_codes = [
        row["game"]
        for row in schedule_records
        if played_column is None or str(row.get(played_column)).lower() == "true"
    ]
    logger.info("Euroliga %s: %d partidos jugados encontrados", season, len(game_codes))

    for game_code in game_codes:
        try:
            metadata = _to_single_dict(_with_retry(lambda: client.fetch_game_metadata(season, game_code)))
            boxscore_records = _to_records(_with_retry(lambda: client.fetch_game_boxscore(season, game_code)))
            shot_records = _to_records(_with_retry(lambda: client.fetch_game_shot_data(season, game_code)))
            pbp_records = _to_records(_with_retry(lambda: client.fetch_game_play_by_play(season, game_code)))
            raw = build_raw_game(metadata, boxscore_records, shot_records, pbp_records)

            with engine.begin() as conn:
                game = parse_and_resolve(conn, raw)
                load_game(conn, game)
            summary["loaded"].append(str(game_code))
        except Exception:  # noqa: BLE001 - un partido roto no debe tumbar el resto
            logger.exception("Euroliga: fallo cargando el partido %s", game_code)
            summary["failed"].append(str(game_code))
        finally:
            time.sleep(REQUEST_DELAY)

    logger.info("Euroliga %s: %d cargados, %d fallidos", season, len(summary["loaded"]), len(summary["failed"]))
    return summary
