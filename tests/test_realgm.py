"""Tests para `scraper/realgm.py`.

Cubre la construcción de URLs y el parsing de las tablas de calendario de
RealGM (sin red). El scraping real (fetch_team_schedule, fetch_game_boxscore)
requiere red y no se testea aquí.
"""
from datetime import date

from bs4 import BeautifulSoup

from apps.ingest.scraper.realgm import (
    _find_schedule_table,
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
      <thead><tr><th>Away Team</th><th>Score</th><th>Home Team</th><th>Venue</th></tr></thead>
      <tbody>
        <tr><td>Baskonia</td><td>85-90</td><td>Real Madrid</td><td>WiZink</td></tr>
        <tr><td>Barcelona</td><td>70-75</td><td>Valencia</td><td>Fonteta</td></tr>
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


def test_parse_schedule_table_home():
    """Un partido en casa se marca como is_home=True y el rival es el visitante."""
    html = """
    <html><body>
    <table id="schedule">
      <thead><tr><th>Away Team</th><th>Score</th><th>Home Team</th><th>Venue</th></tr></thead>
      <tbody>
        <tr><td>Real Madrid</td><td>90-85</td><td>Baskonia</td><td>Buesa</td></tr>
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
