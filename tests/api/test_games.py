"""Tests de los endpoints de partidos: /games/{game_id} y /games/{game_id}/boxscore."""


def test_get_game_detail(client):
    """Detalle completo de un partido (resultado, advanced, lineups, zonas, eventos)."""
    r = client.get("/api/v1/games/g1")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "g1"
    assert body["season_label"] == "2025-2026"
    assert body["competition_name"] == "Euroliga"
    assert body["home_team"]["id"] == "bas"
    assert body["away_team"]["id"] == "rm"
    assert body["home_score"] == 88
    assert body["away_score"] == 82
    assert body["pace"] == 71.2

    # Bloque de conveniencia del Baskonia.
    assert body["baskonia"]["is_home"] is True
    assert body["baskonia"]["opponent_id"] == "rm"
    assert body["baskonia"]["score_for"] == 88
    assert body["baskonia"]["score_against"] == 82

    # Advanced: una fila por equipo (seed solo tiene 'bas').
    assert len(body["advanced"]) == 1
    assert body["advanced"][0]["net_rating"] == 7.8

    # Lineups, zonas y eventos del seed.
    assert len(body["lineups"]) == 3
    assert len(body["zone_stats"]) == 6
    assert len(body["key_events"]) == 4


def test_get_game_detail_not_found(client):
    """Partido inexistente → 404 problem+json."""
    r = client.get("/api/v1/games/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["type"].endswith("game-not-found")
    assert body["status"] == 404


def test_get_boxscore(client):
    """Box score de un partido (filas de ambos equipos, sin filtrar por equipo)."""
    r = client.get("/api/v1/games/g1/boxscore")
    assert r.status_code == 200
    body = r.json()
    assert body["game_id"] == "g1"
    assert len(body["rows"]) == 8
    # Orden por puntos desc: howard (20) primero.
    assert body["rows"][0]["player_id"] == "howard"
    assert body["rows"][0]["name"] == "Marcus Howard"
    assert body["rows"][0]["pts"] == 20
    # Cada fila expone el team_id del jugador (para agrupar por equipo).
    assert all("team_id" in row for row in body["rows"])
    assert body["rows"][0]["team_id"] == "bas"


def test_get_boxscore_not_found(client):
    """Box score de un partido inexistente → 404."""
    r = client.get("/api/v1/games/does-not-exist/boxscore")
    assert r.status_code == 404
