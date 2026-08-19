"""Parser de las páginas HTML de Basketball-Reference.

Convierte las tablas HTML de BBR en estructuras de datos limpias
(listas de diccionarios) listas para insertar en la base de datos.
"""
import logging
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, Comment

logger = logging.getLogger(__name__)


def _find_all_tables(soup: BeautifulSoup) -> List[object]:
    """Devuelve todas las tablas de la página, incluidas las que BBR oculta
    dentro de comentarios HTML (truco habitual anti-scraping en box scores).

    Args:
        soup: Documento parseado.

    Returns:
        Lista de elementos de tabla (visibles + los ocultos en comentarios).
    """
    tables = list(soup.find_all("table"))
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if "<table" in comment:
            tables.extend(BeautifulSoup(comment, "html.parser").find_all("table"))
    return tables


def _parse_table_rows(table) -> List[Dict[str, str]]:
    """Extrae las filas de una tabla HTML como lista de diccionarios.

    Args:
        table: Elemento BeautifulSoup de la tabla.

    Returns:
        Lista de diccionarios con las columnas como claves.
    """
    if table is None:
        return []

    # Cabeceras: primera fila de <th>
    headers = []
    header_row = table.find("thead")
    if header_row:
        headers = [th.get_text(strip=True) for th in header_row.find_all("th")]

    rows = []
    body = table.find("tbody")
    if body is None:
        body = table
    for tr in body.find_all("tr"):
        # Saltar filas de separación o de totales
        if tr.get("class") and any("thead" in c for c in tr.get("class", [])):
            continue
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        # Si no hay cabeceras, usar índices numéricos
        if not headers:
            row = {str(i): cell.get_text(strip=True) for i, cell in enumerate(cells)}
        else:
            row = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                row[key] = cell.get_text(strip=True)
        rows.append(row)
    return rows


def _clean_team_name(name: str) -> str:
    """Limpia el nombre de un equipo eliminando sufijos de BBR."""
    # BBR añade sufijos como "Basket Club", "B.C.", etc.
    return name.strip()


def parse_standings(html: str) -> List[Dict[str, str]]:
    """Parsea la tabla de clasificación de una liga.

    Args:
        html: Contenido HTML de la página de la liga.

    Returns:
        Lista de filas de clasificación. Cada fila tiene al menos la
        clave 'team' con el nombre del equipo.
    """
    soup = BeautifulSoup(html, "html.parser")
    # La tabla de standings de BBR tiene un id que termina en 'standings'
    # (p.ej. 'spa_standings' para la Liga ACB, 'euro_standings' para EuroLeague).
    table = None
    for candidate in soup.find_all("table"):
        table_id = candidate.get("id", "")
        if table_id.endswith("standings"):
            table = candidate
            break
    if table is None:
        # Fallback: primera tabla con datos
        table = soup.find("table")

    rows = []
    body = table.find("tbody") if table is not None else None
    if body is None:
        body = table
    if body is None:
        return rows

    for tr in body.find_all("tr"):
        # Saltar filas de separación o de totales
        if tr.get("class") and any("thead" in c for c in tr.get("class", [])):
            continue
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue

        row: Dict[str, str] = {}
        for cell in cells:
            # Usar data-stat como clave si está disponible
            stat = cell.get("data-stat")
            text = cell.get_text(strip=True)
            if stat:
                # Quitar el sufijo de fase (|regular-season, |playoffs)
                base_stat = stat.split("|")[0]
                if base_stat == "team":
                    row["team"] = text
                elif base_stat not in row:
                    row[base_stat] = text
            else:
                # Sin data-stat: usar la primera celda como nombre de equipo
                if "team" not in row:
                    row["team"] = text
        if row:
            rows.append(row)
    return rows


