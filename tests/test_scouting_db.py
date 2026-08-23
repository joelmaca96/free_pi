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
