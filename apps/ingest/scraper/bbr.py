"""Funciones específicas de Basketball-Reference.

Construye las URLs de las páginas de BBR y orquesta las llamadas
al cliente HTTP + parser para obtener los datos de ligas, equipos
y partidos.
"""
import logging
from typing import Dict, List, Optional

from packages.baskonia_core import config
from .client import BBRClient
from . import parser

logger = logging.getLogger(__name__)


def _league_url(league: str, season: Optional[int] = None) -> str:
    """Construye la URL de la página de una liga.

    Args:
        league: Clave de la liga ('acb' o 'euroleague').
        season: Año de finalización de la temporada (opcional).

    Returns:
        URL de la página de la liga.
    """
    slug = config.LEAGUE_SLUGS[league]
    if season:
        return f"{config.BBR_INTERNATIONAL}/{slug}/{season}.html"
    return f"{config.BBR_INTERNATIONAL}/{slug}/"


def _team_url(team_slug: str, season: int) -> str:
    """Construye la URL de la página de un equipo.

    Args:
        team_slug: Slug del equipo en BBR (p.ej. 'vitoria').
        season: Año de finalización de la temporada.

    Returns:
        URL de la página del equipo.
    """
    return f"{config.BBR_INTERNATIONAL}/teams/{team_slug}/{season}.html"


def _schedule_url(team_slug: str, season: int) -> str:
    """Construye la URL de la página de calendario de un equipo.

    En BBR internacional, el calendario está en una página separada de la
    página de stats del equipo.

    Args:
        team_slug: Slug del equipo en BBR (p.ej. 'vitoria').
        season: Año de finalización de la temporada.

    Returns:
        URL de la página de calendario del equipo.
    """
    return f"{config.BBR_INTERNATIONAL}/schedules/{team_slug}/{season}.html"


def fetch_standings(client: BBRClient, league: str, season: int) -> List[Dict[str, str]]:
    """Obtiene la clasificación de una liga.

    Args:
        client: Cliente HTTP.
        league: Clave de la liga.
        season: Año de la temporada.

    Returns:
        Lista de filas de clasificación.
    """
    url = _league_url(league, season)
    logger.info("Obteniendo clasificación de %s %s desde %s", league, season, url)
    response = client.get(url)
    return parser.parse_standings(response.text)


def fetch_team(client: BBRClient, team_slug: str, season: int) -> Dict[str, object]:
    """Obtiene los datos de un equipo (roster y calendario).

    En BBR internacional, el roster (stats de jugadores) está en la página del
    equipo (`/international/teams/<slug>/<season>.html`) y el calendario en una
    página separada (`/international/schedules/<slug>/<season>.html`). Esta
    función obtiene ambas y las combina.

    Args:
        client: Cliente HTTP.
        team_slug: Slug del equipo.
        season: Año de la temporada.

    Returns:
        Diccionario con 'roster', 'schedule' y 'html' (HTML de la página de
        calendario, que es la que contiene los partidos).
    """
    # Página del equipo (roster / stats de jugadores)
    team_url = _team_url(team_slug, season)
    logger.info("Obteniendo roster del equipo %s desde %s", team_slug, team_url)
    team_response = client.get(team_url)
    data = parser.parse_team_page(team_response.text)

    # Página de calendario (partidos y box scores)
    schedule_url = _schedule_url(team_slug, season)
    logger.info("Obteniendo calendario del equipo %s desde %s", team_slug, schedule_url)
    schedule_response = client.get(schedule_url)
    data["html"] = schedule_response.text
    return data


def fetch_boxscore(client: BBRClient, boxscore_url: str) -> Dict[str, object]:
    """Obtiene el box score de un partido.

    Args:
        client: Cliente HTTP.
        boxscore_url: URL relativa del box score.

    Returns:
        Diccionario con las stats de ambos equipos.
    """
    url = f"{config.BBR_BASE}{boxscore_url}"
    logger.info("Obteniendo box score desde %s", url)
    response = client.get(url)
    return parser.parse_boxscore(response.text)
