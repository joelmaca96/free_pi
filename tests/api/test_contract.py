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


def test_contract_has_21_endpoints():
    """El contrato expone los 19 endpoints del §5.1 (17/18 como 501) más los 2
    de la cola de scouting bajo demanda (`/teams/{slug}/scout`, `/jobs/{id}`),
    añadidos fuera del alcance original de F3-F5 (ver doc/arquitectura/02_migration.md).
    """
    spec = create_app().openapi()
    paths = spec["paths"]
    # paths de negocio + docs/openapi/redoc no cuentan como paths de negocio
    business_paths = [p for p in paths if not p.endswith(("/docs", "/redoc", "/openapi.json"))]
    assert len(business_paths) == 21, f"Se esperaban 21 endpoints, hay {len(business_paths)}"
