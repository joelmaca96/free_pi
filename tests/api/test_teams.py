"""Tests de los endpoints de equipos: /teams, /teams/{team_id}, /filters,
/summary, /games, /roster, /rating-trend, /schedule-difficulty, /narrative."""


def test_list_teams(client):
    """Lista todos los equipos conocidos (id TEXT + nombre + is_own_team)."""
    r = client.get("/api/v1/teams")
    assert r.status_code == 200
    teams = r.json()
    ids = {t["id"] for t in teams}
    assert ids == {"bas", "rm", "fcb", "bay", "baxi", "jb", "val", "gc", "uni"}
    bas = next(t for t in teams if t["id"] == "bas")
    assert bas["name"] == "Baskonia"
    assert bas["is_own_team"] is True
    rm = next(t for t in teams if t["id"] == "rm")
    assert rm["is_own_team"] is False


def test_get_team_detail(client):
    """Detalle de un equipo por id TEXT."""
    r = client.get("/api/v1/teams/bas")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "bas"
    assert body["name"] == "Baskonia"
    assert body["is_own_team"] is True


def test_get_team_not_found(client):
    """Id inexistente → 404 problem+json."""
    r = client.get("/api/v1/teams/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert body["type"].endswith("team-not-found")
    assert body["status"] == 404


def test_filters(client):
    """Filtros: temporadas y competiciones del esquema de scouting."""
    r = client.get("/api/v1/teams/bas/filters")
    assert r.status_code == 200
    body = r.json()
    assert body["seasons"] == ["2025-2026"]
    assert body["default_season"] == "2025-2026"
    comps = {c["name"] for c in body["competitions"]}
    assert comps == {"ACB", "Euroliga", "Copa del Rey", "Supercopa"}


def test_filters_team_not_found(client):
    """Filtros de un equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/filters")
    assert r.status_code == 404


def test_summary(client):
    """Resumen del equipo: identidad, filtros y medias avanzadas."""
    r = client.get("/api/v1/teams/bas/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["id"] == "bas"
    assert body["games_played"] == 5
    assert body["games_upcoming"] == 5
    adv = body["advanced"]
    # Media de net_rating del seed: (7.8 + 5.1 - 9.4 + 16.2 + 8.9) / 5
    assert adv["avg_net_rating"] is not None
    assert abs(adv["avg_net_rating"] - (7.8 + 5.1 - 9.4 + 16.2 + 8.9) / 5) < 1e-6


def test_list_games(client):
    """Partidos de un equipo (jugados y pendientes), paginados."""
    r = client.get("/api/v1/teams/bas/games")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    first = body["items"][0]
    assert first["id"] == "g1"
    assert first["competition_name"] == "Euroliga"
    assert first["is_home"] is True
    assert first["opponent"]["id"] == "rm"
    assert first["team_score"] == 88
    assert first["opponent_score"] == 82
    assert first["result"] == "W"


def test_list_games_pagination(client):
    """Paginación de partidos (limit/offset)."""
    r = client.get("/api/v1/teams/bas/games", params={"limit": 2, "offset": 0})
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_list_games_team_not_found(client):
    """Partidos de un equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/games")
    assert r.status_code == 404


def test_roster(client):
    """Plantilla de un equipo para la temporada (por defecto la última)."""
    r = client.get("/api/v1/teams/bas/roster")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["id"] == "bas"
    assert body["season_label"] == "2025-2026"
    assert len(body["players"]) == 8
    howard = next(p for p in body["players"] if p["id"] == "howard")
    assert howard["name"] == "Marcus Howard"
    assert howard["position"] == "Base"
    assert howard["gp"] == 5


def test_roster_team_not_found(client):
    """Plantilla de un equipo inexistente → 404."""
    r = client.get("/api/v1/teams/nonexistent/roster")
    assert r.status_code == 404


def test_rating_trend(client):
    """Tendencia ORtg/DRtg de un equipo en sus últimos N partidos."""
    r = client.get("/api/v1/teams/bas/rating-trend", params={"last_n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["team_id"] == "bas"
    assert body["last_n"] == 3
    assert len(body["items"]) == 3
    # Ordenado por fecha desc: g5, g4, g3
    assert [i["game_id"] for i in body["items"]] == ["g5", "g4", "g3"]
    # El seed de game_advanced_stats no tiene ortg/drtg (solo net_rating/efg/...),
    # así que estos campos son null en la tendencia.
    assert body["items"][0]["ortg"] is None
    assert body["items"][0]["drtg"] is None


def test_schedule_difficulty(client):
    """Dificultad del próximo tramo de calendario (de upcoming_matchups)."""
    r = client.get("/api/v1/teams/bas/schedule-difficulty", params={"next_n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["games_considered"] == 3
    # rm, fcb, bay tienen has_scouting_data=1
    assert body["opponents_scouted"] == 3
    assert body["avg_opponent_net_rating"] is not None
    assert len(body["opponents"]) == 3
    assert body["opponents"][0]["opponent_id"] == "rm"


def test_narrative(client):
    """Narrativa de scouting derivada de los últimos partidos del equipo."""
    r = client.get("/api/v1/teams/bas/narrative")
    assert r.status_code == 200
    body = r.json()
    assert body["team_id"] == "bas"
    assert body["narrative"] is not None
    # Último partido del seed es g5 (victoria 84-79 vs val)
    assert "victoria" in body["narrative"]
