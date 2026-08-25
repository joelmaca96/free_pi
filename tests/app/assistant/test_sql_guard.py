"""Tests de las guardas de `run_sql` (§12.3).

Son la única superficie del sistema donde el modelo escribe SQL, así que la
batería es de ataque, no de humo: inyección, DDL disfrazado en comentarios,
`ATTACH`, tablas fuera de la lista blanca, ausencia de `LIMIT` y consulta
lenta. **Ningún caso de esta batería debe pasar** (criterio de aceptación de
la fase 2).
"""
import pytest

from app.assistant import sql_guard


def test_a_plain_select_passes_and_gets_a_limit(engine):
    guarded = sql_guard.validate("SELECT id FROM games", engine)
    assert guarded.sql.endswith(f"LIMIT {sql_guard.MAX_ROWS}")
    assert guarded.limit_added is True
    assert guarded.sources == ["games"]


def test_an_existing_limit_is_respected(engine):
    guarded = sql_guard.validate("SELECT id FROM games LIMIT 3", engine)
    assert guarded.limit_added is False
    assert guarded.sql.endswith("LIMIT 3")


def test_a_cte_is_allowed_and_its_alias_is_not_a_table(engine):
    guarded = sql_guard.validate(
        "WITH recientes AS (SELECT id FROM games) SELECT * FROM recientes", engine
    )
    assert guarded.sources == ["games"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE games",           # inyección clásica
        "DROP TABLE games",                     # DDL directo
        "DELETE FROM games",                    # DML
        "UPDATE games SET pace = 0",
        "INSERT INTO games VALUES (1)",
        "ATTACH DATABASE 'otra.db' AS otra",    # sortearía el mode=ro
        "PRAGMA table_info(games)",
        "SELECT load_extension('x')",
        "SELECT 1 -- \nDROP TABLE games",       # DDL escondido en un comentario
        "SELECT * FROM /* comentario */ sqlite_master",  # tabla fuera de la lista blanca
        "SELECT * FROM pg_catalog",             # tabla que no existe aquí
        "",                                     # vacía
    ],
)
def test_the_attack_battery_never_gets_through(sql, engine):
    with pytest.raises(sql_guard.SQLGuardError):
        sql_guard.validate(sql, engine)


def test_the_rejection_message_says_which_guard_fired(engine):
    with pytest.raises(sql_guard.SQLGuardError, match="no existen en esta base de datos"):
        sql_guard.validate("SELECT * FROM sqlite_master", engine)
    with pytest.raises(sql_guard.SQLGuardError, match="UNA sentencia"):
        sql_guard.validate("SELECT 1; SELECT 2", engine)
    with pytest.raises(sql_guard.SQLGuardError, match="empezar por SELECT o WITH"):
        sql_guard.validate("EXPLAIN SELECT 1", engine)


def test_views_are_queryable_too(engine):
    """Las vistas son parte del contrato de datos, no un detalle interno."""
    guarded = sql_guard.validate("SELECT * FROM team_stats_combined", engine)
    assert guarded.sources == ["team_stats_combined"]


def test_run_returns_rows_and_column_names(engine):
    result = sql_guard.run("SELECT id, home_score FROM games ORDER BY id", engine)
    assert result["columns"] == ["id", "home_score"]
    assert result["row_count"] > 0
    assert result["limit_added"] is True


def test_run_reports_a_sqlite_error_as_a_guard_error(engine):
    """Sintaxis o columna inexistente llegan al modelo con la misma forma que
    un rechazo: puede corregir en los dos casos."""
    with pytest.raises(sql_guard.SQLGuardError, match="SQLite rechazó"):
        sql_guard.run("SELECT columna_que_no_existe FROM games", engine)


def test_a_slow_query_is_aborted_by_the_progress_handler(engine, monkeypatch):
    """El manejador de progreso es la única forma de cortar un barrido en
    SQLite: no hay `statement_timeout`."""
    monkeypatch.setattr(sql_guard, "TIMEOUT_SECONDS", 0.0)
    # Producto cartesiano sobre `games`: suficientes instrucciones para que el
    # manejador se dispare aunque la tabla sea pequeña.
    with pytest.raises(sql_guard.SQLGuardError, match="abortada por tardar"):
        sql_guard.run("SELECT COUNT(*) FROM games a, games b, games c, games d", engine)


def test_the_connection_is_left_writable_after_a_query(engine):
    """La guarda de solo lectura no puede dejar el motor inutilizable detrás."""
    from sqlalchemy import text

    sql_guard.run("SELECT COUNT(*) FROM games", engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (99, '2099-2100')"))


def test_the_in_memory_fallback_also_works_and_restores_the_connection():
    """Camino secundario de `_open_readonly` (ver su docstring).

    Con `:memory:` no hay fichero que abrir en `mode=ro`, así que se reutiliza
    la conexión del engine con `PRAGMA query_only`. Hay que comprobar las dos
    mitades: que la consulta va, y que la conexión queda escribible detrás —
    si no, el siguiente uso del engine fallaría por culpa de este.
    """
    from sqlalchemy import text

    from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

    memory_engine = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(memory_engine)
    try:
        result = sql_guard.run("SELECT COUNT(*) AS n FROM games", memory_engine)
        assert result["rows"][0][0] > 0
        with memory_engine.begin() as conn:
            conn.execute(text("INSERT INTO seasons (id, label) VALUES (98, '2098-2099')"))
    finally:
        memory_engine.dispose()
