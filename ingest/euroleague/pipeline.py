"""Orquesta fetch -> parse -> load para la fuente Euroliga."""
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ingest.common.identity import get_competition_id, get_or_create_season, normalize_name, resolve_or_create_team
from ingest.common.loader import list_existing_external_ids, load_game

from .adapter import COMPETITION_NAME, build_raw_game, build_scheduled_matchup
from .client import EuroleagueClient
from .parser import SOURCE, parse_and_resolve

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


def _played_game_codes(schedule_df) -> List[int]:
    """`game_code` de los partidos ya jugados de un calendario de temporada.

    "gamecode" en el calendario es el código compuesto ("E2025_7"), no el
    parámetro que aceptan boxscore/shots/metadata/pbp - ese es "game" (int).
    "played" llega como texto "true"/"false", no booleano.
    """
    played_column = "played" if hasattr(schedule_df, "columns") and "played" in schedule_df.columns else None
    return [
        row["game"]
        for row in _to_records(schedule_df)
        if played_column is None or str(row.get(played_column)).lower() == "true"
    ]


def _fetch_and_load_game(engine: Engine, client: EuroleagueClient, season: int, game_code: int) -> None:
    """Descarga los 4 endpoints de un partido y lo carga (idempotente).

    Extraído de `run()` para que `run_single_game` no duplique la secuencia
    metadata/boxscore/tiros/play-by-play ni la política de reintento.
    """
    metadata = _to_single_dict(_with_retry(lambda: client.fetch_game_metadata(season, game_code)))
    boxscore_records = _to_records(_with_retry(lambda: client.fetch_game_boxscore(season, game_code)))
    shot_records = _to_records(_with_retry(lambda: client.fetch_game_shot_data(season, game_code)))
    pbp_records = _to_records(_with_retry(lambda: client.fetch_game_play_by_play(season, game_code)))
    raw = build_raw_game(metadata, boxscore_records, shot_records, pbp_records)

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw)
        load_game(conn, game)


def run(engine: Engine, season: int, client: Optional[EuroleagueClient] = None) -> Dict[str, List[str]]:
    """Descarga y carga todos los partidos jugados de una temporada de Euroliga."""
    client = client or EuroleagueClient()
    summary: Dict[str, List[str]] = {"loaded": [], "failed": []}

    game_codes = _played_game_codes(client.fetch_season_game_codes(season))
    logger.info("Euroliga %s: %d partidos jugados encontrados", season, len(game_codes))

    for game_code in game_codes:
        try:
            _fetch_and_load_game(engine, client, season, game_code)
            summary["loaded"].append(str(game_code))
        except Exception:  # noqa: BLE001 - un partido roto no debe tumbar el resto
            logger.exception("Euroliga: fallo cargando el partido %s", game_code)
            summary["failed"].append(str(game_code))
        finally:
            time.sleep(REQUEST_DELAY)

    logger.info("Euroliga %s: %d cargados, %d fallidos", season, len(summary["loaded"]), len(summary["failed"]))
    return summary


def run_single_game(
    engine: Engine, season: int, game_code: int, client: Optional[EuroleagueClient] = None
) -> Dict[str, List[str]]:
    """Descarga y recarga UN partido de Euroliga (exista o no ya en `games`), idempotente.

    `fetch_game_metadata/boxscore/shot_data/play_by_play` aceptan
    `(season, game_code)` directamente, así que no hace falta listar el
    calendario. Coste: 4 llamadas, con reintento y backoff si la fuente
    responde 429 (`EUROLEAGUE_MAX_RETRIES`/`EUROLEAGUE_RETRY_BACKOFF`).

    Args:
        engine: engine SQLAlchemy sobre el esquema de scouting.
        season: año de inicio de temporada (`2025` = "2025-2026").
        game_code: código numérico del partido (columna `game` del calendario).
        client: cliente de Euroliga a reutilizar (uno nuevo si se omite).

    Returns:
        Mismo contrato que `run()`: `{"loaded": [...], "failed": [...]}`. Si el
        fetch o la carga fallan, el `game_code` queda en `"failed"` — nunca
        propaga una excepción al llamante.
    """
    client = client or EuroleagueClient()
    summary: Dict[str, List[str]] = {"loaded": [], "failed": []}

    try:
        _fetch_and_load_game(engine, client, season, game_code)
        summary["loaded"].append(str(game_code))
    except Exception:  # noqa: BLE001 - un refresco fallido no debe propagarse al llamante
        logger.exception(
            "Euroliga: fallo refrescando el partido %s (temporada %s)", game_code, season
        )
        summary["failed"].append(str(game_code))

    return summary


def discover_missing_games(
    engine: Engine, season: int, client: Optional[EuroleagueClient] = None
) -> List[int]:
    """`game_code` de los partidos jugados de `season` ausentes de `games`.

    Un único fetch de calendario (mismo primer paso que `run()`), sin ninguna
    llamada por partido: no está sujeto al rate-limit que causó el 58/344 de
    `doc/features/ingestor/01_estado.md` §2.3 (ese fallo lo provocaba el
    fetch+load por partido, no el listado del calendario).

    Args:
        engine: engine SQLAlchemy sobre el esquema de scouting.
        season: año de inicio de temporada (`2025` = "2025-2026").
        client: cliente de Euroliga a reutilizar (uno nuevo si se omite).

    Returns:
        Códigos de partido (sin prefijo de fuente), en el orden del calendario.
    """
    client = client or EuroleagueClient()
    game_codes = _played_game_codes(client.fetch_season_game_codes(season))
    existing = list_existing_external_ids(engine, SOURCE, season)
    missing = [game_code for game_code in game_codes if str(game_code) not in existing]
    logger.info(
        "Euroliga %s: %d jugados en el calendario, %d ya en BD, %d ausentes",
        season, len(game_codes), len(existing), len(missing),
    )
    return missing


