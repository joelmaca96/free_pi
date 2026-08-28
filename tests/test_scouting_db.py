"""Tests de la base de datos de scouting: carga del esquema.

Cada test usa una BD SQLite en memoria propia, inicializada ejecutando
`schema.sql` (DDL + datos semilla reales), para no depender de ni tocar
`data/baskonia.db`.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import (
    create_scouting_engine,
    init_scouting_db,
    is_initialized,
)


@pytest.fixture()
def engine():
    """Engine SQLite en memoria con el esquema de scouting ya cargado."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ---------- Carga de esquema ----------


def test_schema_loads_without_errors(engine):
    assert is_initialized(engine)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        views = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='view'")
            )
        }
    expected_tables = {
        "seasons", "competitions", "teams", "team_external_ids", "court_zones",
        "players", "player_external_ids", "games", "game_advanced_stats",
        "game_team_quarter_stats", "player_game_stats", "lineups", "lineup_players",
        "game_zone_stats", "shots", "key_events", "score_progression", "upcoming_matchups",
    }
    expected_views = {
        "player_stats_by_competition", "player_stats_combined",
        "team_stats_by_competition", "team_stats_combined",
    }
    assert expected_tables <= tables
    assert expected_views <= views


def test_init_is_noop_without_force(engine):
    """Llamar dos veces sin --force no falla (no reinserta el seed)."""
    init_scouting_db(engine)  # ya inicializado por el fixture; no debe lanzar
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM players")).scalar_one()
    assert count == 8


def test_init_force_recreates_schema(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM key_events WHERE game_id = 'g1'"))
    init_scouting_db(engine, force=True)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM key_events")).scalar_one()
    assert count == 20  # el seed se recarga entero


# ---------- Migraciones aditivas (columnas nuevas sobre una BD ya existente) ----------


def test_init_backfills_columns_added_after_a_db_was_created():
    """Simula una `data/baskonia.db` real creada ANTES de que `schema.sql` ganara
    `teams.logo_url`/`players.photo_local_path` (sin `_apply_additive_migrations`,
    esto es justo el `sqlite3.OperationalError: no such column` que se ve en
    producción). `init_scouting_db()` SIN `--force` debe añadir esas columnas con
    `ALTER TABLE`, sin recrear nada ni perder ninguna fila ya insertada."""
    eng = create_scouting_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        # Un subconjunto mínimo del esquema "antiguo" (sin las columnas nuevas) -
        # basta con `seasons` (la que usa `is_initialized`) + `teams`/`players`
        # con datos ya ingeridos de verdad, para comprobar que sobreviven intactos.
        conn.execute(text("CREATE TABLE seasons (id INTEGER PRIMARY KEY, label TEXT)"))
        conn.execute(text("CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT, is_own_team INTEGER)"))
        conn.execute(
            text(
                "CREATE TABLE players (id TEXT PRIMARY KEY, team_id TEXT, name TEXT,"
                " number INTEGER, position TEXT, active INTEGER)"
            )
        )
        conn.execute(text("INSERT INTO teams (id, name, is_own_team) VALUES ('bas', 'Baskonia', 1)"))
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position, active)"
                " VALUES ('howard', 'bas', 'Markus Howard', 0, 'Escolta', 1)"
            )
        )

    assert is_initialized(eng)  # `seasons` ya existe -> NO dispara el recreate completo desde schema.sql

    init_scouting_db(eng)  # sin --force: esta es la ruta que corre `ingest/`/`tools/init_scouting_db.py`

    with eng.connect() as conn:
        team_row = conn.execute(text("SELECT name, logo_url FROM teams WHERE id = 'bas'")).first()
        player_row = conn.execute(text("SELECT name, photo_local_path FROM players WHERE id = 'howard'")).first()
    assert team_row.name == "Baskonia"  # el dato ya ingerido sigue ahí
    assert team_row.logo_url is None  # columna nueva, backfilleada a NULL (no había valor que migrar)
    assert player_row.name == "Markus Howard"
    assert player_row.photo_local_path is None
    eng.dispose()


def test_init_additive_migration_is_idempotent(engine):
    """Una segunda llamada (p.ej. dos ejecuciones de ingesta seguidas) no debe
    relanzar `ALTER TABLE ADD COLUMN` sobre una columna que ya existe."""
    init_scouting_db(engine)  # la fixture ya inicializó una vez; esta es la segunda
    with engine.connect() as conn:
        columns = {col[1] for col in conn.execute(text("PRAGMA table_info(players)"))}
    assert "photo_local_path" in columns


