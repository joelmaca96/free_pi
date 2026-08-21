"""Scraper de la plantilla oficial de baskonia.com (roster + fotos).

Investigación real (verificada contra `https://www.baskonia.com/plantilla`
con una petición HTTP real, no solo inspección visual): la página es una SPA
Next.js, pero el HTML servido en la primera respuesta incluye un
`<script id="serverApp-state" type="application/json">` con el estado ya
resuelto de la consulta GraphQL/Strapi que alimenta la página — no hace
falta ejecutar JavaScript ni hay que reconstruir tarjetas de jugador por
HTML/CSS (mucho más frágil): basta con parsear ese JSON embebido. Ruta hasta
la plantilla dentro de ese JSON:

    data["page-plantilla-es-baskonia"]["extendedPage"]["page"]["data"]
        ["attributes"]["content"]  # lista de componentes de la página
        -> el componente con __typename == "ComponentSharedTeamPage"
        -> ["team"]["data"]["attributes"]["members"]["data"]  # plantilla completa

Cada miembro trae `team_member_role.data.attributes.label` (filtramos a
`"Jugador"`, excluyendo cuerpo técnico/médico/delegados) y
`team_member_position.data.attributes.label` (posición en español). El
`id` del miembro es un id numérico estable — se usa como `external_id` en
vez de un nombre normalizado (más fiable, no depende de la ortografía).

Campos disponibles: `dorsal` (puede ser `None` en fichajes recientes sin
número confirmado), `photo.data.attributes.url` (`None` si es la silueta
genérica `silueta_generica...webp`, no una foto real), `birthday` (fecha
ISO), `nationality`. **`height_cm` no está en este payload** (no hay ningún
campo de altura) y queda `NULL`.
"""
import json
import logging
import re
import urllib.robotparser
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.baskonia.com/plantilla"
USER_AGENT = "baskonia-scouting-bot/1.0 (+scouting Baskonia; respeta robots.txt)"

_STATE_SCRIPT_ID = "serverApp-state"
_PAGE_STATE_KEY = "page-plantilla-es-baskonia"
_TEAM_PAGE_COMPONENT = "ComponentSharedTeamPage"
_PLAYER_ROLE_LABEL = "Jugador"

# La web usa "Ala Pívot" (con espacio); normalizamos al guion que ya usan
# los demás datos del proyecto ("Ala-pívot").
_POSITION_LABEL_FIXUPS = {"Ala Pívot": "Ala-pívot"}

# Nombre de fichero del icono de silueta genérica que usa baskonia.com
# cuando un jugador no tiene foto subida todavía (no es una foto real).
_PLACEHOLDER_PHOTO_MARKERS = ("silueta_generica",)


@dataclass
class ScrapedPlayer:
    external_id: str
    name: str
    position: str
    number: Optional[int]
    photo_url: Optional[str]
    birth_date: Optional[str]
    nationality: Optional[str]


def _is_allowed_by_robots(session: requests.Session, url: str) -> bool:
    """Comprueba `robots.txt` antes de scrapear; si no se puede leer, asume permitido."""
    parser = urllib.robotparser.RobotFileParser()
    try:
        response = session.get(urljoin(url, "/robots.txt"), timeout=10)
        parser.parse(response.text.splitlines())
    except requests.RequestException:
        logger.warning("No se pudo leer robots.txt de %s; se asume scraping permitido.", url)
        return True
    return parser.can_fetch(USER_AGENT, url)


def fetch_roster_html(session: Optional[requests.Session] = None, url: str = ROSTER_URL) -> str:
    """Descarga el HTML de la página de plantilla, respetando `robots.txt`."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    if not _is_allowed_by_robots(session, url):
        raise PermissionError(f"robots.txt de {url} no permite scrapear esta ruta")

    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def _extract_server_state(html: str) -> Dict[str, Any]:
    """Extrae y parsea el JSON de `<script id="serverApp-state">` del HTML."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id=_STATE_SCRIPT_ID)
    if script is None or not script.string:
        raise ValueError(
            f"No se encontró <script id=\"{_STATE_SCRIPT_ID}\"> en el HTML de la plantilla "
            "(¿ha cambiado la estructura de la página?)"
        )
    return json.loads(script.string)


def _find_team_members(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_state = state.get(_PAGE_STATE_KEY)
    if page_state is None:
        raise ValueError(f"No se encontró la clave \"{_PAGE_STATE_KEY}\" en el estado embebido")

    content = page_state["extendedPage"]["page"]["data"]["attributes"]["content"]
    team_component = next((c for c in content if c.get("__typename") == _TEAM_PAGE_COMPONENT), None)
    if team_component is None:
        raise ValueError(f"No se encontró el componente \"{_TEAM_PAGE_COMPONENT}\" en la página")

    return team_component["team"]["data"]["attributes"]["members"]["data"]


def parse_roster(html: str) -> List[ScrapedPlayer]:
    """Extrae los jugadores (excluyendo cuerpo técnico/médico) del JSON embebido en el HTML."""
    state = _extract_server_state(html)
    members = _find_team_members(state)

    players = []
    for member in members:
        attrs = member["attributes"]
        role = attrs["team_member_role"]["data"]["attributes"]["label"]
        if role != _PLAYER_ROLE_LABEL:
            continue

        position_data = attrs["team_member_position"]["data"]
        position = position_data["attributes"]["label"] if position_data else ""
        position = _POSITION_LABEL_FIXUPS.get(position, position)

        photo_data = attrs.get("photo", {}).get("data")
        photo_url = photo_data["attributes"]["url"] if photo_data else None
        if photo_url and any(marker in photo_url for marker in _PLACEHOLDER_PHOTO_MARKERS):
            photo_url = None  # silueta genérica: no es una foto real del jugador
        elif photo_url:
            photo_url = urljoin(ROSTER_URL, photo_url)

        players.append(
            ScrapedPlayer(
                external_id=str(member["id"]),
                name=f"{attrs['name']} {attrs['lastName']}".strip(),
                position=position,
                number=attrs.get("dorsal"),
                photo_url=photo_url,
                birth_date=attrs.get("birthday"),
                nationality=attrs.get("nationality"),
            )
        )

    return players
