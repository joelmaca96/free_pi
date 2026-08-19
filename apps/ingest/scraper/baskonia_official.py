"""Scraper de la web oficial del Baskonia (baskonia.com).

Basketball-Reference no publica el calendario de la temporada siguiente
hasta que empieza (ver README, sección "Limitación de datos"), y tampoco
cubre competiciones como la Supercopa o la Euskal Kopa. La web oficial del
Baskonia sí tiene ya esos datos. Esta fuente se usa *solo* para completar
el calendario de próximos partidos y la plantilla actual: BBR sigue siendo
la única fuente de box scores y estadísticas avanzadas.

baskonia.com es una SPA en Angular sin datos en el HTML inicial; tanto el
calendario como la plantilla se sirven desde la API pública que la propia
página consulta al cargar (cms.deportivoalaves.com, un CMS -Strapi-
compartido por varios clubes del grupo). Es la misma petición que hace
cualquier visitante; no requiere autenticación.
"""
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

import requests

from packages.baskonia_core import config

from .ratelimit import throttle

logger = logging.getLogger(__name__)

API_BASE = "https://cms.deportivoalaves.com/api"
CMS_BASE = "https://cms.deportivoalaves.com"
TEAM_NAME = "Kosner Baskonia"

# Nombre de competición de la API -> valor de `games.league` a guardar.
# Liga Endesa y Euroleague se alinean con los valores ya usados para BBR
# ("acb"/"euroleague"); el resto (copas, pretemporada) se guarda tal cual
# (en minúsculas, sin espacios), ya que `games.league` es texto libre.
_LEAGUE_MAP = {
    "liga endesa": "acb",
    "euroleague": "euroleague",
    "copa del rey": "copa-del-rey",
    "supercopa": "supercopa",
}


def _headers() -> Dict[str, str]:
    return {"User-Agent": config.USER_AGENT}


def _to_league(competition_name: Optional[str]) -> str:
    if not competition_name:
        return "baskonia.com"
    key = competition_name.strip().lower()
    return _LEAGUE_MAP.get(key, key.replace(" ", "-"))


def _to_bbr_style_date(iso_date: str) -> str:
    """Convierte 'AAAA-MM-DD' al mismo formato de fecha que usa BBR ('Sat, Sep 19, 2026')."""
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%a, %b %d, %Y")


