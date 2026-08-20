"""Sesión SQLAlchemy para la API (y futuras apps) sobre la BD compartida.

Configura un engine con las opciones que necesita un servidor HTTP síncrono
(uvicorn con threadpool) leyendo la misma BD SQLite que escribe el pipeline:

- **WAL** (`PRAGMA journal_mode=WAL`): permite que la API siga leyendo durante
  una ejecución del pipeline (lectores y escritor no se bloquean entre sí).
- **`busy_timeout`** (`PRAGMA busy_timeout=30000`): si dos escritores coinciden
  (el `cron` del pipeline y el worker de `apps/ingest/worker.py` consumiendo
  `ingest_jobs`), SQLite espera hasta 30s en vez de fallar al instante con
  "database is locked".
- **`check_same_thread=False`**: uvicorn atiende peticiones desde un threadpool;
  el engine debe poder usarse desde varios hilos.
- **`pool_pre_ping`**: descarta conexiones muertas antes de usarlas.

Sustituye a `models._add_missing_columns()` en lo que respecta a la gestión del
esquema: el esquema ahora lo versiona Alembic (ver `db/migrations/`), no un
`ALTER TABLE` manual.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .. import config


def _enable_wal(dbapi_connection, connection_record):
    """Activa journal mode WAL y busy_timeout en cada conexión SQLite nueva."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_db_engine(database_url: str | None = None):
    """Crea el engine de la API sobre la BD compartida.

    Args:
        database_url: cadena de conexión; por defecto la de `config.DATABASE_URL`.

    Returns:
        Un `sqlalchemy.engine.Engine` con WAL, `check_same_thread=False` y
        `pool_pre_ping` activados.
    """
    url = database_url or config.DATABASE_URL
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_wal)
    return engine


def create_session_factory(database_url: str | None = None) -> sessionmaker:
    """Crea una fábrica de sesiones sobre el engine de la API.

    Args:
        database_url: cadena de conexión; por defecto la de `config.DATABASE_URL`.

    Returns:
        Un `sessionmaker` configurado con el engine de la API.
    """
    engine = create_db_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
