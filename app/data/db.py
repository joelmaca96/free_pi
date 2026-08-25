"""Motor de solo lectura sobre la base de datos de scouting.

Reutiliza `create_scouting_engine()` de `packages.baskonia_core.db.scouting` —
la app **nunca** crea ni migra el esquema (eso es responsabilidad exclusiva de
`ingest/`/`tools/init_scouting_db.py`, ver `local/features/001-interfaz-baskonia/
01_design.md` §3). Si el esquema no existe todavía, falla con un mensaje
explícito en vez de intentar poblarlo.
"""
import sys
from pathlib import Path
from typing import Optional

import streamlit as st
from sqlalchemy.engine import Engine

# `app/` no vive en la raíz del repo, así que hay que añadir la raíz a
# sys.path para poder importar `packages.*` — mismo patrón que
# `tools/init_scouting_db.py`.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packages.baskonia_core.db.scouting import create_scouting_engine, is_initialized  # noqa: E402


@st.cache_resource(show_spinner=False)
def get_read_engine(database_url: Optional[str] = None) -> Engine:
    """Engine SQLAlchemy sobre `data/baskonia.db`, cacheado por proceso.

    Args:
        database_url: URL SQLAlchemy; por defecto `config.DATABASE_URL` (la
            misma variable que usa `ingest/`, ver `.env`).

    Returns:
        Engine listo para lectura (`foreign_keys=ON`, `busy_timeout`, ver
        `packages/baskonia_core/db/scouting/engine.py` — la app no añade
        pragmas propios).

    Raises:
        RuntimeError: si el esquema de scouting no existe en la base de datos
            configurada. La app no lo crea (ver docstring del módulo).
    """
    engine = create_scouting_engine(database_url)
    if not is_initialized(engine):
        raise RuntimeError(
            "El esquema de scouting no existe en la base de datos configurada "
            f"({engine.url}). Ejecuta `python tools/init_scouting_db.py` o "
            "`python -m ingest.run_all --season <año>` (que lo crea "
            "automáticamente) antes de abrir la interfaz."
        )
    return engine