def fetch_games(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> List[Dict[str, object]]:
    """Descarga los partidos del Baskonia en un rango de fechas (histórico y próximos).

    Args:
        from_date: fecha inicial (inclusive). Por defecto, hoy.
        to_date: fecha final (inclusive). Si se pasa, se añade el filtro
            `gameDate[$lte]`; si no, solo se filtra por `$gte` (comportamiento
            actual de `fetch_upcoming_games`).

    Returns:
        Lista de partidos con la misma forma que produce
        `parser.parse_schedule_games` (`date`, `opponent`, `opponent_slug`,
        `boxscore_url`, `is_home`, `points`, `opp_points`, `notes`,
        `league`), lista para pasar directamente a `main.persist_schedule()`.
        `opponent_slug` siempre es `None`: esta fuente no da un slug de BBR,
        así que la resolución del equipo rival cae en el emparejamiento por
        nombre de `main.resolve_opponent_team()`.

    Raises:
        requests.RequestException: si falla la petición a la API.
    """
    from_date = from_date or date.today()
    params = {
        "populate[homeTeam]": "*",
        "populate[awayTeam]": "*",
        "populate[competition]": "*",
        "filters[$or][0][homeTeam][name][$eq]": TEAM_NAME,
        "filters[$or][1][awayTeam][name][$eq]": TEAM_NAME,
        "filters[gameDate][$gte]": from_date.isoformat(),
        "sort[0]": "gameDate:asc",
        "pagination[pageSize]": 200,
        "locale": "es",
    }
    if to_date is not None:
        params["filters[gameDate][$lte]"] = to_date.isoformat()

    logger.info(
        "Obteniendo calendario oficial de baskonia.com desde %s hasta %s",
        from_date.isoformat(),
        to_date.isoformat() if to_date else "∞",
    )
    throttle(API_BASE)
    response = requests.get(f"{API_BASE}/games-items", params=params, headers=_headers(), timeout=config.TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    games: List[Dict[str, object]] = []
    for item in payload.get("data", []):
        attrs = item.get("attributes", {})
        home = attrs.get("homeTeam", {}).get("data")
        away = attrs.get("awayTeam", {}).get("data")
        if home is None or away is None:
            continue  # fila incompleta (raro, pero la API lo permite)

        home_name = home["attributes"]["name"]
        away_name = away["attributes"]["name"]
        is_home = home_name == TEAM_NAME
        opponent_name = away_name if is_home else home_name

        competition = attrs.get("competition", {}).get("data")
        competition_name = competition["attributes"]["name"] if competition else None

        games.append(
            {
                "date": _to_bbr_style_date(attrs["gameDate"]),
                "opponent": opponent_name,
                "opponent_slug": None,
                "boxscore_url": None,
                "is_home": is_home,
                "points": attrs.get("homeScore") if is_home else attrs.get("awayScore"),
                "opp_points": attrs.get("awayScore") if is_home else attrs.get("homeScore"),
                "notes": None,
                "league": _to_league(competition_name),
            }
        )
    return games


def fetch_upcoming_games(from_date: Optional[date] = None) -> List[Dict[str, object]]:
    """Descarga los próximos partidos del Baskonia desde baskonia.com.

    Wrapper de `fetch_games` sin `to_date` (solo partidos desde `from_date`),
    para no romper a los consumidores existentes (`main.py`/`app.py`).

    Args:
        from_date: fecha a partir de la cual se consideran partidos
            "próximos" (por defecto, hoy).

    Returns:
        Lista de partidos con la misma forma que `fetch_games`.

    Raises:
        requests.RequestException: si falla la petición a la API.
    """
    return fetch_games(from_date=from_date)


def fetch_current_roster() -> List[Dict[str, object]]:
    """Descarga la plantilla actual de jugadores del primer equipo desde baskonia.com.

    A diferencia del roster de BBR (solo se actualiza con `--refresh-teams`
    y refleja lo que había la última vez que se pidió), esta fuente siempre
    da la plantilla tal cual está publicada ahora mismo en la web oficial,
    incluidos fichajes recientes que BBR aún no tiene, y trae la foto de
    cada jugador (BBR no tiene fotos).

    Returns:
        Lista de dicts con `name` (nombre completo), `position` (en
        castellano, p.ej. "Ala Pívot"), `number` (dorsal, como texto) y
        `photo_url` (URL completa a una miniatura, o `None` si no tiene).

    Raises:
        requests.RequestException: si falla la petición a la API.
    """
    params = {
        "populate[photo]": "*",
        "populate[team_member_position]": "*",
        "populate[team_member_role]": "*",
        "pagination[pageSize]": 50,
        "locale": "es",
        "filters[team][name][$eq]": TEAM_NAME,
        "filters[team_member_role][key][$eq]": "Player",
    }
    logger.info("Obteniendo plantilla actual de baskonia.com")
    throttle(API_BASE)
    response = requests.get(f"{API_BASE}/team-members", params=params, headers=_headers(), timeout=config.TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    players: List[Dict[str, object]] = []
    for item in payload.get("data", []):
        attrs = item.get("attributes", {})
        full_name = f"{(attrs.get('name') or '').strip()} {(attrs.get('lastName') or '').strip()}".strip()
        if not full_name:
            continue

        position_data = (attrs.get("team_member_position") or {}).get("data")
        position = position_data["attributes"]["label"] if position_data else None

        photo_data = (attrs.get("photo") or {}).get("data")
        photo_url = None
        if photo_data:
            photo_attrs = photo_data["attributes"]
            rel_url = (photo_attrs.get("formats") or {}).get("small", {}).get("url") or photo_attrs.get("url")
            if rel_url:
                photo_url = f"{CMS_BASE}{rel_url}"

        dorsal = attrs.get("dorsal")
        players.append(
            {
                "name": full_name,
                "position": position,
                "number": str(dorsal) if dorsal is not None else None,
                "photo_url": photo_url,
            }
        )
    return players
