"""Tests para `scraper/realgm.py`.

Cubre la construcción de URLs y el parsing de las tablas de calendario de
RealGM (sin red). El scraping real (fetch_team_schedule, fetch_game_boxscore)
requiere red y no se testea aquí.
"""
from datetime import date

from bs4 import BeautifulSoup

from apps.ingest.scraper.realgm import (
    _find_schedule_table,
    _parse_player_table,
    _parse_schedule_table,
    _schedule_url,
    _teams_url,
)


def test_schedule_url_euroleague():
    """La URL de calendario de Euroliga usa el league id 1 y la fecha."""
    url = _schedule_url("euroleague", date(2025, 10, 2))
    assert url == (
        "https://basketball.realgm.com/international/league/1/Euroleague/schedules/2025-10-02"
    )


def test_schedule_url_acb():
    """La URL de calendario de ACB usa el league id 2."""
    url = _schedule_url("acb", date(2025, 10, 2))
    assert url == (
        "https://basketball.realgm.com/international/league/2/Liga-ACB/schedules/2025-10-02"
    )


def test_teams_url():
    """La URL de equipos de Euroliga apunta a la página de teams."""
    url = _teams_url("euroleague")
    assert url == "https://basketball.realgm.com/international/league/1/Euroleague/teams"


def test_find_schedule_table():
    """Localiza la tabla con cabeceras Away Team / Home Team."""
    html = """
    <html><body>
    <table id="other"><thead><tr><th>Foo</th></tr></thead></table>
    <table id="schedule">
      <thead><tr><th>Away Team</th><th>Score</th><th>Home Team</th><th>Venue</th></tr></thead>
      <tbody><tr><td>Baskonia</td><td>85-90</td><td>Real Madrid</td><td>WiZink</td></tr></tbody>
    </table>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _find_schedule_table(soup)
    assert table is not None
    assert table.get("id") == "schedule"


def test_parse_schedule_table_filters_team():
    """Solo se devuelven los partidos del equipo objetivo."""
    html = """
    <html><body>
    <table id="schedule">
      <thead><tr><th>Date</th><th>Away Team</th><th>Home Team</th><th>Result</th><th>Venue</th></tr></thead>
      <tbody>
        <tr><td>Oct 9, 2025</td><td>Baskonia</td><td>Real Madrid</td><td><a href="/international/boxscore/2025-10-09/Baskonia-at-Real-Madrid/1">85-90</a></td><td>WiZink</td></tr>
        <tr><td>Oct 9, 2025</td><td>Barcelona</td><td>Valencia</td><td>70-75</td><td>Fonteta</td></tr>
      </tbody>
    </table>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _find_schedule_table(soup)
    games = _parse_schedule_table(table, "euroleague", "Baskonia")
    assert len(games) == 1
    game = games[0]
    assert game["opponent"] == "Real Madrid"
    assert game["is_home"] is False
    assert game["league"] == "euroleague"
    # El marcador de RealGM es [local]-[visitante]: Real Madrid (local) 85,
    # Baskonia (visitante) 90.
    assert game["points"] == "90"
    assert game["opp_points"] == "85"
    assert game["boxscore_url"] == "/international/boxscore/2025-10-09/Baskonia-at-Real-Madrid/1"


def test_parse_schedule_table_home():
    """Un partido en casa se marca como is_home=True y el rival es el visitante."""
    html = """
    <html><body>
    <table id="schedule">
      <thead><tr><th>Date</th><th>Away Team</th><th>Home Team</th><th>Result</th><th>Venue</th></tr></thead>
      <tbody>
        <tr><td>Oct 9, 2025</td><td>Real Madrid</td><td>Baskonia</td><td><a href="/international/boxscore/2025-10-09/Real-Madrid-at-Baskonia/2">90-85</a></td><td>Buesa</td></tr>
      </tbody>
    </table>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _find_schedule_table(soup)
    games = _parse_schedule_table(table, "euroleague", "Baskonia")
    assert len(games) == 1
    game = games[0]
    assert game["opponent"] == "Real Madrid"
    assert game["is_home"] is True
    # El marcador de RealGM es [local]-[visitante]: Baskonia (local) 90,
    # Real Madrid (visitante) 85.
    assert game["points"] == "90"
    assert game["opp_points"] == "85"
    assert game["boxscore_url"] == "/international/boxscore/2025-10-09/Real-Madrid-at-Baskonia/2"


def test_parse_player_table_normalizes_headers():
    """Normaliza las cabeceras de RealGM y descompone los campos combinados."""
    html = """
    <html><body>
    <table id="box">
      <thead><tr><th>#</th><th>Player</th><th>Min</th><th>FGM-A</th><th>3PM-A</th>
        <th>FTM-A</th><th>Off</th><th>Def</th><th>Reb</th><th>Ast</th>
        <th>PF</th><th>STL</th><th>TOV</th><th>BLK</th><th>PTS</th></tr></thead>
      <tbody>
        <tr><td>25</td><td><a href="/player/1">Kendrick Nunn</a></td><td>30:48</td>
          <td>12-20</td><td>3-7</td><td>3-3</td><td>2</td><td>5</td><td>7</td>
          <td>2</td><td>4</td><td>0</td><td>4</td><td>0</td><td>30</td></tr>
      </tbody>
    </table>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = _parse_player_table(soup.find("table"))
    assert len(rows) == 1
    row = rows[0]
    assert row["player_name"] == "Kendrick Nunn"
    assert row["MP"] == "30:48"
    assert row["FG"] == "12"
    assert row["FGA"] == "20"
    assert row["3P"] == "3"
    assert row["3PA"] == "7"
    assert row["FT"] == "3"
    assert row["FTA"] == "3"
    assert row["ORB"] == "2"
    assert row["DRB"] == "5"
    assert row["TRB"] == "7"
    assert row["AST"] == "2"
    assert row["PTS"] == "30"
