"""Tests de los endpoints de meta: /health y /meta/data-freshness."""


def test_health(client):
    """El endpoint de salud responde 200 con status ok."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_data_freshness(client):
    """Frescura de datos: recuentos del esquema de scouting (seed)."""
    r = client.get("/api/v1/meta/data-freshness")
    assert r.status_code == 200
    body = r.json()
    assert body["games_total"] == 5
    assert body["players_total"] == 8
    assert body["teams_total"] == 9
    assert body["upcoming_matchups_total"] == 5
    assert body["last_game_date"] == "2026-01-18"
