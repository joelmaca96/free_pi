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

HALLAZGO (2026-08-24, verificado en vivo): `photo.data.attributes.url` viene
SIEMPRE como ruta relativa (`/uploads/xxx.png`), y resolverla contra
`www.baskonia.com` (el host de `ROSTER_URL`, lo que hacía este módulo antes
de este hallazgo) da 200 pero devuelve el HTML de la propia SPA Angular, no
la imagen — esa ruta no está servida por ese host, la SPA la intercepta como
si fuera una ruta de cliente más y responde con su propio `index.html`. La
página completa (no el JSON embebido) referencia en otro sitio
`https://cms.deportivoalaves.com` — el backend Strapi real, compartido con
la web del Deportivo Alavés (mismo grupo propietario) — y las mismas rutas
`/uploads/xxx.png` SÍ sirven la imagen real (`Content-Type: image/...`)
contra ESE host. Confirmado con varios ficheros distintos (jpg/png) antes de
fijar `MEDIA_BASE_URL`. Sin este fix, `download_player_photos` descartaba
TODAS las fotos (`Content-Type` no-imagen, ver ahí) — y el hotlink directo
que hacía la interfaz antes de tener descarga local también estaba roto
desde siempre, solo que nadie lo había notado.

FOTOS A DISCO (`download_player_photos`): `photo_url` es una URL de
baskonia.com — servir la plantilla haciendo *hotlink* directo a esa URL
(como hace hoy `app/components/avatar.py`) ata la disponibilidad de la
interfaz a que baskonia.com esté arriba. Esta función descarga el binario a
`BASKONIA_WEB_PHOTOS_DIR` (por defecto `data/player_photos/`, al lado de
`data/baskonia.db` — mismo volumen que monta `app/` en `docker-compose.yml`)
y rellena `ScrapedPlayer.photo_local_path` con la ruta resultante. Nombrado
por `external_id` (el id estable de baskonia.com, no el `players.id`
interno que resuelve `ingest/common/identity.py` más tarde) — así una
descarga ya hecha se reconoce y se salta en la siguiente ejecución sin negociar
con `loader.py` qué `players.id` le corresponde. Un fallo puntual (URL caída,
timeout, contenido que no es imagen) se registra y se salta ese jugador, no
tumba el resto de la plantilla — mismo principio que `advanced_stats`/
`match_header` opcionales en `ingest/acb/client.py`.
"""
import json
import logging
import os
import re
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.baskonia.com/plantilla"
USER_AGENT = "baskonia-scouting-bot/1.0 (+scouting Baskonia; respeta robots.txt)"

# Backend real de medios (Strapi), NO `www.baskonia.com` — ver hallazgo en el
# docstring del módulo. Overridable por si cambia de host en el futuro.
MEDIA_BASE_URL = os.getenv("BASKONIA_WEB_MEDIA_BASE_URL", "https://cms.deportivoalaves.com")

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

DEFAULT_PHOTOS_DIR = os.getenv("BASKONIA_WEB_PHOTOS_DIR", "data/player_photos")
_PHOTOS_REQUEST_TIMEOUT = 30

# Extensiones soportadas -> Content-Type real devuelto por baskonia.com,
# verificado en vivo (CDN Strapi sirve jpg/png/webp según lo que se subió).
_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass
class ScrapedPlayer:
    external_id: str
    name: str
    position: str
    number: Optional[int]
    photo_url: Optional[str]
    birth_date: Optional[str]
    nationality: Optional[str]
    # Rellenado por `download_player_photos` (no por `parse_roster`) — `None`
    # hasta que se llama esa función, o si no hay foto real / la descarga falló.
    photo_local_path: Optional[str] = None


# Cache de `robots.txt` ya parseado por origen (`scheme://netloc`), para no
# volver a pedirlo por cada foto de la plantilla (~15-20 jugadores, mismo
# host que la página ya comprobada en `fetch_roster_html`). Vive a nivel de
# módulo porque una ejecución del pipeline es un proceso corto de un solo uso
# (CLI/cron) — no hay motivo para invalidarlo dentro de esa vida.
_robots_cache: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}


def _robots_parser_for(session: requests.Session, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
    """Parser de `robots.txt` del origen de `url`, cacheado; `None` si no se pudo leer
    (se trata como "permitido", igual que `_is_allowed_by_robots`)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in _robots_cache:
        return _robots_cache[origin]

    parser = urllib.robotparser.RobotFileParser()
    try:
        response = session.get(urljoin(origin, "/robots.txt"), timeout=10)
        parser.parse(response.text.splitlines())
    except requests.RequestException:
        logger.warning("No se pudo leer robots.txt de %s; se asume scraping permitido.", origin)
        parser = None
    _robots_cache[origin] = parser
    return parser


