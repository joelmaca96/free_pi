"""Tests del sondeo de capacidades (Fase 0-4, ver `app/assistant/capabilities.py`).

Mismo criterio que el resto del módulo: columna/tabla PRESENTE Y CON DATO
real, no solo `ALTER TABLE`/`CREATE TABLE` ya aplicado — una BD recién
migrada pero sin reingerir debe seguir sin prometer el bloque nuevo.
"""
from sqlalchemy import text

from app.assistant.capabilities import probe


def test_new_capabilities_are_off_by_default(engine):
    """El fixture `engine` (schema + seed) no tiene ninguno de los bloques
    nuevos cargado todavía: las cinco capacidades deben venir apagadas."""
    caps = probe(engine)
    assert caps.box_extras is False
    assert caps.play_events is False
    assert caps.game_metadata is False
    assert caps.quarter_player_stats is False
    assert caps.player_advanced_stats is False
    gaps = caps.missing_summary()
    assert any("boxscore ampliado" in gap for gap in gaps)
    assert any("play-by-play tipado" in gap for gap in gaps)
    assert any("árbitros" in gap for gap in gaps)
    assert any("jugador por cuarto" in gap for gap in gaps)
    assert any("avanzadas oficiales por jugador" in gap for gap in gaps)


def test_box_extras_turns_on_only_with_real_data(engine):
    """Columna presente pero toda en NULL no basta (mismo criterio que `free_throws`)."""
    assert probe(engine).box_extras is False
    with engine.begin() as conn:
        conn.execute(text("UPDATE player_game_stats SET stl = 2 WHERE game_id = 'g5' AND player_id = 'howard'"))
    assert probe(engine).box_extras is True


def test_play_events_turns_on_with_at_least_one_row(engine):
    assert probe(engine).play_events is False
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                " event_type, home_score, away_score)"
                " VALUES ('g5', 'bas', 'howard', 'Q1', '08:00', 2320, 'steal', 2, 0)"
            )
        )
    assert probe(engine).play_events is True


def test_game_metadata_turns_on_with_a_real_arena(engine):
    assert probe(engine).game_metadata is False
    with engine.begin() as conn:
        conn.execute(text("UPDATE games SET arena = 'Fernando Buesa Arena' WHERE id = 'g5'"))
    assert probe(engine).game_metadata is True


def test_quarter_player_stats_turns_on_with_a_real_row(engine):
    assert probe(engine).quarter_player_stats is False
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO player_game_quarter_stats (game_id, player_id, quarter, pts)"
                " VALUES ('g5', 'howard', 1, 6)"
            )
        )
    assert probe(engine).quarter_player_stats is True


def test_player_advanced_stats_turns_on_with_a_real_row(engine):
    assert probe(engine).player_advanced_stats is False
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO player_advanced_stats (game_id, player_id, ts_pct) VALUES ('g5', 'howard', 61.2)"))
    assert probe(engine).player_advanced_stats is True


def test_competitions_carry_their_id(engine):
    """Igual que `seasons`, y por un motivo medido: hasta el 2026-09-14
    `competitions` eran solo nombres, y como media docena de herramientas
    piden `competition_id` como entero, el modelo se escapaba por `run_sql`
    solo para preguntar el id. Los dos únicos escapes a SQL libre de la
    primera pasada del set dorado eran exactamente eso."""
    competitions = probe(engine).competitions

    assert competitions, "el seed carga al menos una competición"
    for competition in competitions:
        assert isinstance(competition["id"], int)
        assert competition["name"]
