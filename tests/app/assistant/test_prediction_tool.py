"""Tests de la herramienta `game_prediction` (propuesta 16).

Sobre la liga sintética del `conftest` (temporada 2): el margen de cada
partido es exactamente `5 + 3·(índice_visitante − índice_local)`, así que el
`bas` (índice 0) es el mejor y `uni` (índice 5) el peor, y jugar en casa suma.
"""
import datetime as dt

from sqlalchemy import text

from app.assistant.tools import REGISTRY, ToolCatalog

from tests.app.assistant.conftest import LEAGUE_SEASON_ID, TODAY


def test_game_prediction_is_registered_in_the_team_family():
    assert REGISTRY["game_prediction"].family == "team"


def test_game_prediction_for_an_explicit_rival(league_ctx):
    catalog = ToolCatalog(league_ctx)
    result = catalog.execute(
        "1", "game_prediction", {"opponent_id": "uni", "is_home": True, "date": "2027-03-05"}
    ).result
    data = result["data"]
    assert data["expected_margin"] > 0
    assert data["win_probability"] > 0.5
    assert data["venue"] == "home"
    assert abs(sum(c["points"] for c in data["components"]) - data["expected_margin"]) < 0.2
    assert data["model"]["home_court_points"] > 0
    assert any("por jugar en casa" in line for line in data["explanation"])
    assert result["meta"]["gp"] == 30
    assert "no un pronóstico" in result["meta"]["warnings"][0]
    assert result["artifact"]["type"] == "table"


def test_game_prediction_home_vs_away_moves_the_margin(league_ctx):
    catalog = ToolCatalog(league_ctx)
    home = catalog.execute("1", "game_prediction", {"opponent_id": "rm", "is_home": True}).result["data"]
    away = catalog.execute("2", "game_prediction", {"opponent_id": "rm", "is_home": False}).result["data"]
    assert home["expected_margin"] > away["expected_margin"]
    assert home["win_probability"] > away["win_probability"]


def test_game_prediction_without_venue_or_calendar_goes_neutral(league_ctx):
    result = ToolCatalog(league_ctx).execute("1", "game_prediction", {"opponent_id": "val"}).result
    assert result["data"]["venue"] == "neutral"
    assert any("pista neutral" in w for w in result["meta"]["warnings"])


def test_game_prediction_uses_the_next_matchup_by_default(engine, league_ctx):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO upcoming_matchups (opponent_team_id, competition_id, match_date, is_home, season_id)"
            " VALUES ('gc', 1, :date, 0, :season)"
        ), {"date": (TODAY + dt.timedelta(days=3)).isoformat(), "season": LEAGUE_SEASON_ID})
    data = ToolCatalog(league_ctx).execute("1", "game_prediction", {}).result["data"]
    assert data["opponent_id"] == "gc"
    assert data["venue"] == "away"
    assert data["match_date"] == (TODAY + dt.timedelta(days=3)).isoformat()


def test_game_prediction_fails_usefully_without_calendar(league_ctx):
    # Los `upcoming_matchups` del seed son de 2026, anteriores al TODAY del conftest.
    result = ToolCatalog(league_ctx).execute("1", "game_prediction", {}).result
    assert result["error"] == "sin calendario"
    assert "suggestion" in result


def test_game_prediction_rejects_a_bad_date(league_ctx):
    result = ToolCatalog(league_ctx).execute("1", "game_prediction", {"opponent_id": "uni", "date": "5/3/2027"}).result
    assert result["error"] == "fecha inválida"


def test_game_prediction_fails_for_a_rival_without_games(league_ctx):
    """Regresión: un id que no existe (o un nombre en vez de id) salía como "equipo medio" con una
    predicción de aspecto normal."""
    result = ToolCatalog(league_ctx).execute("1", "game_prediction", {"opponent_id": "Real Madrid"}).result
    assert result["error"] == "sin datos"
    assert "resolve_entity" in result["suggestion"]


def test_game_prediction_ignores_the_calendar_venue_for_another_date(engine, league_ctx):
    """Regresión: con una fecha distinta de la del calendario, se usaba la pista (y la competición) del
    partido del calendario, que es OTRO partido."""
    match_day = TODAY + dt.timedelta(days=3)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO upcoming_matchups (opponent_team_id, competition_id, match_date, is_home, season_id)"
            " VALUES ('val', 1, :date, 1, :season)"
        ), {"date": match_day.isoformat(), "season": LEAGUE_SEASON_ID})
    catalog = ToolCatalog(league_ctx)
    same = catalog.execute("1", "game_prediction", {"opponent_id": "val", "date": match_day.isoformat()}).result
    assert same["data"]["venue"] == "home"
    other_day = (TODAY + dt.timedelta(days=20)).isoformat()
    other = catalog.execute("2", "game_prediction", {"opponent_id": "val", "date": other_day}).result
    assert other["data"]["venue"] == "neutral"
    assert other["data"]["match_date"] == other_day
