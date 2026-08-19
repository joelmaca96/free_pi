"""Tests para `scraper/parser.py`.

Cubre el parsing de las páginas HTML de Basketball-Reference:
clasificación, página de equipo, calendario (con extracción de slug real del
rival y competición real por tabla) y box scores (incluidas las tablas
ocultas en comentarios HTML).
"""
import pytest

from scraper.parser import (
    _table_competition,
    parse_boxscore,
    parse_schedule_games,
    parse_standings,
    parse_team_page,
)


# --- HTML de ejemplo -------------------------------------------------------

STANDINGS_HTML = """
<html><body>
<table id="spa_standings">
  <thead><tr><th data-stat="team">Team</th><th data-stat="wins">W</th><th data-stat="losses">L</th></tr></thead>
  <tbody>
    <tr><td data-stat="team">Baskonia</td><td data-stat="wins">15</td><td data-stat="losses">5</td></tr>
    <tr><td data-stat="team">Real Madrid</td><td data-stat="wins">14</td><td data-stat="losses">6</td></tr>
  </tbody>
</table>
</body></html>
"""

TEAM_PAGE_HTML = """
<html><body>
<table id="per_game">
  <thead><tr><th>Player</th><th>Pos</th><th>PTS</th></tr></thead>
  <tbody>
    <tr><td>Markus Howard</td><td>G</td><td>18.5</td></tr>
    <tr><td>Chima Moneke</td><td>F</td><td>14.2</td></tr>
  </tbody>
</table>
</body></html>
"""

# Calendario con dos tablas: una de EuroLeague (ELG) y otra de ACB (SPA).
# La celda del rival incluye el enlace al slug real de BBR.
SCHEDULE_HTML = """
<html><body>
<table id="vitoria-ELG-regular-season">
  <thead><tr><th data-stat="date_game_full">Date</th><th data-stat="opp_name_link">Opp</th><th data-stat="game_location">Loc</th><th data-stat="pts">PTS</th><th data-stat="opp_pts">OPP</th><th data-stat="notes">Notes</th></tr></thead>
  <tbody>
    <tr>
      <td data-stat="date_game_full"><a href="/international/boxscores/202510020-vitoria.html">Fri, Oct 2, 2026</a></td>
      <td data-stat="opp_name_link"><a href="/international/teams/villeurbanne/2026.html">LDLC ASVEL</a></td>
      <td data-stat="game_location">@</td>
      <td data-stat="pts">85</td>
      <td data-stat="opp_pts">90</td>
      <td data-stat="notes"></td>
    </tr>
  </tbody>
</table>
<table id="vitoria-SPA-regular-season">
  <thead><tr><th data-stat="date_game_full">Date</th><th data-stat="opp_name_link">Opp</th><th data-stat="game_location">Loc</th><th data-stat="pts">PTS</th><th data-stat="opp_pts">OPP</th><th data-stat="notes">Notes</th></tr></thead>
  <tbody>
    <tr>
      <td data-stat="date_game_full"><a href="/international/boxscores/202511230-vitoria.html">Sun, Nov 23, 2025</a></td>
      <td data-stat="opp_name_link"><a href="/international/teams/bilbao/2026.html">Surne Bilbao Basket</a></td>
      <td data-stat="game_location"></td>
      <td data-stat="pts">88</td>
      <td data-stat="opp_pts">80</td>
      <td data-stat="notes"></td>
    </tr>
    <tr>
      <td data-stat="date_game_full">Sun, Dec 13, 2025</td>
      <td data-stat="opp_name_link"><a href="/international/teams/gran-canaria/2026.html">Gran Canaria</a></td>
      <td data-stat="game_location"></td>
      <td data-stat="pts"></td>
      <td data-stat="opp_pts"></td>
      <td data-stat="notes">Postponed</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

# Box score con las tablas reales ocultas en comentarios HTML.
BOXSCORE_HTML = """
<html><body>
<!--
<table id="box-score-home">
  <thead><tr><th>Player</th><th>MP</th><th>PTS</th></tr></thead>
  <tbody>
    <tr><td>Markus Howard</td><td>30:00</td><td>22</td></tr>
  </tbody>
</table>
<table id="box-score-visitor">
  <thead><tr><th>Player</th><th>MP</th><th>PTS</th></tr></thead>
  <tbody>
    <tr><td>Melwin Pantzar</td><td>28:00</td><td>12</td></tr>
  </tbody>
