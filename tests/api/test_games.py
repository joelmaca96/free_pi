"""Tests de los endpoints de partidos (/games, /boxscore)."""
from tests.api.conftest import api_played_game, api_teams  # noqa: F401


def test_list_games(client, api_played_game, api_teams):
    """Lista partidos de un equipo con resultado W/L y fecha ISO."""
    r = client.get("/api/v1/teams/vitoria/games")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["date"] == "2025-11-23"  # ISO, no BBR
    assert item["result"] == "W"
    assert item["team_score"] == 22
    assert item["opponent"]["slug"] == "bilbao"
    assert item["advanced"]["net_rating"] == 11.4
    assert item["has_boxscore"] is True


def test_list_games_pagination(client, api_played_game, api_teams):
    """Paginación con limit/offset."""
    r = client.get("/api/v1/teams/vitoria/games?limit=1&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


def test_boxscore(client, api_played_game, api_teams):
    """Box score de un equipo en un partido."""
    gid = api_played_game.id
    r = client.get(f"/api/v1/games/{gid}/boxscore?team_slug=vitoria")
    assert r.status_code == 200
    body = r.json()
    assert body["game_id"] == gid
    assert body["team"]["slug"] == "vitoria"
    assert body["result"] == "W"
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["player_name"] == "Markus Howard"
    assert row["points"] == 22
    assert row["efg_pct"] == 0.667


def test_boxscore_game_not_found(client, api_played_game, api_teams):
    """Partido inexistente → 404 problem+json."""
    r = client.get("/api/v1/games/99999/boxscore?team_slug=vitoria")
    assert r.status_code == 404
    assert r.json()["type"].endswith("game-not-found")


def test_boxscore_team_not_found(client, api_played_game, api_teams):
    """Equipo inexistente en boxscore → 404 problem+json."""
    gid = api_played_game.id
    r = client.get(f"/api/v1/games/{gid}/boxscore?team_slug=nonexistent")
    assert r.status_code == 404
    assert r.json()["type"].endswith("team-not-found")
