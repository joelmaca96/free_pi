"""Tests del contrato de errores: toda respuesta >=400 es problem+json (RFC 9457).

Verifica que los campos obligatorios (type, title, status, detail, instance,
request_id) están presentes y que el Content-Type es application/problem+json.
"""
from tests.api.conftest import api_played_game, api_teams  # noqa: F401


def _assert_problem_json(r):
    assert r.status_code >= 400
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    for field in ("type", "title", "status", "detail", "instance", "request_id"):
        assert field in body, f"Falta campo {field} en problem+json"
    assert body["status"] == r.status_code
    return body


def test_404_team_is_problem_json(client, api_teams):
    r = client.get("/api/v1/teams/nonexistent")
    _assert_problem_json(r)


def test_404_game_is_problem_json(client, api_played_game, api_teams):
    r = client.get("/api/v1/games/99999/boxscore?team_slug=vitoria")
    _assert_problem_json(r)


def test_422_validation_is_problem_json(client, api_teams):
    """Un parámetro inválido (p.ej. limit=0) → 422 problem+json."""
    r = client.get("/api/v1/teams/vitoria/games?limit=0")
    _assert_problem_json(r)


def test_404_unknown_route_is_problem_json(client, api_teams):
    """Una ruta inexistente → 404 problem+json (no HTML)."""
    r = client.get("/api/v1/no-such-route")
    _assert_problem_json(r)


def test_501_report_is_problem_json(client, api_teams):
    """Los informes 501 también usan problem+json."""
    r = client.get("/api/v1/teams/vitoria/reports/scouting.pdf")
    _assert_problem_json(r)
