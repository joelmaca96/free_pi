"""Tests para `scraper/acb_api.py`.

La API de la ACB requiere red y su esquema JSON está pendiente de verificar
en desarrollo, por lo que aquí solo se cubren las constantes y helpers puros
del módulo (sin red).
"""
from apps.ingest.scraper.acb_api import ACB_API_BASE, _headers, _map_game


def test_api_base():
    """La base de la API de la ACB es la esperada."""
    assert ACB_API_BASE == "https://www.acb.com/api"


def test_headers_include_user_agent():
    """Las cabeceras incluyen un User-Agent y Accept JSON."""
    headers = _headers()
    assert "User-Agent" in headers
    assert headers["Accept"] == "application/json"


# --- _map_game ---------------------------------------------------------------

def test_map_game_home_variant():
    """Mapea un partido en casa con los nombres de campo en inglés."""
    item = {
        "date": "2025-10-05",
        "home": "Baskonia",
        "away": "Real Madrid",
        "homeScore": 90,
        "awayScore": 85,
        "competition": "Liga Endesa",
    }
    game = _map_game(item, "Baskonia")

    assert game["date"] == "2025-10-05"
    assert game["opponent"] == "Real Madrid"
    assert game["is_home"] is True
    assert game["points"] == 90
    assert game["opp_points"] == 85
    assert game["opponent_slug"] is None
    assert game["boxscore_url"] is None
    assert game["season"] is None


def test_map_game_away_swaps_scores():
    """Fuera de casa, `points` es el marcador del visitante."""
    item = {
        "fecha": "2025-10-12",
        "local": "Real Madrid",
        "visitante": "Baskonia",
        "localScore": 70,
        "visitanteScore": 88,
    }
    game = _map_game(item, "Baskonia")

    assert game["is_home"] is False
    assert game["opponent"] == "Real Madrid"
    assert game["points"] == 88
    assert game["opp_points"] == 70


def test_map_game_accepts_nested_team_objects():
    """Los equipos pueden venir como objeto con `name`, no solo como texto."""
    item = {
        "gameDate": "2025-11-01",
        "homeTeam": {"name": "Baskonia"},
        "awayTeam": {"name": "Joventut"},
    }
    game = _map_game(item, "Baskonia")

    assert game["opponent"] == "Joventut"
    assert game["is_home"] is True


def test_map_game_maps_competition_to_league():
    """La competición se traduce al nombre canónico de liga del proyecto."""
    item = {
        "date": "2026-02-14",
        "home": "Baskonia",
        "away": "Unicaja",
        "competition": "Copa del Rey",
    }
    assert _map_game(item, "Baskonia")["league"] == "copa-del-rey"


def test_map_game_defaults_to_acb_for_unknown_competition():
    """Una competición desconocida cae en 'acb' (la liga por defecto)."""
    item = {"date": "2026-02-14", "home": "Baskonia", "away": "Unicaja", "competition": "???"}
    assert _map_game(item, "Baskonia")["league"] == "acb"


def test_map_game_without_scores_leaves_points_none():
    """Un partido aún no jugado no inventa marcador."""
    item = {"date": "2026-05-01", "home": "Baskonia", "away": "Unicaja"}
    game = _map_game(item, "Baskonia")

    assert game["points"] is None
    assert game["opp_points"] is None


def test_map_game_returns_none_without_date():
    """Sin fecha no hay clave natural: el item se descarta."""
    assert _map_game({"home": "Baskonia", "away": "Unicaja"}, "Baskonia") is None


def test_map_game_returns_none_without_teams():
    """Sin los dos equipos el partido no es utilizable."""
    assert _map_game({"date": "2025-10-05", "home": "Baskonia"}, "Baskonia") is None
