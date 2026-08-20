"""Scraper de RealGM (basketball.realgm.com) — fuente principal de partidos.

RealGM es la fuente PRINCIPAL de calendario/resultados/box scores/game logs
del pipeline (especialmente para Euroliga), quedando BBR, CMS baskonia.com y
la API ACB como backup. Este módulo scrapea HTML público con
`requests` + `BeautifulSoup`, sin autenticación.

Estructura de URLs de RealGM (verificada en desarrollo):
- Calendario de una liga por fecha:
  `https://basketball.realgm.com/international/league/<id>/<slug>/schedules/<YYYY-MM-DD>`
  La tabla tiene cabeceras "Away Team", "Home Team" y "Venue".
- Equipos de una liga:
  `https://basketball.realgm.com/international/league/<id>/<slug>/teams`
  La tabla tiene cabecera "Team" y enlaces a "Rosters".
- Game logs de un jugador: la URL de la página del jugador con "Summary"
  sustituido por "GameLogs". La tabla tiene cabeceras "Date" y "Opponent".

RealGM oculta algunas tablas dentro de comentarios HTML (truco anti-scraping),
por lo que se reutiliza el patrón de `parser._find_all_tables` para extraerlas.

El contrato de salida es el mismo que el resto de fuentes de `scraper/`:
dicts planos normalizados, sin imports de `db/` (regla de capas).
"""
import logging
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Comment

from packages.baskonia_core import config

from .ratelimit import throttle

logger = logging.getLogger(__name__)

# RealGM protege `/international/*` con un challenge de Cloudflare que
# `requests` no puede resolver (no ejecuta JS ni presenta un fingerprint TLS
# de navegador). Si `curl_cffi` está instalado se usa una sesión que imita el
# fingerprint TLS de Chrome, que suele pasar el challenge; si no, se cae a
# `requests` (comportamiento original). `curl_cffi` es una dependencia
# opcional para no romper el pipeline en entornos donde no esté instalada.
try:
    from curl_cffi import requests as cf_requests

    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover - depende del entorno
    cf_requests = None
    _HAS_CURL_CFFI = False

REALGM_BASE = "https://basketball.realgm.com"


def _new_session() -> object:
    """Crea una sesión HTTP para RealGM con el User-Agent configurado.

    Usa `curl_cffi` (fingerprint TLS de Chrome) si está disponible para
    intentar pasar el challenge de Cloudflare de `/international/*`; si no,
    cae a `requests`. Ambas sesiones comparten la API `.get(url, timeout=)`.

    Returns:
        Sesión HTTP lista para usar (curl_cffi o requests).
    """
    if _HAS_CURL_CFFI:
        session = cf_requests.Session(impersonate="chrome")
    else:
        session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session

# Mapa de competición canónica -> (league id, slug) de RealGM.
# RealGM usa un id y slug propios por competición; la competición se conoce
# por el endpoint consultado.
_LEAGUE_ENDPOINTS = {
    "euroleague": (1, "Euroleague"),
    "acb": (2, "Liga-ACB"),
}

# Mapa de competición canónica -> nombre de competición en RealGM (para
# filtrar partidos cuando una página mezcla competiciones).
_LEAGUE_NAMES = {
    "euroleague": "Euroleague",
    "acb": "Liga ACB",
}

# Cabeceras de la tabla de calendario de RealGM (por orden de columnas).
_SCHEDULE_HEADERS = ["Away Team", "Home Team", "Venue"]

# Normalización de cabeceras de las tablas de stats de jugador de RealGM a
# las claves canónicas que esperan `upsert_boxscore`/`upsert_player_game_log`
# (mismo contrato que BBR). RealGM usa abreviaturas propias (MIN, TO, FGM,
# 3PM, FTM...); se mapean a las claves del resto de fuentes.
_HEADER_ALIASES = {
    "MIN": "MP",
    "TO": "TOV",
    "FGM": "FG",
    "FGA": "FGA",
    "3PM": "3P",
    "3PA": "3PA",
    "FTM": "FT",
    "FTA": "FTA",
    "REB": "TRB",
    "OREB": "ORB",
    "DREB": "DRB",
    "PTS": "PTS",
    "AST": "AST",
    "STL": "STL",
    "BLK": "BLK",
    "PF": "PF",
    "GS": "GS",
    "+/-": "+/-",
}