def parse_team_page(html: str) -> Dict[str, object]:
    """Parsea la página de un equipo.

    En BBR internacional, la página del equipo (`/international/teams/<slug>/<season>.html`)
    contiene las estadísticas de los jugadores en tablas "Per Game", "Totals" y
    "Per 36 Minutes". El roster se extrae de la tabla "Per Game" (una fila por
    jugador). El calendario NO está en esta página: está en una página separada
    (`/international/schedules/<slug>/<season>.html`), que se parsea con
    `parse_schedule_games`.

    Args:
        html: Contenido HTML de la página del equipo.

    Returns:
        Diccionario con 'roster' (jugadores de la tabla Per Game).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Roster: en BBR internacional la tabla de jugadores es la "Per Game".
    # Buscamos por id (p.ej. 'per_game') o por el encabezado de sección.
    roster_table = soup.find("table", id="per_game")
    if roster_table is None:
        # Fallback: buscar la tabla cuya sección <caption> o <h2> previo sea "Per Game"
        for table in soup.find_all("table"):
            caption = table.find("caption")
            if caption and "per game" in caption.get_text(strip=True).lower():
                roster_table = table
                break
    roster = _parse_table_rows(roster_table)

    return {"roster": roster}


# Código de competición embebido en el id de la tabla de calendario de BBR
# internacional (p.ej. "vitoria-ELG-regular-season", "vitoria-SPA-playoffs") ->
# valor de `games.league` a guardar. Verificado contra HTML real de BBR (tablas
# de vitoria/bilbao/gran-canaria, temporada 2026): "SPA" (Liga ACB) y "ELG"
# (EuroLeague) son los únicos códigos vistos. Un código no reconocido se guarda
# tal cual en minúsculas (mismo patrón que `_to_league` en
# scraper/baskonia_official.py) en vez de fallar.
_BBR_LEAGUE_MAP: Dict[str, str] = {
    "SPA": "acb",
    "ELG": "euroleague",
}


def _table_competition(table_id: str) -> Optional[str]:
    """Extrae la competición real codificada en el id de una tabla de calendario BBR.

    El código va siempre justo antes del sufijo de fase (`-regular-season` o
    `-playoffs`), independientemente de cuántos guiones tenga el slug del
    equipo (p.ej. "gran-canaria-SPA-regular-season"), así que no hace falta
    recibir el slug como parámetro.

    Args:
        table_id: Atributo `id` de la tabla (`<table id="...">`).

    Returns:
        Valor de liga a guardar en `games.league`, o `None` si el id no sigue
        el patrón conocido (tablas genéricas `games`/`schedule`, o el
        fallback por cabecera Date/Result de `parse_schedule_games`): en ese
        caso el llamador debe seguir usando su liga de reserva actual
        (`team.league`), igual que hoy.
    """
    match = re.search(r"-([A-Z]+)-(?:regular-season|playoffs)$", table_id)
    if not match:
        return None
    comp_code = match.group(1)
    return _BBR_LEAGUE_MAP.get(comp_code, comp_code.lower())


def parse_schedule_games(html: str) -> List[Dict[str, object]]:
    """Parsea el calendario de un equipo extrayendo partidos estructurados.

    En BBR internacional, el calendario está en una página separada
    (`/international/schedules/<slug>/<season>.html`) con una tabla por
    competición/fase (p.ej. "EuroLeague - Regular Season", "Liga ACB - Regular
    Season", "Liga ACB - Playoffs"). Cada fila contiene fecha, oponente,
    local/visitante, resultado y la URL del box score.

    Args:
        html: Contenido HTML de la página de schedules del equipo.

    Returns:
        Lista de diccionarios con los partidos del calendario. Cada partido
        incluye `"league"` con la competición real de su tabla de origen
        (`None` si el id de la tabla no permite deducirla, ver
        `_table_competition`).
    """
    soup = BeautifulSoup(html, "html.parser")

    # La página de schedules tiene varias tablas (una por competición/fase).
    # Las identificamos por su id (terminan en '-regular-season' o '-playoffs')
    # o por tener una columna de fecha y resultado.
    schedule_tables = []
    for table in soup.find_all("table"):
        table_id = table.get("id", "")
        if table_id in ("games", "schedule"):
            schedule_tables.append(table)
            continue
        if table_id.endswith("-regular-season") or table_id.endswith("-playoffs"):
            schedule_tables.append(table)
            continue
        # Fallback: tabla con cabecera que incluya "Date" y "Result"
        header_row = table.find("thead")
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
            if "Date" in headers and "Result" in headers:
                schedule_tables.append(table)

    games: List[Dict[str, object]] = []
    for schedule_table in schedule_tables:
        # La competición se resuelve una vez por tabla: todas las filas de una
        # misma tabla de BBR pertenecen a la misma competición.
        league = _table_competition(schedule_table.get("id", ""))
        games.extend(_parse_schedule_table(schedule_table, league))

    return games


def _parse_schedule_table(schedule_table, league: Optional[str] = None) -> List[Dict[str, object]]:
    """Parsea una tabla de calendario individual.

    Usa el atributo `data-stat` de cada celda para identificar las columnas,
    ya que en BBR internacional las cabeceras pueden estar vacías o tener
    nombres distintos según la competición.

    Args:
        schedule_table: Elemento BeautifulSoup de la tabla de calendario.
        league: Competición real de todas las filas de esta tabla (ver
            `_table_competition`), o `None` si no se pudo determinar.

    Returns:
        Lista de partidos de esa tabla.
    """
    games = []
    body = schedule_table.find("tbody")
    if body is None:
        body = schedule_table

    for tr in body.find_all("tr"):
        # Saltar filas de cabecera repetidas o separadores
        if tr.get("class") and any("thead" in c for c in tr.get("class", [])):
            continue
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue

        # Mapear data-stat -> celda
        by_stat: Dict[str, object] = {}
        for cell in cells:
            stat = cell.get("data-stat")
            if stat:
                by_stat[stat] = cell

        def _text(stat: str) -> str:
            """Texto de una celda por data-stat."""
            cell = by_stat.get(stat)
            if cell is None:
                return ""
            return cell.get_text(strip=True)

        # Fecha
        date = _text("date_game_full")
        if not date:
            continue

        # Oponente
        opp = _text("opp_name_link")

        # Slug real de BBR del rival: el enlace de la celda del oponente
        # apunta a /international/teams/<slug>/<año>.html. Necesario para
        # poder scrapear luego al rival por su cuenta (roster/calendario),
        # ya que el nombre de display normalizado no coincide con el slug
        # real (p.ej. "LDLC ASVEL" -> slug "villeurbanne").
        opponent_slug = None
        opp_cell = by_stat.get("opp_name_link")
        if opp_cell is not None:
            opp_link = opp_cell.find("a", href=True)
            if opp_link:
                match = re.search(r"/teams/([^/]+)/", opp_link["href"])
                if match:
                    opponent_slug = match.group(1)

        # URL del box score: en BBR internacional el enlace está en la celda
        # de la fecha (date_game_full).
        boxscore_url = ""
        date_cell = by_stat.get("date_game_full")
        if date_cell is not None:
            link = date_cell.find("a", href=True)
            if link and "boxscores" in link["href"]:
                boxscore_url = link["href"]

        # Local/visitante: la columna game_location usa "@" para visitante
        # y vacío para local.
        location = _text("game_location")
        is_home = location.strip() != "@"

        # Puntos
        pts = _text("pts")
        opp_pts = _text("opp_pts")

        # Notas de BBR sobre el partido (p.ej. "Postponed"): un partido
        # aplazado se queda para siempre sin resultado en su fila original,
        # aunque se haya jugado más adelante en otra fecha como fila aparte.
        # Sin esto, un aplazamiento antiguo parece indistinguible de un
        # partido genuinamente pendiente de jugar.
        notes = _text("notes")

        games.append(
            {
                "date": date,
                "opponent": opp,
                "opponent_slug": opponent_slug,
                "boxscore_url": boxscore_url,
                "is_home": is_home,
                "points": pts,
                "opp_points": opp_pts,
                "notes": notes,
                "league": league,
            }
        )

    return games


def parse_boxscore(html: str) -> Dict[str, object]:
    """Parsea la página de un box score de partido.

    Extrae las estadísticas de ambos equipos.

    Args:
        html: Contenido HTML de la página del box score.

    Returns:
        Diccionario con 'home' y 'away' (stats por jugador).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Los box scores de BBR tienen dos tablas de stats básicas ocultas en
    # comentarios HTML: 'box-score-home' (equipo local) y 'box-score-visitor'
    # (equipo visitante).
    id_to_key = {"box-score-home": "home", "box-score-visitor": "away"}
    result: Dict[str, object] = {}
    for table in _find_all_tables(soup):
        key = id_to_key.get(table.get("id", ""))
        if key:
            result[key] = _parse_table_rows(table)
    return result


def parse_player_page(html: str) -> Dict[str, object]:
    """Parsea la página de un jugador.

    Extrae los datos básicos y las estadísticas de temporada.

    Args:
        html: Contenido HTML de la página del jugador.

    Returns:
        Diccionario con 'info' y 'per_game'.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Datos básicos del jugador (nombre, posición, etc.)
    info = {}
    info_p = soup.find("p", id="meta")
    if info_p:
        info["meta"] = info_p.get_text(" ", strip=True)

    # Estadísticas por partido: tabla con id 'per_game'
    per_game_table = soup.find("table", id="per_game")
    per_game = _parse_table_rows(per_game_table)

    return {"info": info, "per_game": per_game}