def _is_allowed_by_robots(session: requests.Session, url: str) -> bool:
    """Comprueba `robots.txt` antes de scrapear; si no se puede leer, asume permitido."""
    parser = _robots_parser_for(session, url)
    return parser is None or parser.can_fetch(USER_AGENT, url)


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
            # `MEDIA_BASE_URL`, no `ROSTER_URL` — ver hallazgo en el docstring del módulo.
            photo_url = urljoin(MEDIA_BASE_URL, photo_url)

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


def _guess_extension(photo_url: str, content_type: Optional[str]) -> str:
    """Extensión de fichero a partir del `Content-Type` real (preferido) o, si falta/no
    se reconoce, del sufijo de la URL — con `.jpg` como último recurso, nunca falla."""
    if content_type:
        ext = _EXTENSION_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    suffix = Path(urlparse(photo_url).path).suffix.lower()
    return suffix if suffix in _EXTENSION_BY_CONTENT_TYPE.values() else ".jpg"


def _existing_photo_path(photos_dir: Path, external_id: str) -> Optional[Path]:
    """Fichero ya descargado para `external_id`, con cualquiera de las extensiones
    soportadas — evita volver a pedir por red una foto ya en disco."""
    for ext in _EXTENSION_BY_CONTENT_TYPE.values():
        candidate = photos_dir / f"{external_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def download_player_photos(
    players: List[ScrapedPlayer],
    session: Optional[requests.Session] = None,
    photos_dir: str = DEFAULT_PHOTOS_DIR,
) -> List[ScrapedPlayer]:
    """Descarga a disco la foto real de cada jugador y rellena `photo_local_path` (in-place,
    también devuelve `players` por conveniencia del caller).

    Idempotente: si ya existe un fichero para `external_id` en `photos_dir` (de una
    ejecución anterior), no vuelve a pedirlo por red — solo `players` nuevos o cuya foto
    aún no se había descargado generan tráfico. Jugadores sin `photo_url` (silueta
    genérica) se saltan sin tocar la red. Un fallo de red/robots.txt/contenido no-imagen
    en UN jugador se registra y se salta — no interrumpe la descarga del resto (ver
    docstring del módulo).
    """
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    photos_path = Path(photos_dir)

    for player in players:
        if not player.photo_url:
            continue

        existing = _existing_photo_path(photos_path, player.external_id)
        if existing is not None:
            player.photo_local_path = str(existing)
            continue

        if not _is_allowed_by_robots(session, player.photo_url):
            logger.warning(
                "baskonia_web: robots.txt no permite descargar la foto de %s (%s)",
                player.name, player.photo_url,
            )
            continue

        try:
            response = session.get(player.photo_url, timeout=_PHOTOS_REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "baskonia_web: no se pudo descargar la foto de %s (%s): %s", player.name, player.photo_url, exc
            )
            continue

        content_type = response.headers.get("Content-Type")
        if content_type and not content_type.split(";")[0].strip().lower().startswith("image/"):
            logger.warning(
                "baskonia_web: %s no devolvió una imagen (Content-Type=%s), se descarta",
                player.photo_url, content_type,
            )
            continue

        photos_path.mkdir(parents=True, exist_ok=True)
        target = photos_path / f"{player.external_id}{_guess_extension(player.photo_url, content_type)}"
        # Escritura atómica (fichero temporal + rename): si el proceso se interrumpe a
        # mitad de escritura, `_existing_photo_path` nunca ve un fichero a medias.
        tmp_target = target.with_name(target.name + ".part")
        tmp_target.write_bytes(response.content)
        tmp_target.replace(target)
        player.photo_local_path = str(target)

    return players
