"""Tests de los endpoints de informes (501 en F3) y admin (data-quality)."""
from tests.api.conftest import api_played_game, api_teams  # noqa: F401


def test_report_scouting_pdf_not_implemented(client, api_teams):
    """El informe de scouting devuelve 501 en F3 (implementación en F6)."""
    r = client.get("/api/v1/teams/vitoria/reports/scouting.pdf")
    assert r.status_code == 501
    body = r.json()
    assert "detail" in body


def test_report_roster_pptx_not_implemented(client, api_teams):
    """El informe de roster devuelve 501 en F3 (implementación en F6)."""
    r = client.get("/api/v1/teams/vitoria/reports/roster.pptx")
    assert r.status_code == 501
    body = r.json()
    assert "detail" in body


def test_data_quality_healthy(client, api_played_game, api_teams):
    """Con datos coherentes, data-quality es healthy."""
    r = client.get("/api/v1/admin/data-quality")
    assert r.status_code == 200
    body = r.json()
    assert body["healthy"] is True
    assert body["warnings"] == []
