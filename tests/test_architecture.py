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

# Módulos de ingest/ que apps/api puede importar explícitamente: los dos
# entrypoints de orquestación que la feature 014 (revisión tras gate humano,
# ver local/features/014-auto-fetch-datos-faltantes/01_design.md, Decisión 1)
# autoriza para disparar refresco/discovery bajo demanda vía BackgroundTasks
# en el mismo proceso. Lista cerrada, no un prefijo laxo `ingest.*`: cualquier
# otro submódulo de ingest/ (ingest.common.loader, ingest.acb.client, ingest
# a secas...) sigue prohibido, para que la API no reimplemente scraping por
# su cuenta ni toque el resto de ingest/ sin pasar por estos dos entrypoints
# (que no fabrican datos y capturan sus propios errores, # noqa: BLE001).
ALLOWED_INGEST_MODULES = {"ingest.acb.pipeline", "ingest.euroleague.pipeline"}

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


def _imported_full_names(tree):
    """Nombres de import completos (dotted), sin truncar al primer segmento.

    A diferencia de `_imported_absolute_names` (usada por el resto de
    reglas de este fichero), aquí hace falta distinguir
    `ingest.acb.pipeline` de `ingest.acb.client`/`ingest.common.loader`.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
    return names


def test_api_does_not_import_requests_or_ingest() -> None:
    """`apps/api` solo puede importar `ingest/` a través de la excepción explícita.

    Feature 014 (revisión tras gate humano): `apps/api` dispara refresco/
    discovery bajo demanda con `fastapi.BackgroundTasks` en el mismo proceso,
    así que necesita `ingest.acb.pipeline`/`ingest.euroleague.pipeline`
    directamente (antes prohibido implícitamente, aislado vía subproceso).
    Se permite EXACTAMENTE esos dos módulos (`ALLOWED_INGEST_MODULES`, lista
    cerrada); se sigue prohibiendo `requests`/`beautifulsoup4` a pelo y
    cualquier otro submódulo de `ingest`. Convención requerida en el código
    nuevo: usar `import ingest.acb.pipeline` / `import ingest.euroleague.pipeline`
    (no `from ingest.acb import pipeline`), para que esta comprobación por
    nombre completo de módulo sea inequívoca.
    """
    api_dir = ROOT_DIR / "apps" / "api"
    for py_file in sorted(api_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        imported_top_level = _imported_absolute_names(tree)
        bad = sorted(
            name
            for name in imported_top_level
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in FORBIDDEN_API_MODULES)
        )
        assert not bad, (
            f"Forbidden imports in {py_file}: {bad}. "
            "API package must not depend on requests/beautifulsoup4 or apps.ingest."
        )

        imported_full = _imported_full_names(tree)
        ingest_imports = {n for n in imported_full if n == "ingest" or n.startswith("ingest.")}
        disallowed = sorted(ingest_imports - ALLOWED_INGEST_MODULES)
        assert not disallowed, (
            f"{py_file}: import de ingest/ no autorizado: {disallowed}. "
            f"Solo se permite {sorted(ALLOWED_INGEST_MODULES)} (ver 01_design.md, "
            "Decisión 1, feature 014)."
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


# --- Retirada de Streamlit (F7) ---------------------------------------------
#
# La feature "Eliminar Streamlit y quedarse solo con la interfaz web" retiró
# la app Streamlit (`app.py`), el arnés de paridad (`tools/parity_api.py`) y
# las dependencias `streamlit`/`fpdf2`/`python-pptx`/`Pillow`, dejando la SPA
# React + API FastAPI como única GUI. Estos tests son el guardián de regresión:
# si alguien reintroduce Streamlit (o sus deps) en el futuro, la suite lo
# detecta de forma hermética (sin red ni BD real).

# Dependencias de la raíz que la app Streamlit usaba y que deben permanecer
# ausentes de `requirements.txt`.
STREAMLIT_DEPENDENCIES = {"streamlit", "fpdf2", "python-pptx", "Pillow"}

# Directorios que se excluyen del escaneo de imports (venv, dependencias de
# node y el directorio local del pipeline agéntico).
EXCLUDED_DIRS = {".venv", "node_modules", "local"}


def _repo_py_files():
    """Itera los ficheros .py del repo excluyendo venv/node_modules/local."""
    return sorted(
        py_file
        for py_file in ROOT_DIR.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in py_file.parts)
    )


def test_app_py_does_not_exist():
    """`app.py` (app Streamlit) no existe en la raíz del repo.

    Criterio de aceptación 1 de `01_design.md`: la app Streamlit se borró
    definitivamente (`git rm`). Si reaparece, es una regresión de la retirada.
    """
    assert not (ROOT_DIR / "app.py").exists(), (
        "app.py (app Streamlit) no debería existir; la UI es la SPA + API."
    )


def test_parity_api_does_not_exist():
    """`tools/parity_api.py` (arnés de paridad API↔Streamlit) no existe.

    Criterio de aceptación 1: el arnés de paridad quedó obsoleto al retirar
    Streamlit y se borró. La línea base `tests/parity/baseline/` se conserva
    (la usan los tests de contrato de la API), pero el arnés no.
    """
    assert not (ROOT_DIR / "tools" / "parity_api.py").exists(), (
        "tools/parity_api.py no debería existir; su propósito (paridad "
        "API↔Streamlit) desapareció con la retirada de Streamlit."
    )


@pytest.mark.parametrize("dependency", sorted(STREAMLIT_DEPENDENCIES))
def test_requirements_txt_has_no_streamlit_dependency(dependency):
    """`requirements.txt` de la raíz no contiene la dependencia `{dependency}`.

    Criterio de aceptación 2: `streamlit`, `fpdf2`, `python-pptx` y `Pillow`
    se eliminaron de `requirements.txt` (solo las usaba `app.py`).
    """
    requirements = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert dependency.lower() not in requirements.lower(), (
        f"requirements.txt no debería contener '{dependency}'; se eliminó "
        "con la retirada de Streamlit."
    )


def test_no_streamlit_imports_in_python_files():
    """Ningún fichero .py del repo importa `streamlit`.

    Criterio de aceptación 1: el grep de ausencia no debe devolver nada salvo
    los docs históricos. Se escanea el AST de cada fichero .py (excluyendo
    `.venv/`, `node_modules/`, `local/`) buscando imports de `streamlit`.
    """
    offenders = []
    for py_file in _repo_py_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        imported = _imported_absolute_names(tree)
        if "streamlit" in imported:
            offenders.append(str(py_file.relative_to(ROOT_DIR)))
    assert not offenders, (
        "Se encontraron imports de streamlit en: "
        f"{sorted(offenders)}. Streamlit se retiró; la UI es la SPA + API."
    )


def test_vscode_tasks_have_no_launch_app():
    """.vscode/tasks.json no contiene la tarea "Launch app".

    Criterio de aceptación 3: la tarea de arranque de la app Streamlit se
    eliminó; solo queda "Run tests".
    """
    tasks = (ROOT_DIR / ".vscode" / "tasks.json").read_text(encoding="utf-8")
    assert "Launch app" not in tasks, (
        ".vscode/tasks.json no debería contener la tarea 'Launch app' de "
        "Streamlit."
    )


def test_vscode_settings_have_no_launch_app():
    """.vscode/settings.json no contiene el botón "Launch app".

    Criterio de aceptación 3: el botón de arranque de la app Streamlit se
    eliminó; solo queda "Run tests".
    """
    settings = (ROOT_DIR / ".vscode" / "settings.json").read_text(encoding="utf-8")
    assert "Launch app" not in settings, (
        ".vscode/settings.json no debería contener el botón 'Launch app' de "
        "Streamlit."
    )


def test_gitignore_has_no_streamlit_logs_section():
    """.gitignore no contiene la sección "Logs de Streamlit".

    Criterio de aceptación 4: la sección `.streamlit/logs/` se eliminó del
    `.gitignore` junto con la retirada de Streamlit.
    """
    gitignore = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "streamlit" not in gitignore.lower(), (
        ".gitignore no debería contener referencias a Streamlit "
        "(`.streamlit/logs/` se eliminó)."
    )


def test_api_cors_default_has_no_streamlit_origin():
    """`apps/api/settings.py` no incluye el origen CORS de Streamlit (8501).

    Criterio de aceptación 5: el origen por defecto `http://localhost:8501`
    (puerto de Streamlit) se eliminó del CORS; queda solo `:5173` (dev SPA).
    """
    settings = (ROOT_DIR / "apps" / "api" / "settings.py").read_text(encoding="utf-8")
    assert "8501" not in settings, (
        "apps/api/settings.py no debería incluir el origen CORS "
        "http://localhost:8501 (puerto de Streamlit)."
    )
