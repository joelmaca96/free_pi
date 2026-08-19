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
    "beautifulsoup4",
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


def test_ingest_can_import_network_libs() -> None:
    """`apps/ingest` es la capa de red legítima: SÍ puede importar requests/bs4.

    Complementa la frontera de `apps/api` (que no puede): la ingesta es la única
    capa con red del proyecto, así que la regla se explicita en ambas direcciones.
    Se verifica sobre `apps/ingest/scraper/` (la subcapa de red): al menos un
    módulo de la subcapa debe importar una librería de red (requests/bs4). No se
    exige a cada fichero porque `bbr.py` delega en `.client`/`.parser`, que son
    quienes tocan la red.
    """
    scraper_dir = Path("apps") / "ingest" / "scraper"
    network_files = [
        py_file
        for py_file in sorted(scraper_dir.rglob("*.py"))
        if py_file.name != "__init__.py"
    ]
    assert network_files, "apps/ingest/scraper no contiene ficheros .py"

    uses_network = False
    for py_file in network_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        imported = _imported_absolute_names(tree)
        if imported & {"requests", "beautifulsoup4", "bs4"}:
            uses_network = True
            break
    assert uses_network, (
        "apps/ingest/scraper no importa ninguna librería de red (requests/bs4). "
        "Si la ingesta dejara de usar red, revisar esta regla."
    )


# --- Puentes de migración ---------------------------------------------------

# Módulos de la raíz que NO son puentes (capa de borde con código propio).
NON_BRIDGE_ROOT_MODULES = {"app"}

BRIDGE_MARKER = "PUENTE DE MIGRACIÓN"

ROOT_DIR = Path(__file__).resolve().parent.parent


def _root_bridge_files():
    """Ficheros .py de la raíz que deben ser puentes de migración."""
    return sorted(
        py_file
        for py_file in ROOT_DIR.glob("*.py")
        if py_file.stem not in NON_BRIDGE_ROOT_MODULES
    )


@pytest.mark.parametrize("pyfile", _root_bridge_files(), ids=lambda p: p.name)
def test_root_bridges_are_marked(pyfile):
    """Todo módulo puente de la raíz lleva el marcador de retirada en F7.

    Principio 3 de `doc/arquitectura/02_migration.md`: los puentes son
    temporales y explícitos. Sin el marcador, la limpieza de F7 no los
    encuentra. Regresión: `config.py` era una copia literal de
    `packages/baskonia_core/config.py` sin marcador, así que habría
    sobrevivido a F7 y divergido en silencio.
    """
    source = pyfile.read_text(encoding="utf-8")
    assert BRIDGE_MARKER in source, (
        f"{pyfile.name} está en la raíz y no lleva '{BRIDGE_MARKER}'. "
        "Si es un puente, márcalo; si es código propio, añádelo a "
        "NON_BRIDGE_ROOT_MODULES."
    )


@pytest.mark.parametrize("pyfile", _root_bridge_files(), ids=lambda p: p.name)
def test_root_bridges_do_not_duplicate_domain(pyfile):
    """Un puente reexporta el dominio; no redefine su contenido.

    Se comprueba que no declara constantes de configuración ni clases/funciones
    propias: si lo hace, es una copia y no un puente, y las dos definiciones
    divergirán.
    """
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    own_definitions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not own_definitions, (
        f"{pyfile.name} define {own_definitions} en vez de solo reexportar el dominio."
    )

    assignments = [
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.isupper()
    ]
    assert not assignments, (
        f"{pyfile.name} redefine las constantes {assignments}; deben vivir solo "
        "en packages/baskonia_core/."
    )
