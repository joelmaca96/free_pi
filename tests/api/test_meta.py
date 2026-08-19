"""Tests de los endpoints de meta (/health, /meta/data-freshness)."""
from tests.api.conftest import api_played_game  # noqa: F401  (fixture)


def test_health(client):
    """El endpoint /health responde 200 con status ok."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_data_freshness_empty(client):
    """Con BD vacía, data-freshness devuelve recuentos a cero y fecha null."""
    r = client.get("/api/v1/meta/data-freshness")
    assert r.status_code == 200
    body = r.json()
    assert body["games_total"] == 0
    assert body["boxscores_total"] == 0
    assert body["teams_total"] == 0
    assert body["last_game_date"] is None


def test_data_freshness_with_data(client, api_played_game, api_teams):
    """Con datos, data-freshness refleja los recuentos y la última fecha."""
    r = client.get("/api/v1/meta/data-freshness")
    assert r.status_code == 200
    body = r.json()
    assert body["games_total"] == 1
    assert body["boxscores_total"] == 2
    assert body["teams_total"] == 2
    assert body["last_game_date"] == "Sun, Nov 23, 2025"
