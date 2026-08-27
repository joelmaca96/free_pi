"""Engine y arranque de esquema para la base de datos de scouting.

El esquema completo (DDL + datos semilla) vive en `schema.sql`. Este módulo
solo sabe crear un engine SQLAlchemy sobre él y ejecutar ese script una vez.
"""
import os
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine

from ... import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Orden de creación (usado también, invertido, para borrar en modo --force).
TABLE_NAMES = [
    "seasons",
    "competitions",
    "teams",
    "team_external_ids",
    "court_zones",
    "players",
    "player_external_ids",
    "games",
    "game_advanced_stats",
    "game_team_quarter_stats",
    "player_game_stats",
    "lineups",
    "lineup_players",
    "lineup_stints",
    "lineup_stint_players",
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
    # Contexto de liga y equipo de quinteto — las usa el asistente de scouting
    # (`app/assistant/`, ver `local/features/005-chatbot/01_design.md` §10.1).
    # Van DESPUÉS de las de medias en esta lista y en `schema.sql`: se calculan
    # sobre ellas.
    "team_style_percentiles",
    "player_percentiles",
    "lineup_team",
]

# Tablas añadidas a `schema.sql` después de que ya hubiera bases de datos
# reales, igual que `_ADDITIVE_COLUMN_MIGRATIONS` pero a nivel de tabla: una
# BD ya inicializada nunca vuelve a ejecutar el script entero, así que sin
# esto una tabla nueva no aparece jamás salvo recreando la BD desde cero y
# perdiendo todo lo ingerido. Se crean con su DDL literal de `schema.sql`
# (junto a los índices que la nombren), así que no hay una segunda definición
# que se pueda desincronizar.
_ADDITIVE_TABLES = ["lineup_stints", "lineup_stint_players"]

# Columnas añadidas a `schema.sql` DESPUÉS de que ya hubiera bases de datos
# `data/baskonia.db` reales desplegadas con datos ingeridos de verdad. Este
# proyecto no tiene un sistema de migraciones (ni Alembic ni versionado de
# esquema, ver `doc/features/ingestor/01_estado.md`) — sin esto, cualquier
# columna nueva nullable en `schema.sql` deja rota a una BD ya inicializada
# hasta que alguien la borra y la recrea desde cero con `--force`, perdiendo
# TODO lo ya ingerido (partidos/boxscores/tiros...) solo por una columna de
# más. `init_scouting_db` aplica estas entradas con `ALTER TABLE ... ADD
# COLUMN` cuando faltan, de forma idempotente y sin tocar ninguna fila
# existente. Todas nullable a propósito (si no lo fueran, `ADD COLUMN`
# necesitaría un `DEFAULT` para las filas ya existentes).
_ADDITIVE_COLUMN_MIGRATIONS = [
    # (tabla, columna, tipo SQL) — mismo orden en que se añadieron a schema.sql.
    ("teams", "logo_url", "TEXT"),
    ("players", "photo_local_path", "TEXT"),
    ("shots", "located", "INTEGER"),
    ("player_game_stats", "ftm", "INTEGER"),
    ("player_game_stats", "fta", "INTEGER"),
    ("game_advanced_stats", "ftm", "INTEGER"),
    ("game_advanced_stats", "fta", "INTEGER"),
    # Equipo del quinteto: antes se infería por el equipo ACTUAL de sus cinco
    # jugadores, lo que estropea hacia atrás cualquier agregado histórico en
    # cuanto hay un traspaso (ver `lineups.team_id` en `schema.sql`).
    ("lineups", "team_id", "TEXT"),
]

# Las VISTAS (`VIEW_NAMES`) no se migran con `ALTER TABLE`: se recrean enteras
# desde `schema.sql` en cada `init_scouting_db`. Son objetos derivados —
# ninguna guarda datos, así que borrarlas y volver a crearlas no pierde nada—
# y SQLite congela su definición al crearlas: una BD ya existente se quedaba
# con la versión vieja de `player_stats_combined`/`team_stats_*` para siempre,
# aunque `schema.sql` ganara columnas nuevas (las de tiros libres del
# 2026-08-24 son el primer caso real). Sin esto, `_apply_additive_migrations`
# añadía `player_game_stats.ftm` pero la vista seguía sin exponerla y la
# interfaz veía "no such column: ft_pct".
_CREATE_VIEW_RE = re.compile(r"CREATE\s+VIEW\s+(\w+)\b.*?;", re.IGNORECASE | re.DOTALL)

