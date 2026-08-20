"""Engine y arranque de esquema para la base de datos de scouting.

El esquema completo (DDL + datos semilla) vive en `schema.sql`. Este módulo
solo sabe crear un engine SQLAlchemy sobre él y ejecutar ese script una vez.
"""
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine

from ... import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Orden de creación (usado también, invertido, para borrar en modo --force).
TABLE_NAMES = [
    "seasons",
    "competitions",
    "teams",
    "court_zones",
    "players",
    "player_external_ids",
    "games",
    "game_advanced_stats",
    "player_game_stats",
    "lineups",
    "lineup_players",
    "game_zone_stats",
    "shots",
    "key_events",
    "score_progression",
    "upcoming_matchups",
]

# Vistas calculadas sobre player_game_stats/games (medias por competición y
# combinadas); hay que borrarlas antes que las tablas en modo --force.
VIEW_NAMES = [
    "player_stats_by_competition",
    "player_stats_combined",
    "team_stats_by_competition",
    "team_stats_combined",
]


def _enable_foreign_keys(dbapi_connection, connection_record):
    """SQLite no valida claves foráneas salvo que se active por conexión."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_scouting_engine(database_url: Optional[str] = None) -> Engine:
    """Crea el engine de la base de datos de scouting.

    Args:
        database_url: cadena de conexión SQLAlchemy; por defecto
            `config.DATABASE_URL` (misma BD que usa el resto del proyecto).

    Returns:
        Un `Engine` con `foreign_keys=ON` si es SQLite.
    """
    url = database_url or config.DATABASE_URL
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def is_initialized(engine: Engine) -> bool:
    """Comprueba si el esquema de scouting ya existe (tabla `seasons` presente)."""
    return inspect(engine).has_table("seasons")


def init_scouting_db(engine: Engine, *, force: bool = False) -> None:
    """Crea el esquema y los datos semilla ejecutando `schema.sql`.

    No hace nada si el esquema ya existe, salvo que `force=True`, en cuyo
    caso borra antes las tablas conocidas para poder recrear la BD desde
    cero (usado por tests y por `--force` en `tools/init_scouting_db.py`).

    Args:
        engine: engine SQLAlchemy destino.
        force: si `True`, elimina el esquema existente antes de recrearlo.
    """
    if not force and is_initialized(engine):
        return

    script = SCHEMA_PATH.read_text(encoding="utf-8")
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        if force:
            cursor.execute("PRAGMA foreign_keys=OFF")
            for view in VIEW_NAMES:
                cursor.execute(f"DROP VIEW IF EXISTS {view}")
            for table in reversed(TABLE_NAMES):
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            raw_conn.commit()
        raw_conn.executescript(script)
        raw_conn.commit()
    finally:
        raw_conn.close()
