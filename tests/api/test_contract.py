"""Test del contrato congelado: el OpenAPI generado coincide con el versionado.

El `openapi.json` versionado en la raíz del repo es la fuente de verdad del
contrato. Este test regenera el OpenAPI de la app y lo compara con el
versionado; si difieren, el contrato ha cambiado y hay que regenerarlo
(`tools/export_openapi.py`).
"""
import json
from pathlib import Path

from apps.api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "openapi.json"


def test_openapi_matches_versioned_contract():
    """El OpenAPI generado debe coincidir con el versionado en la raíz."""
    assert OPENAPI_PATH.exists(), (
        f"No existe {OPENAPI_PATH}. Regenera el contrato con tools/export_openapi.py"
    )
    generated = create_app().openapi()
    versioned = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert generated == versioned, (
        "El OpenAPI generado difiere del versionado. Regenera el contrato "
        "con tools/export_openapi.py y revisa el diff."
    )


def test_contract_has_20_endpoints():
    """El contrato expone los 20 endpoints de negocio.

    18 de la feature 012 (se eliminaron jobs/reports/admin/streaks/scout, que el
    esquema de scouting no soporta) + 2 de la feature 014 (refresco por-partido
    bajo demanda y discovery de partidos ausentes). Los paths de
    docs/openapi/redoc no cuentan como negocio.
    """
    spec = create_app().openapi()
    paths = spec["paths"]
    business_paths = [
        p for p in paths if not p.endswith(("/docs", "/redoc", "/openapi.json"))
    ]
    assert len(business_paths) == 20, f"Se esperaban 20 endpoints, hay {len(business_paths)}"


def test_contract_uses_team_id_and_game_id_text():
    """El contrato usa `team_id`/`game_id` TEXT (no slug ni game_id numérico)."""
    spec = create_app().openapi()
    paths = spec["paths"]
    # No debe quedar ningún path con {slug} ni {opponent_slug}.
    assert not any("{slug}" in p or "{opponent_slug}" in p for p in paths)
    # Debe haber paths con {team_id} y {game_id}.
    assert any("{team_id}" in p for p in paths)
    assert any("{game_id}" in p for p in paths)
    # Endpoints eliminados no deben existir.
    assert not any("streaks" in p for p in paths)
    assert not any("reports" in p for p in paths)
    assert not any("admin" in p for p in paths)
    assert not any("jobs" in p for p in paths)
    assert not any("scout" in p for p in paths)