def _league_endpoint(league: str) -> Tuple[int, str]:
    """Devuelve el (league id, slug) de RealGM para una competición canónica.

    Args:
        league: Competición canónica ('euroleague' o 'acb').

    Returns:
        Tupla (league_id, slug) de RealGM.

    Raises:
        ValueError: si la competición no está soportada por RealGM.
    """
    if league not in _LEAGUE_ENDPOINTS:
        raise ValueError(f"RealGM no soporta la competición '{league}'")
    return _LEAGUE_ENDPOINTS[league]


def _schedule_url(league: str, day: date) -> str:
    """Construye la URL del calendario de una liga para un día concreto.

    Args:
        league: Competición canónica.
        day: Fecha del día a consultar.

    Returns:
        URL del calendario de ese día.
    """
    league_id, slug = _league_endpoint(league)
    return (
        f"{REALGM_BASE}/international/league/{league_id}/{slug}/schedules/"
        f"{day.isoformat()}"
    )


def _teams_url(league: str) -> str:
    """Construye la URL de la página de equipos de una liga.

    Args:
        league: Competición canónica.

    Returns:
        URL de la página de equipos.
    """
    league_id, slug = _league_endpoint(league)
    return f"{REALGM_BASE}/international/league/{league_id}/{slug}/teams"


def _find_all_tables(soup: BeautifulSoup) -> List[object]:
    """Devuelve todas las tablas de la página, incluidas las ocultas en
    comentarios HTML (truco anti-scraping habitual en RealGM).

    Args:
        soup: Documento parseado.

    Returns:
        Lista de elementos de tabla (visibles + ocultos en comentarios).
    """
    tables = list(soup.find_all("table"))
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if "<table" in comment:
            tables.extend(BeautifulSoup(comment, "html.parser").find_all("table"))
    return tables


def _table_headers(table) -> List[str]:
    """Devuelve las cabeceras de una tabla como lista de textos.

    Args:
        table: Elemento BeautifulSoup de la tabla.

    Returns:
        Lista de textos de las celdas de cabecera.
    """
    headers = []
    header_row = table.find("thead")
    if header_row:
        headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
    return headers


def _find_schedule_table(soup: BeautifulSoup):
    """Localiza la tabla de calendario/resultados en una página de RealGM.

    La tabla de calendario tiene cabeceras "Away Team" y "Home Team".

    Args:
        soup: Documento parseado.

    Returns:
        Elemento de tabla o None si no se encuentra.
    """
    for table in _find_all_tables(soup):
        headers = _table_headers(table)
        if "Away Team" in headers and "Home Team" in headers:
            return table
    return None


def _parse_schedule_table(table, league: str, team_name: str) -> List[Dict[str, object]]:
    """Parsea la tabla de calendario de RealGM filtrando por equipo.

    Args:
        table: Elemento BeautifulSoup de la tabla de calendario.
        league: Competición canónica de la página.
        team_name: Nombre del equipo tal como lo usa RealGM (p.ej. "Baskonia").

    Returns:
        Lista de partidos normalizados al contrato plano de `scraper/`.
    """
    games: List[Dict[str, object]] = []
    body = table.find("tbody")
    if body is None:
        body = table

    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue

        away_text = cells[0].get_text(strip=True)
        home_text = cells[2].get_text(strip=True)

        # Solo nos interesan los partidos del equipo objetivo.
        if team_name not in (away_text, home_text):
            continue

        is_home = home_text == team_name
        opponent = away_text if is_home else home_text

        # Enlace al box score: en RealGM el enlace suele estar en la celda
        # del equipo local o en la columna de resultado. Se busca cualquier
        # enlace que apunte a una página de box score.
        boxscore_url = ""
        for cell in cells:
            link = cell.find("a", href=True)
            if link and "/boxscores/" in link["href"]:
                boxscore_url = link["href"]
                break

        # Resultado: RealGM muestra "W 85-70" o "L 70-85" en una columna.
        # Se intenta extraer los puntos de la celda de resultado si existe.
        points = None
        opp_points = None
        result_text = ""
        if len(cells) > 3:
            result_text = cells[3].get_text(strip=True)
        match = re.search(r"(\d+)\s*-\s*(\d+)", result_text)
        if match:
            if is_home:
                points, opp_points = match.group(1), match.group(2)
            else:
                opp_points, points = match.group(1), match.group(2)

        games.append(
            {
                "date": None,  # se rellena en _fetch_team_schedule con la fecha del día
                "opponent": opponent,
                "opponent_slug": None,  # RealGM no usa slugs de BBR
                "boxscore_url": boxscore_url,
                "is_home": is_home,
                "points": points,
                "opp_points": opp_points,
                "notes": None,
                "league": league,
                "season": None,  # se rellena en _fetch_team_schedule
            }
        )

    return games


