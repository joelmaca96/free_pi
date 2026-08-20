"""Tests de la cola de scouting bajo demanda (/teams/{slug}/scout, /jobs/{id})."""
from tests.api.conftest import api_teams  # noqa: F401


def test_enqueue_scout_creates_queued_job(client, api_teams):
    """Encolar un scouting crea un job con status=queued."""
    r = client.post("/api/v1/teams/bilbao/scout")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["team"]["slug"] == "bilbao"
    assert body["last_n"] == 5  # valor por defecto
    assert body["error"] is None
    assert body["started_at"] is None


def test_enqueue_scout_is_idempotent_while_active(client, api_teams):
    """Encolar dos veces seguidas para el mismo equipo devuelve el mismo job."""
    first = client.post("/api/v1/teams/bilbao/scout").json()
    second = client.post("/api/v1/teams/bilbao/scout").json()
    assert first["id"] == second["id"]


def test_enqueue_scout_respects_last_n(client, api_teams):
    """El parámetro last_n se guarda en el job encolado."""
    r = client.post("/api/v1/teams/bilbao/scout?last_n=10")
    assert r.json()["last_n"] == 10


def test_enqueue_scout_unknown_team_404(client, api_teams):
    """Encolar el scouting de un equipo inexistente devuelve 404 problem+json."""
    r = client.post("/api/v1/teams/valencia/scout")
    assert r.status_code == 404
    assert r.json()["type"].endswith("team-not-found")


def test_get_latest_scout_without_jobs_returns_null(client, api_teams):
    """Sin ningún job previo, el último scouting de un equipo es null."""
    r = client.get("/api/v1/teams/bilbao/scout")
    assert r.status_code == 200
    assert r.json() is None


def test_get_latest_scout_returns_most_recent_job(client, api_teams):
    """El último scouting de un equipo devuelve el job recién encolado."""
    enqueued = client.post("/api/v1/teams/bilbao/scout").json()
    r = client.get("/api/v1/teams/bilbao/scout")
    assert r.status_code == 200
    assert r.json()["id"] == enqueued["id"]


def test_get_job_by_id(client, api_teams):
    """GET /jobs/{id} devuelve el estado de un job encolado."""
    enqueued = client.post("/api/v1/teams/bilbao/scout").json()
    r = client.get(f"/api/v1/jobs/{enqueued['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == enqueued["id"]
    assert r.json()["status"] == "queued"


def test_get_job_unknown_id_404(client, api_teams):
    """GET /jobs/{id} con un id inexistente devuelve 404 problem+json."""
    r = client.get("/api/v1/jobs/999999")
    assert r.status_code == 404
    assert r.json()["type"].endswith("job-not-found")
