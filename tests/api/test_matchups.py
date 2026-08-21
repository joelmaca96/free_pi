"""Tests de los endpoints de enfrentamientos: /upcoming-matchups, /projection,
/head-to-head."""


def test_upcoming_matchups(client):
    """Próximos rivales, ordenados por fecha de partido."""
    r = client.get("/api/v1/upcoming-matchups")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 5
    assert body[0]["opponent"]["id"] == "rm"
    assert body[0]["match_date"] == "2026-08-27"
    assert body[0]["has_scouting_data"] is True
    assert body[0]["h2h_wins"] == 2
    # baxi no tiene datos de scouting.
    baxi = next(m for m in body if m["opponent"]["id"] == "baxi")
    assert baxi["has_scouting_data"] is False
    assert baxi["predicted_net_rating"] is None


def test_projection(client):
    """Proyección de marcador esperado entre dos equipos.

    El seed de `game_advanced_stats` no tiene ortg/drtg (solo net_rating/efg/...),
    así que la proyección se degrada a `projection: null` (comportamiento
    documentado: no inventa datos).
    """
    r = client.get("/api/v1/teams/bas/matchups/rm/projection")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["id"] == "bas"
    assert body["opponent"]["id"] == "rm"
    assert body["projection"] is None


def test_projection_team_not_found(client):
    """Proyección con equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/matchups/rm/projection")
    assert r.status_code == 404


def test_projection_opponent_not_found(client):
    """Proyección con rival inexistente → 404."""
    r = client.get("/api/v1/teams/bas/matchups/nonexistent/projection")
    assert r.status_code == 404


def test_head_to_head(client):
    """Enfrentamientos directos entre dos equipos."""
    r = client.get("/api/v1/teams/bas/matchups/rm/head-to-head")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["id"] == "bas"
    assert body["opponent"]["id"] == "rm"
    # g1 es bas vs rm.
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == "g1"
    assert body["items"][0]["team_score"] == 88
    assert body["items"][0]["opponent_score"] == 82
    assert body["items"][0]["result"] == "W"


def test_head_to_head_no_games(client):
    """Enfrentamientos directos sin partidos → lista vacía."""
    r = client.get("/api/v1/teams/bas/matchups/bay/head-to-head")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []


def test_head_to_head_team_not_found(client):
    """Head-to-head con equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/matchups/rm/head-to-head")
    assert r.status_code == 404