# Mismo truco para las tablas/índices de `_ADDITIVE_TABLES`: se extrae su DDL
# literal de `schema.sql` en vez de repetirlo aquí.
_CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(\w+)\b.*?;", re.IGNORECASE | re.DOTALL)
_CREATE_INDEX_RE = re.compile(r"CREATE\s+INDEX\s+(\w+)\s+ON\s+(\w+)\b.*?;", re.IGNORECASE | re.DOTALL)

# `court_zones` es una tabla de CATÁLOGO (geometría del mapa de tiros), no de
# datos ingeridos, pero a diferencia de las columnas/tablas de arriba su seed
# vive en un `INSERT` normal de `schema.sql` — que, igual que el resto del
# script, solo se ejecuta una vez, al crear la BD desde cero. Sin esto, una
# `data/baskonia.db` ya inicializada se queda para siempre con la geometría
# de zonas de cuando se creó, aunque `schema.sql` gane filas nuevas o
# reajuste las existentes (el reteselado de 2026-08-27, ver `schema.sql`,
# es el primer caso real). Se extrae el `INSERT` literal igual que
# `_create_missing_tables` hace con las tablas — una sola definición, no dos
# que puedan desincronizarse — y se relanza como upsert por `id`.
_INSERT_COURT_ZONES_RE = re.compile(r"INSERT\s+INTO\s+court_zones\b.*?;", re.IGNORECASE | re.DOTALL)


# Milisegundos que un escritor espera a que se libere el lock de la BD antes de
# fallar con "database is locked". Necesario desde que la API puede disparar una
# escritura (refresco de partido bajo demanda) en un hilo de su propio proceso
# mientras atiende lecturas: sin `busy_timeout`, SQLite falla al instante en vez
# de esperar. Sobreescribible con `SQLITE_BUSY_TIMEOUT_MS`.
BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "15000"))


def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    """Pragmas por conexión: claves foráneas (SQLite no las valida por defecto)
    y `busy_timeout` (ver `BUSY_TIMEOUT_MS`)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.close()


def create_scouting_engine(database_url: Optional[str] = None) -> Engine:
    """Crea el engine de la base de datos de scouting.

    Args:
        database_url: cadena de conexión SQLAlchemy; por defecto
            `config.DATABASE_URL` (misma BD que usa el resto del proyecto).

    Returns:
        Un `Engine` con `foreign_keys=ON` y `busy_timeout` si es SQLite.
    """
    url = database_url or config.DATABASE_URL
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def is_initialized(engine: Engine) -> bool:
    """Comprueba si el esquema de scouting ya existe (tabla `seasons` presente)."""
    return inspect(engine).has_table("seasons")


def _apply_additive_migrations(engine: Engine) -> None:
    """Añade con `ALTER TABLE ... ADD COLUMN` cualquier columna de
    `_ADDITIVE_COLUMN_MIGRATIONS` que falte todavía — no hace nada si ya están
    todas (BD recién creada desde `schema.sql`, o ya migrada en una llamada
    anterior). Ver la constante para el porqué."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDITIVE_COLUMN_MIGRATIONS:
            if not inspector.has_table(table):
                continue  # tabla que tampoco existe todavía: la trae schema.sql, no esto
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            if column not in existing_columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _create_missing_tables(engine: Engine) -> None:
    """Crea las tablas de `_ADDITIVE_TABLES` que falten, con su DDL de `schema.sql`.

    Idempotente y no destructiva: una tabla que ya existe no se toca (ni se
    recrea ni se compara), porque a diferencia de una vista sí guarda datos.
    Ver `_ADDITIVE_TABLES` para el porqué.
    """
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    tables = {m.group(1): m.group(0) for m in _CREATE_TABLE_RE.finditer(script)}
    indexes = [(m.group(1), m.group(2), m.group(0)) for m in _CREATE_INDEX_RE.finditer(script)]
    inspector = inspect(engine)

    with engine.begin() as conn:
        for name in _ADDITIVE_TABLES:
            if inspector.has_table(name) or name not in tables:
                continue
            conn.execute(text(tables[name].rstrip().rstrip(";")))
            for index_name, index_table, ddl in indexes:
                if index_table == name:
                    conn.execute(text(ddl.rstrip().rstrip(";").replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1)))


