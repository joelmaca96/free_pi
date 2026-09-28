"""Tests de `tools/backfill_stint_possessions.py`: recalcular desde lo ya guardado, sin red.

Dos partidos del seed con tramos: `g5` con tiros tipados en `play_events`
(como quedará tras reingerir con la ingesta de tiros) y `g4` sin ellos (como
cualquier partido ingerido antes). El primero gana posesiones, el segundo se
queda en NULL — y el dry-run no escribe nada en ninguno.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db
from tools import backfill_stint_possessions


@pytest.fixture()
def engine(tmp_path):
    eng = create_scouting_engine(f"sqlite:///{(tmp_path / 'scouting.db').as_posix()}")
    init_scouting_db(eng)
    with eng.begin() as conn:
        for game_id, team_id in (("g5", "bas"), ("g5", "val"), ("g4", "bas")):
            conn.execute(
                text(
                    "INSERT INTO lineup_stints"
                    " (game_id, team_id, start_seconds, end_seconds, points_for, points_against, margin_start)"
                    " VALUES (:g, :t, 0, 600, 0, 0, 0)"
                ),
                {"g": game_id, "t": team_id},
            )
        for game_id, team_id, seconds, event_type in (
            ("g5", "bas", 10, "fg2_made"),
            ("g5", "bas", 20, "fg3_missed"),
            ("g5", "val", 30, "turnover"),
            ("g4", "bas", 30, "turnover"),  # g4: play-by-play sin tiros tipados
        ):
            conn.execute(
                text(
                    "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                    " event_type, home_score, away_score)"
                    " VALUES (:g, :t, NULL, 'Q1', '05:00', :s, :e, 0, 0)"
                ),
                {"g": game_id, "t": team_id, "s": seconds, "e": event_type},
            )
    try:
        yield eng
    finally:
        eng.dispose()


def _possessions(engine):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT game_id, team_id, possessions_for, possessions_against FROM lineup_stints ORDER BY id")
        ).all()


def test_dry_run_reports_without_writing(engine):
    with engine.begin() as conn:
        summary = backfill_stint_possessions.backfill(conn, apply=False)

    assert summary["games"] == 2
    assert summary["with_shots"] == 1
    assert summary["without_shots"] == 1
    assert summary["applied"] is False
    assert all(row[2] is None and row[3] is None for row in _possessions(engine))


def test_apply_writes_possessions_and_null_for_games_without_shots(engine):
    with engine.begin() as conn:
        summary = backfill_stint_possessions.backfill(conn, apply=True)

    assert summary["stints_updated"] == 2
    assert _possessions(engine) == [
        ("g5", "bas", 2.0, 1.0),
        ("g5", "val", 1.0, 2.0),
        ("g4", "bas", None, None),
    ]
    # Idempotente: una segunda pasada deja lo mismo.
    with engine.begin() as conn:
        backfill_stint_possessions.backfill(conn, apply=True)
    assert _possessions(engine)[0] == ("g5", "bas", 2.0, 1.0)


def test_main_parses_flags_and_prints_the_summary(engine, capsys):
    url = str(engine.url)
    summary = backfill_stint_possessions.main(["--database-url", url, "--game", "g5"])
    out = capsys.readouterr().out

    assert summary["games"] == 1 and summary["applied"] is False
    assert "Dry-run" in out
    assert all(row[2] is None for row in _possessions(engine))

    summary = backfill_stint_possessions.main(["--database-url", url, "--apply", "--no-rescale", "--ft-mode", "trips"])
    assert summary["applied"] is True
    assert _possessions(engine)[0] == ("g5", "bas", 2.0, 1.0)


def test_reports_the_discrepancy_against_the_reference(engine):
    with engine.begin() as conn:
        # bas acaba g5 con 84 puntos: ortg 4200 -> 2 posesiones de referencia, igual que las estimadas.
        conn.execute(text("UPDATE game_advanced_stats SET ortg = 4200 WHERE game_id = 'g5' AND team_id = 'bas'"))
        summary = backfill_stint_possessions.backfill(conn, apply=False)
    assert summary["with_reference"] == 1
    assert summary["mean_abs_discrepancy_pct"] == 0.0
    assert summary["over_threshold"] == 0