# --- Calendario futuro (upcoming_matchups) + escudos ------------------------


def _own_team_euroleague_code(conn, clubs_by_code: Dict[str, Dict[str, Any]]) -> str:
    """Código de club de Euroliga (p.ej. `"BAS"`) del Baskonia en el catálogo de esta
    temporada, para filtrar el calendario en `build_scheduled_matchup`.

    A diferencia de ACB (id numérico que cambia con el patrocinador cada
    temporada, ver `ingest/acb/pipeline.py::_own_team_acb_external_id`), el
    `code` de Euroliga es el identificador propio y estable del club en el
    backend - no cambia con el sponsor (verificado en vivo: el mismo `"BAS"`
    ya estaba vinculado en `team_external_ids` de una carga de partidos
    jugados de una temporada anterior). Por eso aquí se prueba PRIMERO ese
    enlace ya guardado (barato, sin ambigüedad) y solo se cae a resolver por
    nombre normalizado dentro del catálogo de clubes de esta temporada (con
    `_KNOWN_TEAM_ALIASES` cubriendo el nombre real 2026-2027, "Kosner
    Baskonia Vitoria-Gasteiz") si no hay enlace todavía - una BD nueva sin
    ningún partido de Euroliga cargado.
    """
    row = conn.execute(
        text(
            "SELECT tei.external_id FROM team_external_ids tei"
            " JOIN teams t ON t.id = tei.team_id"
            " WHERE t.is_own_team = 1 AND tei.source = 'euroleague' LIMIT 1"
        )
    ).first()
    if row is not None and row[0] in clubs_by_code:
        return row[0]

    for code, info in clubs_by_code.items():
        if normalize_name(info["name"]) == "baskonia":
            return code

    raise ValueError(
        "Euroliga: no se pudo identificar al Baskonia en el catálogo de clubes de esta "
        "temporada (ni por team_external_ids ni por nombre normalizado) - revisa "
        "ingest.common.identity._KNOWN_TEAM_ALIASES."
    )


def run_upcoming(engine: Engine, season: int, client: Optional[EuroleagueClient] = None) -> Dict[str, int]:
    """Refresca el calendario NO jugado del Baskonia en Euroliga (`upcoming_matchups`).

    Igual que `ingest/acb/pipeline.py::run_upcoming`: no toca `games` (los
    partidos ya jugados siguen yendo por `run()`), y de paso backfillea el
    escudo (`teams.logo_url`) del Baskonia y de cada rival vía
    `EuroleagueClient.fetch_clubs` - funciona también para escudos de
    rivales que solo aparecen en partidos YA jugados de temporadas
    anteriores (misma fila de `teams`, se actualiza igual). Idempotente vía
    borrar-e-insertar, acotado a `(season_id, competition_id)` para no tocar
    el calendario futuro de ACB que pueda haber para la misma temporada
    (`ingest/acb/pipeline.py::run_upcoming` hace la misma acotación).

    Returns:
        `{"season_id": ..., "upcoming": N}` - cuántos partidos futuros del
        Baskonia se cargaron.
    """
    client = client or EuroleagueClient()
    schedule_rows = _to_records(client.fetch_season_game_codes(season))
    clubs = client.fetch_clubs(season)
    clubs_by_code = {c["code"]: c for c in clubs}

    with engine.begin() as conn:
        own_code = _own_team_euroleague_code(conn, clubs_by_code)
        season_id = get_or_create_season(conn, season)
        competition_id = get_competition_id(conn, COMPETITION_NAME)

        own_info = clubs_by_code.get(own_code)
        if own_info:
            resolve_or_create_team(
                conn, SOURCE, own_code, own_info["name"], logo_url=(own_info.get("images") or {}).get("crest")
            )

        matchups = [
            m
            for m in (
                build_scheduled_matchup(row, clubs_by_code, own_code)
                for row in schedule_rows
                if str(row.get("played", "")).strip().lower() != "true"
            )
            if m and m["match_date"]
        ]

        conn.execute(
            text("DELETE FROM upcoming_matchups WHERE season_id = :season_id AND competition_id = :competition_id"),
            {"season_id": season_id, "competition_id": competition_id},
        )
        for matchup in matchups:
            opponent_team_id = resolve_or_create_team(
                conn, SOURCE, matchup["opponent_code"], matchup["opponent_name"],
                logo_url=matchup["opponent_logo_url"],
            )
            conn.execute(
                text(
                    "INSERT INTO upcoming_matchups"
                    " (opponent_team_id, competition_id, match_date, is_home, season_id)"
                    " VALUES (:opponent_team_id, :competition_id, :match_date, :is_home, :season_id)"
                ),
                {
                    "opponent_team_id": opponent_team_id,
                    "competition_id": competition_id,
                    "match_date": matchup["match_date"],
                    "is_home": 1 if matchup["is_home"] else 0,
                    "season_id": season_id,
                },
            )

    logger.info("Euroliga %s: %d partidos futuros del Baskonia cargados en upcoming_matchups", season, len(matchups))
    return {"season_id": season_id, "upcoming": len(matchups)}
