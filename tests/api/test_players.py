"""Tests de los endpoints de jugadores (/roster, /players/form, /streaks, /load)."""
from tests.api.conftest import api_played_game, api_teams  # noqa: F401


def test_roster(client, api_played_game, api_teams, api_session):
    """Plantilla actual: solo jugadores con foto (plantilla oficial)."""
    from packages.baskonia_core.db import models

    api_session.add(
        models.Player(
            name="Markus Howard",
            team_id=api_teams["vitoria"].id,
            position="G",
            number="0",
            photo_url="http://example.com/howard.png",
        )
    )
    api_session.flush()
    r = client.get("/api/v1/teams/vitoria/roster")
    assert r.status_code == 200
    body = r.json()
    assert len(body["players"]) == 1
    assert body["players"][0]["name"] == "Markus Howard"
    assert body["players"][0]["form"] is not None


def test_player_form(client, api_played_game, api_teams):
    """Forma reciente por jugador."""
    r = client.get("/api/v1/teams/vitoria/players/form")
    assert r.status_code == 200
    body = r.json()
    assert body["last_n"] == 5
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["player_name"] == "Markus Howard"
    assert item["avg_pts"] == 22.0


def test_streaks(client, api_played_game, api_teams):
    """Rachas de los jugadores en una temporada."""
    r = client.get("/api/v1/teams/vitoria/players/streaks?season=2025")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == 2025
    assert isinstance(body["items"], list)


def test_load(client, api_played_game, api_teams):
    """Carga de minutos en la ventana de días."""
    r = client.get("/api/v1/teams/vitoria/players/load?window_days=14")
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 14
    assert isinstance(body["items"], list)