</table>
-->
</body></html>
"""


# --- parse_standings -------------------------------------------------------

def test_parse_standings_extracts_teams():
    """La clasificación extrae el nombre de cada equipo."""
    rows = parse_standings(STANDINGS_HTML)
    assert len(rows) == 2
    assert rows[0]["team"] == "Baskonia"
    assert rows[1]["team"] == "Real Madrid"


def test_parse_standings_uses_data_stat_keys():
    """Las columnas se mapean por data-stat."""
    rows = parse_standings(STANDINGS_HTML)
    assert rows[0]["wins"] == "15"
    assert rows[0]["losses"] == "5"


# --- parse_team_page -------------------------------------------------------

def test_parse_team_page_extracts_roster():
    """La página de equipo extrae el roster de la tabla Per Game."""
    result = parse_team_page(TEAM_PAGE_HTML)
    assert len(result["roster"]) == 2
    assert result["roster"][0]["Player"] == "Markus Howard"
    assert result["roster"][1]["Player"] == "Chima Moneke"


# --- _table_competition ----------------------------------------------------

@pytest.mark.parametrize(
    "table_id,expected",
    [
        ("vitoria-ELG-regular-season", "euroleague"),
        ("vitoria-SPA-regular-season", "acb"),
        ("vitoria-SPA-playoffs", "acb"),
        ("gran-canaria-SPA-regular-season", "acb"),
        # Id sin el patrón conocido -> None (el llamador usa la liga de reserva)
        ("games", None),
        ("schedule", None),
        # Código de competición no reconocido -> se guarda en minúsculas
        ("vitoria-XXX-regular-season", "xxx"),
    ],
)
def test_table_competition(table_id, expected):
    """La competición real se extrae del id de la tabla de calendario."""
    assert _table_competition(table_id) == expected


# --- parse_schedule_games --------------------------------------------------

def test_parse_schedule_games_extracts_games():
    """El calendario extrae todos los partidos de todas las tablas."""
    games = parse_schedule_games(SCHEDULE_HTML)
    assert len(games) == 3


def test_parse_schedule_games_league_per_table():
    """Cada partido lleva la competición real de su tabla de origen."""
    games = parse_schedule_games(SCHEDULE_HTML)
    by_opponent = {g["opponent"]: g for g in games}
    assert by_opponent["LDLC ASVEL"]["league"] == "euroleague"
    assert by_opponent["Surne Bilbao Basket"]["league"] == "acb"
    assert by_opponent["Gran Canaria"]["league"] == "acb"


def test_parse_schedule_games_opponent_slug():
    """El slug real del rival se extrae del enlace de la celda del oponente."""
    games = parse_schedule_games(SCHEDULE_HTML)
    by_opponent = {g["opponent"]: g for g in games}
    assert by_opponent["LDLC ASVEL"]["opponent_slug"] == "villeurbanne"
    assert by_opponent["Surne Bilbao Basket"]["opponent_slug"] == "bilbao"


def test_parse_schedule_games_boxscore_url():
    """La URL del box score se extrae del enlace de la fecha."""
    games = parse_schedule_games(SCHEDULE_HTML)
    by_opponent = {g["opponent"]: g for g in games}
    assert by_opponent["Surne Bilbao Basket"]["boxscore_url"] == "/international/boxscores/202511230-vitoria.html"
    # Partido sin jugar (aplazado) no tiene boxscore_url
    assert by_opponent["Gran Canaria"]["boxscore_url"] == ""


def test_parse_schedule_games_home_away():
    """La columna game_location distingue local de visitante."""
    games = parse_schedule_games(SCHEDULE_HTML)
    by_opponent = {g["opponent"]: g for g in games}
    # "@" -> visitante
    assert by_opponent["LDLC ASVEL"]["is_home"] is False
    # vacío -> local
    assert by_opponent["Surne Bilbao Basket"]["is_home"] is True


def test_parse_schedule_games_notes():
    """La nota 'Postponed' se captura para partidos aplazados."""
    games = parse_schedule_games(SCHEDULE_HTML)
    by_opponent = {g["opponent"]: g for g in games}
    assert by_opponent["Gran Canaria"]["notes"] == "Postponed"
    assert by_opponent["Surne Bilbao Basket"]["notes"] == ""


# --- parse_boxscore --------------------------------------------------------

def test_parse_boxscore_extracts_home_and_away():
    """El box score extrae las tablas ocultas en comentarios HTML."""
    result = parse_boxscore(BOXSCORE_HTML)
    assert "home" in result
    assert "away" in result
    assert result["home"][0]["Player"] == "Markus Howard"
    assert result["away"][0]["Player"] == "Melwin Pantzar"


def test_parse_boxscore_empty_html():
    """Un HTML sin tablas de box score devuelve un dict vacío."""
    result = parse_boxscore("<html><body><p>no data</p></body></html>")
    assert result == {}
