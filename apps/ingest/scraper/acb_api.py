"""Scraper de la API pública de la ACB (acb.com) — fuente de backup.

La ACB expone una API JSON pública (sin autenticación) con calendario y
resultados de la Liga Endesa y la Copa del Rey. Se usa como fuente
independiente de la ACB para validar/completar los partidos de liga y copa,
por debajo de RealGM, BBR y CMS en la prioridad de fusión.

El contrato de salida es el mismo que el resto de fuentes de `scraper/`:
dicts planos normalizados, sin imports de `db/` (regla de capas).

NOTA DE VIABILIDAD: los endpoints exactos y el esquema JSON de la API de la
ACB deben verificarse en desarrollo (puede requerir ajustar rutas/campos).
El contrato de salida es estable (dict plano normalizado); solo cambia el
mapeo interno. Si la API no fuera accesible, RealGM+BBR+CMS cubren el
backfill y la feature no queda bloqueada.
"""
import logging
from typing import Dict, List, Optional

import requests

from packages.baskonia_core import config

logger = logging.getLogger(__name__)

ACB_API_BASE = "https://www.acb.com/api"

# Mapa de competición de la API ACB -> valor de `games.league` a guardar.
# (Los nombres exactos de competición de la API se verifican en desarrollo.)
_LEAGUE_MAP = {
    "liga endesa": "acb",
    "copa del rey": "copa-del-rey",
}


def _headers() -> Dict[str, str]:
    """Devuelve las cabeceras HTTP por defecto para la API de la ACB."""
    return {"User-Agent": config.USER_AGENT, "Accept": "application/json"}


def fetch_team_games(team_name: str, season: int) -> List[Dict[str, object]]:
    """Descarga los partidos de un equipo en una temporada desde la API de la ACB.

    Args:
        team_name: Nombre del equipo tal como lo usa la API de la ACB
            (p.ej. "Baskonia").
        season: Año de inicio de la temporada (p.ej. 2025 para 2025-26).

    Returns:
        Lista de partidos normalizados al contrato plano de `scraper/`
        (`date`, `opponent`, `opponent_slug`, `boxscore_url`, `is_home`,
        `points`, `opp_points`, `notes`, `league`). `opponent_slug` siempre
        `None` (esta fuente no da slug de BBR); `league` es `acb` o
        `copa-del-rey` según la competición del partido.

    Raises:
        requests.RequestException: si falla la petición a la API.
    """
    # TODO(desarrollo): verificar el endpoint real de la API de la ACB para
    # el calendario/resultados de un equipo en una temporada. La ACB expone
    # varios endpoints públicos (p.ej. /api/teams, /api/games); el esquema
    # exacto se confirma en desarrollo.
    url = f"{ACB_API_BASE}/teams/{team_name}/games"
    params = {"season": season}
    logger.info("Obteniendo partidos de %s (temporada %s) desde la API ACB", team_name, season)
    response = requests.get(url, params=params, headers=_headers(), timeout=config.TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    games: List[Dict[str, object]] = []
    for item in payload if isinstance(payload, list) else payload.get("data", []):
        # TODO(desarrollo): mapear los campos reales de la API ACB al
        # contrato plano de `scraper/`. Estructura esperada (a verificar):
        #   date, home/away (nombres), home_score/away_score, competition.
        game = _map_game(item, team_name)
        if game is not None:
            games.append(game)
    return games


def _map_game(item: Dict[str, object], team_name: str) -> Optional[Dict[str, object]]:
    """Mapea un partido de la API ACB al contrato plano de `scraper/`.

    El esquema exacto de la API de la ACB se verifica en desarrollo; este
    mapeo es tolerante a variantes de nombres de campo (home/away,
    homeTeam/awayTeam, local/visitante, etc.) y devuelve `None` si el item
    no tiene los campos mínimos (fecha y rival).

    Args:
        item: Diccionario crudo de un partido de la API ACB.
        team_name: Nombre del equipo objetivo.

    Returns:
        Partido normalizado, o None si el item no tiene los campos mínimos.
    """
    date_str = _first_value(item, "date", "fecha", "gameDate", "game_date")
    if not date_str:
        return None

    home = _first_value(item, "home", "homeTeam", "home_team", "local")
    away = _first_value(item, "away", "awayTeam", "away_team", "visitante")
    if not home or not away:
        return None

    home_name = _team_name(home)
    away_name = _team_name(away)
    if not home_name or not away_name:
        return None

    is_home = _name_matches(home_name, team_name)
    opponent = away_name if is_home else home_name

    home_score = _to_int(_first_value(item, "homeScore", "home_score", "localScore"))
    away_score = _to_int(_first_value(item, "awayScore", "away_score", "visitanteScore"))
    if is_home:
        points, opp_points = home_score, away_score
    else:
        points, opp_points = away_score, home_score

    competition = _first_value(item, "competition", "competitionName", "competicion")
    league = _LEAGUE_MAP.get(str(competition or "").strip().lower(), "acb")

    return {
        "date": str(date_str),
        "opponent": opponent,
        "opponent_slug": None,  # la API ACB no da slug de BBR
        "boxscore_url": None,   # la API ACB no da enlace a box score
        "is_home": is_home,
        "points": points,
        "opp_points": opp_points,
        "notes": None,
        "league": league,
        "season": None,  # se rellena en el orquestador
    }


def _first_value(item: Dict[str, object], *keys: str) -> object:
    """Devuelve el primer valor no vacío de una lista de claves candidatas."""
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def _team_name(value: object) -> Optional[str]:
    """Extrae el nombre de un equipo de un valor de la API (string o dict)."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "nombre", "teamName", "team_name"):
            name = value.get(key)
            if name:
                return str(name).strip()
    return None


def _name_matches(name: str, target: str) -> bool:
    """Compara dos nombres de equipo de forma tolerante (case-insensitive)."""
    return name.strip().lower() == target.strip().lower()


def _to_int(value: object) -> Optional[int]:
    """Convierte un valor a entero de forma segura."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None
