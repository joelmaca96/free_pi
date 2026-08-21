"""Tests del contrato de errores: respuestas `application/problem+json` (RFC 9457).

Verifica que los errores de dominio (404) y de validación (422) se traducen al
formato problem+json con `type`, `title`, `status`, `detail`, `instance`,
`request_id`.
"""


def _assert_problem(body, status, type_slug):
    assert body["status"] == status
    assert body["type"].endswith(type_slug)
    assert body["title"]
    assert "detail" in body
    assert "instance" in body
    assert "request_id" in body


def test_team_not_found_is_problem_json(client):
    r = client.get("/api/v1/teams/nonexistent")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    _assert_problem(r.json(), 404, "team-not-found")


def test_game_not_found_is_problem_json(client):
    r = client.get("/api/v1/games/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    _assert_problem(r.json(), 404, "game-not-found")


def test_validation_error_is_problem_json(client):
    """Parámetro de query inválido (fuera de rango) → 422 problem+json."""
    r = client.get("/api/v1/teams/bas/games", params={"limit": 9999})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    _assert_problem(r.json(), 422, "validation-error")


def test_unknown_route_is_problem_json(client):
    """Ruta inexistente → 404 problem+json (handler HTTP genérico)."""
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