def _fetch_schedule_day(
    session: object,
    league: str,
    day: date,
    team_name: str,
    season: int,
) -> List[Dict[str, object]]:
    """Descarga y parsea el calendario de un día concreto para un equipo.

    Args:
        session: Sesión HTTP reutilizada.
        league: Competición canónica.
        day: Fecha del día a consultar.
        team_name: Nombre del equipo en RealGM.
        season: Año de inicio de la temporada.

    Returns:
        Lista de partidos del equipo ese día (normalizados).
    """
    url = _schedule_url(league, day)
    logger.debug("RealGM: consultando calendario %s de %s", league, day)
    throttle(url)
    response = session.get(url, timeout=config.TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    table = _find_schedule_table(soup)
    if table is None:
        return []
    games = _parse_schedule_table(table, league, team_name)
    for game in games:
        game["date"] = day.isoformat()
        game["season"] = season
    return games


def fetch_team_schedule(
    team_name: str,
    season: int,
    league: str,
    session: Optional[object] = None,
) -> List[Dict[str, object]]:
    """Descarga el calendario/resultados de un equipo en una temporada desde RealGM.

    RealGM publica el calendario por día (una página por fecha), no por equipo
    y temporada. Para cubrir una temporada completa se itera sobre el rango de
    fechas de la temporada (de octubre a junio, con margen) consultando cada
    día y filtrando por el equipo objetivo.

    Args:
        team_name: Nombre del equipo tal como lo usa RealGM (p.ej. "Baskonia").
        season: Año de inicio de la temporada (p.ej. 2025 para 2025-26).
        league: Competición canónica ('euroleague' o 'acb').
        session: Sesión HTTP opcional para reutilizar conexiones.

    Returns:
        Lista de partidos normalizados al contrato plano de `scraper/`
        (`date`, `opponent`, `opponent_slug`, `boxscore_url`, `is_home`,
        `points`, `opp_points`, `notes`, `league`, `season`).

    Raises:
        requests.RequestException: si falla la petición a RealGM.
    """
    own_session = session is None
    session = session or _new_session()

    # Rango de fechas de la temporada: de octubre del año de inicio a junio
    # del año siguiente (con margen para pretemporada/playoffs).
    start = date(season, 10, 1)
    end = date(season + 1, 6, 30)

    all_games: List[Dict[str, object]] = []
    day = start
    while day <= end:
        try:
            all_games.extend(_fetch_schedule_day(session, league, day, team_name, season))
        except requests.RequestException as exc:
            logger.warning("RealGM: error en %s: %s", day, exc)
        day += timedelta(days=1)

    if own_session:
        session.close()

    logger.info("RealGM: %d partidos de %s en %s %s", len(all_games), team_name, league, season)
    return all_games


def fetch_game_boxscore(
    boxscore_url: str,
    session: Optional[object] = None,
) -> Dict[str, object]:
    """Descarga el box score completo de un partido desde RealGM.

    Args:
        boxscore_url: URL (absoluta o relativa) del box score en RealGM.
        session: Sesión HTTP opcional para reutilizar conexiones.

    Returns:
        Dict con 'home' y 'away', cada uno con la lista de stats por jugador
        (game log) y los totales de equipo. Mismo shape que
        `scraper.parser.parse_boxscore` para reutilizar `upsert_boxscore`.

    Raises:
        requests.RequestException: si falla la petición a RealGM.
    """
    own_session = session is None
    session = session or _new_session()

    url = boxscore_url if boxscore_url.startswith("http") else f"{REALGM_BASE}{boxscore_url}"
    logger.info("RealGM: descargando box score desde %s", url)
    throttle(url)
    response = session.get(url, timeout=config.TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # Los box scores de RealGM tienen dos tablas de stats por jugador
    # (local y visitante). Se identifican por sus cabeceras.
    result: Dict[str, object] = {"home": [], "away": []}
    tables = _find_all_tables(soup)
    # RealGM suele mostrar primero la tabla del equipo local y luego la del
    # visitante; se asignan por orden de aparición de tablas con cabecera
    # de jugador.
    player_tables = []
    for table in tables:
        headers = _table_headers(table)
        if "Player" in headers and "PTS" in headers:
            player_tables.append(table)

    if len(player_tables) >= 1:
        result["home"] = _parse_player_table(player_tables[0])
    if len(player_tables) >= 2:
        result["away"] = _parse_player_table(player_tables[1])

    if own_session:
        session.close()
    return result


def _parse_player_table(table) -> List[Dict[str, object]]:
    """Parsea una tabla de stats por jugador de un box score de RealGM.

    Normaliza las cabeceras de RealGM (MIN, TO, FGM, 3PM, FTM...) a las
    claves canónicas del contrato de `scraper/` (MP, TOV, FG, 3P, FT...)
    para que el resultado sea directamente compatible con
    `upsert_boxscore`/`upsert_player_game_log`.

    Args:
        table: Elemento BeautifulSoup de la tabla de jugadores.

    Returns:
        Lista de dicts con las stats de cada jugador (claves normalizadas).
    """
    headers = _table_headers(table)
    rows = []
    body = table.find("tbody")
    if body is None:
        body = table
    for tr in body.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row: Dict[str, object] = {}
        for idx, cell in enumerate(cells):
            if idx >= len(headers):
                break
            header = headers[idx]
            text = cell.get_text(strip=True)
            if header == "Player":
                # El nombre del jugador puede estar en un enlace.
                link = cell.find("a")
                row["player_name"] = link.get_text(strip=True) if link else text
            else:
                # Normalizar la cabecera a la clave canónica del contrato.
                key = _HEADER_ALIASES.get(header, header)
                row[key] = text
        if row.get("player_name"):
            rows.append(row)
    return rows


def fetch_player_game_logs(
    player_name: str,
    team_name: str,
    season: int,
    session: Optional[object] = None,
) -> List[Dict[str, object]]:
    """Descarga los game logs de un jugador en una temporada desde RealGM.

    RealGM no expone una URL directa de game logs por jugador y temporada sin
    conocer la página del jugador. Este método resuelve la página del jugador
    desde la plantilla del equipo (página de "Rosters") y sustituye "Summary"
    por "GameLogs" en su URL, tal como documenta el diseño.

    Args:
        player_name: Nombre del jugador.
        team_name: Nombre del equipo en RealGM.
        season: Año de inicio de la temporada.
        session: Sesión HTTP opcional.

    Returns:
        Lista de dicts con stats por partido del jugador (`date`, `opponent`,
        `minutes`, `points`, `rebounds`, `assists`, ...), listos para
        `upsert_player_game_log`. Vacía si no se puede resolver la página.
    """
    own_session = session is None
    session = session or _new_session()

    try:
        player_url = _resolve_player_url(session, player_name, team_name, season)
        if not player_url:
            logger.warning("RealGM: no se pudo resolver la página de %s en %s", player_name, team_name)
            return []

        # La página de game logs es la del jugador con "Summary" -> "GameLogs".
        game_logs_url = player_url.replace("/Summary", "/GameLogs")
        logger.info("RealGM: game logs de %s desde %s", player_name, game_logs_url)
        throttle(game_logs_url)
        response = session.get(game_logs_url, timeout=config.TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = _find_game_log_table(soup)
        if table is None:
            logger.warning("RealGM: no se encontró la tabla de game logs de %s", player_name)
            return []
        return _parse_game_log_table(table, season)
    except requests.RequestException as exc:
        logger.warning("RealGM: error obteniendo game logs de %s: %s", player_name, exc)
        return []
    finally:
        if own_session:
            session.close()


def _resolve_player_url(
    session: object,
    player_name: str,
    team_name: str,
    season: int,
) -> Optional[str]:
    """Localiza la URL de la página de un jugador desde la plantilla del equipo.

    Busca el enlace del jugador en la página de "Rosters" del equipo en la
    temporada indicada. RealGM organiza la plantilla por temporadas, así que
    se intenta con la temporada dada y, si no, con la página general.

    Args:
        session: Sesión HTTP reutilizada.
        player_name: Nombre del jugador a buscar.
        team_name: Nombre del equipo en RealGM.
        season: Año de inicio de la temporada.

    Returns:
        URL absoluta de la página del jugador, o None si no se encuentra.
    """
    # La URL de la plantilla del equipo requiere el id del equipo, que se
    # obtiene de la página de equipos de la liga. Se intenta con Euroliga y
    # ACB (el jugador estará en una de las dos).
    for league in ("euroleague", "acb"):
        try:
            teams_url = _teams_url(league)
            throttle(teams_url)
            response = session.get(teams_url, timeout=config.TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            team_link = _find_team_link(soup, team_name)
            if not team_link:
                continue
            roster_url = _roster_url_from_team_link(team_link)
            if not roster_url:
                continue
            throttle(roster_url)
            roster_response = session.get(roster_url, timeout=config.TIMEOUT)
            roster_response.raise_for_status()
            roster_soup = BeautifulSoup(roster_response.text, "html.parser")
            player_link = _find_player_link(roster_soup, player_name)
            if player_link:
                href = player_link.get("href")
                if href:
                    return href if href.startswith("http") else f"{REALGM_BASE}{href}"
        except requests.RequestException as exc:
            logger.warning("RealGM: error resolviendo plantilla de %s: %s", team_name, exc)
    return None


def _find_team_link(soup: BeautifulSoup, team_name: str):
    """Busca el enlace a la página de un equipo en la tabla de equipos.

    Args:
        soup: Documento de la página de equipos de una liga.
        team_name: Nombre del equipo en RealGM.

    Returns:
        Elemento de enlace del equipo, o None.
    """
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True)
        if text and text.lower() == team_name.lower():
            return link
    return None


def _roster_url_from_team_link(team_link) -> Optional[str]:
    """Deriva la URL de la plantilla ("Rosters") desde el enlace del equipo.

    Args:
        team_link: Elemento de enlace a la página del equipo.

    Returns:
        URL absoluta de la página de "Rosters", o None.
    """
    href = team_link.get("href")
    if not href:
        return None
    # La página del equipo suele ser .../international/team/<id>/<slug>.
    # La plantilla es .../international/team/<id>/<slug>/Rosters.
    base = href.rstrip("/")
    return f"{base}/Rosters"


def _find_player_link(soup: BeautifulSoup, player_name: str):
    """Busca el enlace a la página de un jugador en la plantilla del equipo.

    Args:
        soup: Documento de la página de "Rosters".
        player_name: Nombre del jugador a buscar.

    Returns:
        Elemento de enlace del jugador, o None.
    """
    target = player_name.lower()
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True)
        if text and text.lower() == target:
            return link
    return None


def _find_game_log_table(soup: BeautifulSoup):
    """Localiza la tabla de game logs de un jugador.

    La tabla de game logs tiene cabeceras "Date" y "Opponent".

    Args:
        soup: Documento de la página de game logs.

    Returns:
        Elemento de tabla o None.
    """
    for table in _find_all_tables(soup):
        headers = _table_headers(table)
        if "Date" in headers and "Opponent" in headers:
            return table
    return None


def _parse_game_log_table(table, season: int) -> List[Dict[str, object]]:
    """Parsea la tabla de game logs de un jugador.

    Args:
        table: Elemento BeautifulSoup de la tabla de game logs.
        season: Año de inicio de la temporada.

    Returns:
        Lista de dicts con stats por partido, listos para
        `upsert_player_game_log`.
    """
    headers = _table_headers(table)
    rows = []
    body = table.find("tbody")
    if body is None:
        body = table
    for tr in body.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row: Dict[str, object] = {}
        for idx, cell in enumerate(cells):
            if idx >= len(headers):
                break
            header = headers[idx]
            text = cell.get_text(strip=True)
            if header == "Opponent":
                link = cell.find("a")
                row["opponent"] = link.get_text(strip=True) if link else text
            elif header == "Date":
                row["date"] = text
            else:
                key = _HEADER_ALIASES.get(header, header)
                row[key] = text
        if row.get("date"):
            row["season"] = season
            rows.append(row)
    return rows
