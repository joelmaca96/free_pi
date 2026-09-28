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


def test_shot_clock_needs_shots_with_a_real_clock(engine):
    """Columnas añadidas por la migración (2026-09-28) pero en NULL no bastan:
    hace falta al menos un tiro reingerido con reloj."""
    caps = probe(engine)
    assert caps.shot_clock is False
    assert any("Los tiros no tienen reloj" in gap for gap in caps.missing_summary())
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO shots (game_id, player_id, pos_x, pos_y, made) VALUES ('g5', 'howard', 250, 400, 1)"
        ))
    assert probe(engine).shot_clock is False  # tiro viejo, sin reloj
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO shots (game_id, player_id, pos_x, pos_y, made, quarter, game_clock, seconds)"
            " VALUES ('g5', 'howard', 250, 400, 1, 'Q4', '01:00', 2340)"
        ))
    caps = probe(engine)
    assert caps.shot_clock is True
    assert caps.to_dict()["shot_clock"] is True


def test_timeouts_need_a_timeout_row_not_just_play_events(engine):
    """Un play-by-play tipado cargado antes del 2026-09-28 (sin tiempos muertos)
    enciende `play_events` pero no `timeouts`."""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
            " event_type, home_score, away_score)"
            " VALUES ('g5', 'bas', 'howard', 'Q1', '08:00', 120, 'steal', 2, 0)"
        ))
    caps = probe(engine)
    assert caps.play_events is True and caps.timeouts is False
    assert any("tiempos muertos" in gap for gap in caps.missing_summary())
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
            " event_type, home_score, away_score)"
            " VALUES ('g5', 'bas', NULL, 'Q1', '07:00', 180, 'timeout', 4, 0)"
        ))
    assert probe(engine).timeouts is True
