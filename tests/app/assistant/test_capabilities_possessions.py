"""Capacidad `stint_possessions`: columna presente Y con dato (ver `capabilities.py`)."""
from sqlalchemy import text

from app.assistant.capabilities import probe
from tests.app.assistant.conftest import add_stints

_FIVE = ["howard", "moneke", "codi", "kotsar", "lutse"]


def test_stint_possessions_needs_the_column_with_real_data(engine):
    """Tramos con la columna en NULL (partido sin tiros tipados) no encienden
    nada; basta un tramo con posesiones para encenderla."""
    add_stints(engine, [("g5", "bas", 0.0, 600.0, 10, 8, 0, _FIVE)])
    caps = probe(engine)
    assert caps.lineup_stints is True
    assert caps.stint_possessions is False
    assert any("posesiones por tramo" in gap for gap in caps.missing_summary())
    assert caps.to_dict()["stint_possessions"] is False

    with engine.begin() as conn:
        conn.execute(text("UPDATE lineup_stints SET possessions_for = 11.2, possessions_against = 10.9"))
    caps = probe(engine)
    assert caps.stint_possessions is True
    assert not any("posesiones por tramo" in gap for gap in caps.missing_summary())


def test_no_possessions_gap_without_stints(engine):
    """Sin tramos, el aviso de `lineup_stints` ya lo dice: no se repite."""
    caps = probe(engine)
    assert caps.stint_possessions is False
    assert not any("posesiones por tramo" in gap for gap in caps.missing_summary())


def test_stint_possessions_is_off_on_a_db_without_the_column(engine):
    """BD anterior a la migración: la columna no existe y el sondeo no revienta."""
    add_stints(engine, [("g5", "bas", 0.0, 600.0, 10, 8, 0, _FIVE)])
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE lineup_stints DROP COLUMN possessions_for"))
    assert probe(engine).stint_possessions is False
