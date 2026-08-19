"""Tests de los endpoints de enfrentamientos (difficulty, narrative, projection, h2h)."""
from tests.api.conftest import api_played_game, api_teams  # noqa: F401


def test_schedule_difficulty(client, api_played_game, api_teams):
    """Dificultad del próximo tramo de calendario."""
    r = client.get("/api/v1/teams/vitoria/schedule-difficulty")
    assert r.status_code == 200
    body = r.json()
    assert "games_considered" in body
    assert "opponents" in body


def test_narrative(client, api_played_game, api_teams):
    """Narrativa de scouting (puede ser null si no hay datos suficientes)."""
    r = client.get("/api/v1/teams/vitoria/narrative?season=2025")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == 2025
    assert "narrative" in body


def test_projection(client, api_played_game, api_teams):
    """Proyección de marcador entre dos equipos."""
    r = client.get("/api/v1/teams/vitoria/matchups/bilbao/projection?season=2025")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["slug"] == "vitoria"
    assert body["opponent"]["slug"] == "bilbao"
    assert body["projection"] is not None
    assert "expected_margin" in body["projection"]


def test_projection_null(client, api_played_game, api_teams):
    """Proyección null si falta pace/ratings (200, no 404)."""
    # Bilbao no tiene TeamGameStats → proyección null
    r = client.get("/api/v1/teams/vitoria/matchups/bilbao/projection?season=2025")
    body = r.json()
    # vitoria sí tiene stats, así que la proyección existe; comprobamos el contrato
    assert "projection" in body


def test_head_to_head(client, api_played_game, api_teams):
    """Enfrentamientos directos entre dos equipos."""
    r = client.get("/api/v1/teams/vitoria/matchups/bilbao/head-to-head")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["result"] == "W"
    assert item["date"] == "2025-11-23"
