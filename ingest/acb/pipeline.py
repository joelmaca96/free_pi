"""Orquesta fetch -> parse -> load para la fuente ACB."""
import logging
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ingest.common.identity import get_competition_id, get_or_create_season, resolve_or_create_team
from ingest.common.loader import list_existing_external_ids, load_game

from .adapter import COMPETITION_NAME, build_scheduled_matchup
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


def _own_team_acb_external_id(conn, teams_by_id: Dict[str, Dict]) -> str:
    """Id numérico de ACB del equipo propio EN ESTA EDICIÓN, para filtrar el calendario.

    HALLAZGO (2026-08-24, verificado en vivo contra `data/baskonia.db`): ACB
    asigna un `id` de equipo NUEVO cada vez que cambia el patrocinador — no es un
    id estable del club. El Baskonia real ya tenía en `team_external_ids` TRES
    external_id de `source='acb'` distintos (`4398`, `4462`, `4425`, de
    temporadas/patrocinadores anteriores) y NINGUNO coincidía con el `4463` de la
    edición 2026-2027 ("Kosner Baskonia") - la primera versión de esta función,
    que cogía cualquiera de los ya vinculados con `LIMIT 1`, devolvía un id de
    otra temporada y el filtro de partidos del Baskonia se quedaba silenciosamente
    en 0 (sin excepción, porque un id numérico "válido pero equivocado" no falla,
    solo no coincide con ningún `homeTeamId`/`awayTeamId` de esta edición).

    Por eso ahora se resuelve SIEMPRE por nombre normalizado dentro del catálogo
    de equipos de ESTA edición (`teams_by_id`, `_KNOWN_TEAM_ALIASES` ya resuelve
    variantes de patrocinador como "Kosner Baskonia") - es la única fuente que
    garantiza el id correcto para el calendario que se está pidiendo. Solo cae a
    `team_external_ids` si por lo que sea el catálogo de la edición no trae al
    Baskonia (no debería pasar, red de seguridad).
    """
    from ingest.common.identity import normalize_name

    for external_id, info in teams_by_id.items():
        if normalize_name(info["name"]) == "baskonia":
            return external_id

    row = conn.execute(
        text(
            "SELECT tei.external_id FROM team_external_ids tei"
            " JOIN teams t ON t.id = tei.team_id"
            " WHERE t.is_own_team = 1 AND tei.source = 'acb' LIMIT 1"
        )
    ).first()
    if row is not None:
        return row[0]

    raise ValueError(
        "ACB: no se pudo identificar al Baskonia en el catálogo de equipos de esta edición "
        "(ni por nombre normalizado ni por team_external_ids) - revisa _KNOWN_TEAM_ALIASES."
    )


def run_upcoming(engine: Engine, season: int, client: AcbClient = None) -> Dict[str, int]:
    """Refresca el calendario NO jugado del Baskonia (`upcoming_matchups`) para `season`.

    A diferencia de `run()` (partidos ya finalizados), esto no trae boxscore ni
    genera filas en `games` - solo rival/fecha/local-visitante para "próximo
    partido" (ver `app/data/queries.py::next_matchup`). De paso, backfillea el
    escudo (`teams.logo_url`) del Baskonia y de cada rival que aparece en el
    calendario, con el campo `logo` real que trae ACB (ver
    `AcbClient.fetch_season_scheduled_matches`) - primera fuente de escudo
    verificada del proyecto.

    Idempotente vía borrar-y-reinsertar (mismo patrón que `lineups`/`shots` en
    `ingest/common/loader.py`): una llamada repetida para la misma `season`
    reemplaza el calendario entero de esa temporada en vez de acumular duplicados.
    Acotado a `(season_id, competition_id)`, no solo `season_id` — desde que
    `ingest/euroleague/pipeline.py::run_upcoming` existe, `upcoming_matchups`
    puede tener filas de más de una competición para la misma temporada; sin
    esa segunda condición, refrescar el calendario de ACB borraría también el
    de Euroliga ya cargado (y viceversa).

    Returns:
        `{"season_id": ..., "upcoming": N}` - cuántos partidos futuros del
        Baskonia se cargaron.
    """
    client = client or AcbClient()
    matches, teams_by_id = client.fetch_season_scheduled_matches(season)

    with engine.begin() as conn:
        own_acb_id = _own_team_acb_external_id(conn, teams_by_id)
        season_id = get_or_create_season(conn, season)
        competition_id = get_competition_id(conn, COMPETITION_NAME)

        own_info = teams_by_id.get(own_acb_id)
        if own_info:
            resolve_or_create_team(conn, "acb", own_acb_id, own_info["name"], logo_url=own_info.get("logo_url"))

        # `match_date is None` (falta `startDateTime`, visto en algún partido sin fecha
        # confirmada todavía) se descarta: `upcoming_matchups.match_date` es NOT NULL,
        # y una fecha inventada sería peor que omitir ese partido hasta que se publique.
        matchups = [
            m
            for m in (build_scheduled_matchup(match, teams_by_id, own_acb_id) for match in matches)
            if m and m["match_date"]
        ]

        conn.execute(
            text("DELETE FROM upcoming_matchups WHERE season_id = :season_id AND competition_id = :competition_id"),
            {"season_id": season_id, "competition_id": competition_id},
        )
        for matchup in matchups:
            opponent_team_id = resolve_or_create_team(
                conn, "acb", matchup["opponent_acb_id"], matchup["opponent_name"],
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

    logger.info("ACB %s: %d partidos futuros del Baskonia cargados en upcoming_matchups", season, len(matchups))
    return {"season_id": season_id, "upcoming": len(matchups)}