def test_init_syncs_court_zone_geometry_on_an_existing_db():
    """Reteselado de `court_zones` (2026-08-27): una BD ya inicializada con la geometría
    VIEJA (6 zonas, ver historia en `schema.sql`) debe ganar las zonas nuevas y las
    reajustadas al llamar `init_scouting_db()` SIN `--force`, sin perder ningún tiro
    ya cargado que apunte a un `zone_id` existente (`_sync_court_zones` nunca borra
    filas, solo añade/actualiza)."""
    eng = create_scouting_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE seasons (id INTEGER PRIMARY KEY, label TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE court_zones (id INTEGER PRIMARY KEY, label TEXT UNIQUE NOT NULL,"
                " x_min REAL NOT NULL, x_max REAL NOT NULL, y_min REAL NOT NULL, y_max REAL NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO court_zones (id, label, x_min, x_max, y_min, y_max) VALUES"
                " (1, 'Pintura', 195, 305, 300, 455),"
                " (2, 'Media dist. izq.', 60, 185, 210, 330)"
            )
        )
        conn.execute(text("CREATE TABLE shots (id INTEGER PRIMARY KEY, zone_id INTEGER REFERENCES court_zones(id))"))
        conn.execute(text("INSERT INTO shots (id, zone_id) VALUES (1, 2)"))

    assert is_initialized(eng)  # `seasons` ya existe -> ruta sin recrear desde cero

    init_scouting_db(eng)  # sin --force

    with eng.connect() as conn:
        zone_2 = conn.execute(text("SELECT label, x_min, x_max FROM court_zones WHERE id = 2")).first()
        zone_10 = conn.execute(text("SELECT label FROM court_zones WHERE id = 10")).first()
        # El tiro ya cargado, que apuntaba al id 2, sigue existiendo — el id no
        # se borra ni se reasigna, solo cambian sus límites/etiqueta.
        shot_zone = conn.execute(text("SELECT zone_id FROM shots WHERE id = 1")).scalar_one()
    assert zone_2 == ("Ala izq.", 0, 195)  # reajustada, no borrada
    assert zone_10 == ("Mate",)              # zona nueva, presente
    assert shot_zone == 2
    eng.dispose()


def test_init_court_zone_sync_is_idempotent(engine):
    """Una segunda llamada no falla ni duplica filas (upsert por `id`, no `INSERT` a secas)."""
    init_scouting_db(engine)  # la fixture ya inicializó una vez; esta es la segunda
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM court_zones")).scalar_one()
        pintura = conn.execute(text("SELECT x_min, x_max, y_min, y_max FROM court_zones WHERE id = 1")).first()
    # 13 zonas del reteselado + 4 sub-zonas degeneradas del split de ala por
    # triple ("Ala izq./der. (2)/(3)", filas 14-17 — ver el comentario sobre
    # `court_zones` en `schema.sql`).
    assert count == 17
    assert pintura == (195, 305, 300, 455)


def test_init_creates_tables_added_after_a_db_was_created():
    """Igual que el backfill de columnas, pero a nivel de TABLA.

    Una base de datos ya inicializada nunca vuelve a ejecutar `schema.sql`
    entero (`is_initialized` corta antes), así que sin
    `_create_missing_tables` una tabla nueva no aparecería jamás salvo
    recreando la BD desde cero y perdiendo todo lo ingerido. `lineup_stints`
    es el primer caso real: la trajo el asistente de scouting
    (`local/features/005-chatbot/01_design.md` §10.2) sobre bases de datos
    con dos temporadas ya cargadas.
    """
    eng = create_scouting_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE seasons (id INTEGER PRIMARY KEY, label TEXT)"))
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (1, '2025-2026')"))

    init_scouting_db(eng)  # sin --force: la ruta que corre la ingesta

    with eng.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"lineup_stints", "lineup_stint_players"} <= tables
        # El índice que acompaña a la tabla viaja con ella.
        indexes = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))}
        assert "idx_stints_game_team" in indexes
        # Y el dato que ya estaba sigue intacto.
        assert conn.execute(text("SELECT label FROM seasons WHERE id = 1")).scalar_one() == "2025-2026"
    eng.dispose()


def test_init_creating_missing_tables_is_idempotent(engine):
    """Una tabla que ya existe no se recrea: a diferencia de una vista, guarda datos."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lineup_stints"
                " (game_id, team_id, start_seconds, end_seconds, points_for, points_against, margin_start)"
                " VALUES ('g1', 'bas', 0, 600, 10, 8, 0)"
            )
        )

    init_scouting_db(engine)  # segunda pasada

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM lineup_stints")).scalar_one() == 1


def test_init_refreshes_the_new_assistant_views(engine):
    """Las vistas se recrean enteras en cada init (SQLite congela su definición
    al crearlas), así que las del asistente aparecen en una BD ya existente."""
    with engine.connect() as conn:
        views = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='view'"))}
    assert {"team_style_percentiles", "player_percentiles", "lineup_team"} <= views


def test_lineup_team_view_infers_the_team_and_says_that_it_did(engine):
    """El equipo del quinteto se prefiere explícito y, si falta, se deduce por
    mayoría — marcándolo con `is_inferred` para que nadie lo presente como
    dato firme (§2.3)."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT team_id, is_inferred FROM lineup_team ORDER BY lineup_id LIMIT 1")).first()
    assert rows == ("bas", 1)  # el seed no puebla `lineups.team_id`

    with engine.begin() as conn:
        conn.execute(text("UPDATE lineups SET team_id = 'rm' WHERE id = 1"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT team_id, is_inferred FROM lineup_team WHERE lineup_id = 1")).first() == ("rm", 0)
