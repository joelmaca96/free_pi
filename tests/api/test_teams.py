"""Tests de los endpoints de equipos (/teams, /teams/{slug}, filters, summary)."""
from tests.api.conftest import api_played_game, api_teams  # noqa: F401


def test_list_teams(client, api_teams):
    """Lista los equipos conocidos."""
    r = client.get("/api/v1/teams")
    assert r.status_code == 200
    slugs = {t["slug"] for t in r.json()}
    assert slugs == {"vitoria", "bilbao"}


def test_get_team_detail(client, api_teams):
    """Detalle de un equipo por slug."""
    r = client.get("/api/v1/teams/vitoria")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "vitoria"
    assert body["name"] == "Baskonia"  # nombre para mostrar


def test_get_team_not_found(client, api_teams):
    """Slug inexistente → 404 problem+json."""
    r = client.get("/api/v1/teams/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert body["type"].endswith("team-not-found")
    assert body["status"] == 404


def test_filters(client, api_played_game, api_teams):
    """Filtros: temporadas y competiciones derivadas de los partidos."""
    r = client.get("/api/v1/teams/vitoria/filters")
    assert r.status_code == 200
    body = r.json()
    assert 2025 in body["seasons"]
    assert body["default_season"] == 2025
    codes = {lg["code"] for lg in body["leagues"]}
    assert codes == {"acb"}


def test_summary(client, api_played_game, api_teams):
    """Resumen con medias avanzadas y recuentos."""
    r = client.get("/api/v1/teams/vitoria/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["slug"] == "vitoria"
    assert body["games_played"] == 1
    assert body["advanced"]["avg_pace"] == 70.0
    assert body["advanced"]["avg_net_rating"] == 11.4


def test_summary_with_filters(client, api_played_game, api_teams):
    """Resumen filtrando por temporada/competición."""
    r = client.get("/api/v1/teams/vitoria/summary?season=2025&league=acb")
    assert r.status_code == 200
    assert r.json()["games_played"] == 1
