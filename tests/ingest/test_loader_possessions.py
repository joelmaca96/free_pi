"""El loader rellena `lineup_stints.possessions_*` tras cargar eventos y tramos.

Aparte de `test_loader.py` a propósito: los eventos de cada caso se fijan aquí
(`dataclasses.replace`), así que el partido de muestra de ese fichero puede
ganar tiros tipados sin que cambie lo que se prueba en este.
"""
import dataclasses

import pytest
from sqlalchemy import text

from ingest.common import loader
from ingest.common.loader import load_game
from ingest.common.schema_types import PlayEvent, StintRecord
from tests.ingest.test_loader import _sample_game

_FIVE = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]


def _event(team, seconds, event_type, clock="05:00"):
    return PlayEvent(team_id=team, player_id=None, quarter="Q4", game_clock=clock, seconds=seconds,
                     event_type=event_type, home_score=0, away_score=0)


def _game(events):
    return dataclasses.replace(
        _sample_game(),
        play_events=events,
        stints=[
            StintRecord(team_id="bas", player_ids=_FIVE, start_seconds=2100.0, end_seconds=2250.0,
                        points_for=4, points_against=2, margin_start=0),
            StintRecord(team_id="bas", player_ids=_FIVE[:4] + ["costello"], start_seconds=2250.0,
                        end_seconds=2400.0, points_for=6, points_against=4, margin_start=2),
        ],
    )


def _possessions(engine):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT possessions_for, possessions_against FROM lineup_stints"
                 " WHERE game_id = 'acb-99001' ORDER BY start_seconds")
        ).all()


def test_loader_fills_stint_possessions_from_typed_shots(engine):
    events = [
        _event("bas", 2200.0, "fg2_made"),
        _event("bas", 2240.0, "fg3_missed"),
        _event("bas", 2245.0, "oreb"),
        _event("bas", 2250.0, "turnover"),   # segundo del cambio: va al tramo que entra
        _event("rm", 2300.0, "ft_made"),
        _event("rm", 2300.0, "ft_made"),
        _event("rm", 2350.0, "fg3_made"),
        _event("rm", 2400.0, "fg2_missed", clock="00:00"),  # bocina final: último tramo
    ]
    with engine.begin() as conn:
        load_game(conn, _game(events))

    # La referencia de `bas` (90 pts / ortg 112 ≈ 80 posesiones) está a un
    # factor absurdo de un partido con ocho eventos: no se reescala.
    first, second = _possessions(engine)
    assert first == (1.0, 0.0)
    assert second == (1.0, pytest.approx(0.88 + 1 + 1))


def test_loader_leaves_null_without_typed_shots(engine):
    """Partido con play-by-play tipado pero de antes de los tiros: NULL, no 0."""
    events = [_event("bas", 2200.0, "turnover"), _event("rm", 2210.0, "steal")]
    with engine.begin() as conn:
        load_game(conn, _game(events))
    assert _possessions(engine) == [(None, None), (None, None)]


def test_loader_possessions_are_idempotent_on_rerun(engine):
    events = [_event("bas", 2200.0, "fg2_made"), _event("rm", 2300.0, "fg2_missed")]
    game = _game(events)
    with engine.begin() as conn:
        load_game(conn, game)
    first = _possessions(engine)
    with engine.begin() as conn:
        load_game(conn, game)
    assert _possessions(engine) == first == [(1.0, 0.0), (0.0, 1.0)]


def test_loader_survives_a_failure_in_the_estimate(engine, monkeypatch, caplog):
    """Dato derivado: si el cálculo falla, el partido se carga igual y quedan en NULL."""
    def boom(conn, game_id):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(loader, "update_stint_possessions", boom)
    with engine.begin() as conn:
        load_game(conn, _game([_event("bas", 2200.0, "fg2_made")]))
    assert _possessions(engine) == [(None, None), (None, None)]
    assert "no calculadas" in caplog.text