def _sync_court_zones(engine: Engine) -> None:
    """Upsert por `id` de las filas de `court_zones` contra las de `schema.sql`.

    Añade las filas nuevas y REAJUSTA límites/etiqueta de las que cambian de
    geometría (ver el reteselado de 2026-08-27 documentado en `schema.sql`) —
    nunca borra una fila existente, así que ningún `shots.zone_id` que ya
    apunte a un id antiguo se queda huérfano. Idempotente: sobre una BD ya al
    día, el `UPDATE` de cada fila escribe los mismos valores que ya tenía.

    OJO: esto NO reclasifica los tiros ya cargados (`shots.zone_id` se fija
    una vez, al ingerir — ver `ingest/common/raw_game.py`) ni recalcula
    `game_zone_stats` a partir de la geometría nueva. Para una BD con datos
    reales ya ingeridos antes del reteselado, hace falta además
    `tools/retile_court_zones.py --apply` (mismo patrón que
    `tools/fix_shot_coords.py` para las coordenadas).
    """
    if not inspect(engine).has_table("court_zones"):
        return  # BD "antigua" simulada en tests, sin ni siquiera la tabla — nada que sincronizar
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    match = _INSERT_COURT_ZONES_RE.search(script)
    if match is None:
        return  # `schema.sql` sin seed de zonas (no debería pasar) — no se inventa geometría
    upsert_sql = match.group(0).rstrip().rstrip(";") + (
        " ON CONFLICT (id) DO UPDATE SET"
        " label = excluded.label, x_min = excluded.x_min, x_max = excluded.x_max,"
        " y_min = excluded.y_min, y_max = excluded.y_max"
    )
    with engine.begin() as conn:
        conn.execute(text(upsert_sql))


def _refresh_views(engine: Engine) -> None:
    """Recrea todas las vistas de `schema.sql` (DROP + CREATE), idempotente.

    Ver `_CREATE_VIEW_RE` para el porqué. Se ejecuta DESPUÉS de
    `_apply_additive_migrations`: una vista nueva puede referirse a una
    columna que esa función acaba de añadir. También borra las vistas de
    `VIEW_NAMES` que ya no estén en `schema.sql` (una vista retirada no debe
    sobrevivir en las BD antiguas).
    """
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    definitions = [(m.group(1), m.group(0)) for m in _CREATE_VIEW_RE.finditer(script)]
    obsolete = [name for name in VIEW_NAMES if name not in {n for n, _ in definitions}]

    with engine.begin() as conn:
        for name in obsolete:
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        for name, ddl in definitions:
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(ddl.rstrip().rstrip(";")))


def init_scouting_db(engine: Engine, *, force: bool = False) -> None:
    """Crea el esquema y los datos semilla ejecutando `schema.sql`.

    Si el esquema ya existe y no se pide `force`, no lo recrea — pero sí
    aplica `_apply_additive_migrations` (columnas nuevas nullable que
    `schema.sql` haya ganado desde que se creó esta BD en concreto),
    `_create_missing_tables` (tablas nuevas, ver `_ADDITIVE_TABLES`) y
    `_refresh_views` (las vistas, que SQLite congela al crearlas), para que
    una BD con datos reales no quede rota por una columna o una tabla de más
    sin tener que borrarla y perder todo lo ingerido. `force=True` (usado por
    tests y `--force` en `tools/init_scouting_db.py`) borra antes las
    tablas conocidas para recrear la BD desde cero.

    Args:
        engine: engine SQLAlchemy destino.
        force: si `True`, elimina el esquema existente antes de recrearlo.
    """
    if not force and is_initialized(engine):
        _apply_additive_migrations(engine)
        _create_missing_tables(engine)
        _sync_court_zones(engine)
        _refresh_views(engine)
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
    _apply_additive_migrations(engine)  # no-op justo después de crear desde schema.sql; misma ruta siempre
    _create_missing_tables(engine)      # ídem
    _sync_court_zones(engine)           # ídem
    _refresh_views(engine)
