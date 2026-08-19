"""Exporta el OpenAPI generado de la API al `openapi.json` versionado en la raíz.

Uso (desde la raíz del repo):
    python tools/export_openapi.py

Regenera `openapi.json` a partir de la app real. El contrato queda congelado:
`tests/api/test_contract.py` falla si el OpenAPI generado difiere del versionado.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.api.main import create_app  # noqa: E402

OPENAPI_PATH = REPO_ROOT / "openapi.json"


def main() -> None:
    """Genera y escribe el openapi.json versionado."""
    spec = create_app().openapi()
    OPENAPI_PATH.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI exportado a {OPENAPI_PATH}")


if __name__ == "__main__":
    main()
