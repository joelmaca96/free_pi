"""Tests de arquitectura — Fase F1 de la migración.

Verifica la frontera del paquete de dominio `packages/baskonia_core`:

- **Regla 1**: ningún módulo bajo `packages/baskonia_core/` importa nada de la
  raíz del proyecto (`config`, `stats`, `insights`, `db`, `main`, `app`,
  `report`, `scraper`) ni de `apps/`. Solo se permiten imports relativos
  internos (`from .`, `from ..`) y de terceros/stdlib.

Esta regla garantiza que el dominio compartido es autónomo y reutilizable por
las aplicaciones futuras (API, SPA) sin acoplarse a la capa de borde actual.
"""
import ast
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "packages" / "baskonia_core"

# Módulos de la raíz que el dominio no debe importar (capa de borde).
FORBIDDEN_ROOT_MODULES = {
    "config",
    "stats",
    "insights",
    "db",
    "main",
    "app",
    "report",
    "scraper",
}

FORBIDDEN_API_MODULES = {
    "requests",
    "apps.ingest",
}


def _iter_py_files():
    """Itera los ficheros .py del paquete de dominio (recursivo)."""
    return sorted(PACKAGE_DIR.rglob("*.py"))


def _imported_absolute_names(tree):
    """Devuelve los nombres absolutos importados por un módulo (ast)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Ignora imports relativos (from . / from ..)
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_package_has_py_files():
    """El paquete de dominio existe y contiene módulos."""
    assert _iter_py_files(), "packages/baskonia_core no contiene ficheros .py"


@pytest.mark.parametrize("pyfile", _iter_py_files(), ids=lambda p: str(p.relative_to(PACKAGE_DIR)))
def test_domain_does_not_import_root_or_apps(pyfile):
    """Ningún módulo del dominio importa módulos de la raíz ni de apps/."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    imported = _imported_absolute_names(tree)

    forbidden = imported & FORBIDDEN_ROOT_MODULES
    assert not forbidden, (
        f"{pyfile.relative_to(PACKAGE_DIR)} importa módulos de la raíz: "
        f"{sorted(forbidden)}"
    )

    apps_imports = {name for name in imported if name == "apps" or name.startswith("apps.")}
    assert not apps_imports, (
        f"{pyfile.relative_to(PACKAGE_DIR)} importa de apps/: {sorted(apps_imports)}"
    )


def test_api_does_not_import_requests_or_ingest() -> None:
    """`apps/api` must stay in read-only boundary over shared domain."""
    api_dir = Path("apps") / "api"
    for py_file in sorted(api_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        imported = _imported_absolute_names(tree)
        bad = sorted(
            name
            for name in imported
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in FORBIDDEN_API_MODULES)
        )
        assert not bad, (
            f"Forbidden imports in {py_file}: {bad}. "
            "API package must not depend on requests or apps.ingest."
        )
