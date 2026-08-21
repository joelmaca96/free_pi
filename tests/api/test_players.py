"""Tests de los endpoints de jugadores: /teams/{team_id}/players/form y /players/load."""


def test_player_form(client):
    """Forma reciente de un jugador (últimos N partidos, ordenados por fecha desc)."""
    r = client.get("/api/v1/teams/bas/players/form", params={"player_id": "howard", "last_n": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["player_id"] == "howard"
    assert body["last_n"] == 2
    assert len(body["items"]) == 2
    # Orden por fecha desc: g5 (2026-01-18), g4 (2026-01-15)
    assert [i["game_id"] for i in body["items"]] == ["g5", "g4"]
    assert body["items"][0]["pts"] == 19


def test_player_form_defaults_to_first_roster_player(client):
    """Sin player_id, usa el primer jugador de la plantilla (degradado documentado)."""
    r = client.get("/api/v1/teams/bas/players/form")
    assert r.status_code == 200
    body = r.json()
    assert body["player_id"]  # no vacío
    assert body["items"]


def test_player_form_team_not_found(client):
    """Forma de un equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/players/form")
    assert r.status_code == 404


def test_player_load(client):
    """Carga de minutos por jugador en la ventana de días (transversal)."""
    r = client.get("/api/v1/teams/bas/players/load", params={"window_days": 30, "as_of": "2026-01-18"})
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 30
    assert body["as_of"] == "2026-01-18"
    assert len(body["items"]) == 8
    # Ordenado por total_minutes desc.
    totals = [i["total_minutes"] for i in body["items"]]
    assert totals == sorted(totals, reverse=True)
    # Howard juega en los 5 partidos del seed.
    howard = next(i for i in body["items"] if i["player_id"] == "howard")
    assert howard["total_minutes"] > 0


def test_player_load_team_not_found(client):
    """Carga de un equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/players/load")
    assert r.status_code == 404
