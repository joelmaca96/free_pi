"""Net rating por 100 posesiones en on/off y combinaciones (`queries_assistant`).

Mismos tramos que las pruebas del On/Off por 40 del asistente (titulares 100
minutos a +20, banquillo 200 minutos a −20), ahora con posesiones por tramo.
Lo que se fija: la fórmula (ORtg − DRtg sobre las sumas de los MISMOS tramos),
que los tramos sin posesiones no se mezclan con los que sí, y NaN —no 0—
cuando la base de datos no tiene ninguna.
"""
import math

import pytest
from sqlalchemy import text

from app.data import queries_assistant
from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db
from tests.app.assistant.conftest import add_stints

_STARTERS = ["howard", "moneke", "codi", "kotsar", "sedekerskis"]
_BENCH = ["howard", "moneke", "nikos", "costello", "lutse"]


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    add_stints(
        eng,
        [
            ("g5", "bas", 0.0, 6000.0, 60, 40, 0, _STARTERS),
            ("g4", "bas", 0.0, 12000.0, 80, 100, 0, _BENCH),
        ],
    )
    try:
        yield eng
    finally:
        eng.dispose()


def _set_possessions(engine, game_id, poss_for, poss_against):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE lineup_stints SET possessions_for = :f, possessions_against = :a WHERE game_id = :g"),
            {"f": poss_for, "a": poss_against, "g": game_id},
        )


def test_on_off_per_100_is_nan_without_possessions(engine):
    onoff = queries_assistant.player_on_off(engine, "bas", 1).set_index("player_id")
    # El On/Off por 40 sigue igual que siempre.
    assert onoff.loc["sedekerskis", "on_off"] == pytest.approx(12.0)
    assert math.isnan(onoff.loc["sedekerskis", "on_net_100"])
    assert math.isnan(onoff.loc["sedekerskis", "on_off_100"])


def test_on_off_per_100_uses_ortg_minus_drtg(engine):
    _set_possessions(engine, "g5", 50.0, 40.0)    # titulares: 120 − 100 = +20 por 100
    _set_possessions(engine, "g4", 100.0, 125.0)  # banquillo: 80 − 80 = 0 por 100
    onoff = queries_assistant.player_on_off(engine, "bas", 1).set_index("player_id")

    sede = onoff.loc["sedekerskis"]
    assert sede["on_possessions"] == pytest.approx(45.0)
    assert sede["on_net_100"] == pytest.approx(20.0)
    assert sede["off_net_100"] == pytest.approx(0.0)
    assert sede["on_off_100"] == pytest.approx(20.0)
    # Howard jugó los dos tramos: con él, sumas conjuntas (140/150 − 140/165).
    howard = onoff.loc["howard"]
    assert howard["on_net_100"] == pytest.approx(100 * 140 / 150 - 100 * 140 / 165)
    assert math.isnan(howard["off_net_100"])


def test_stints_without_possessions_are_left_out_of_the_per_100(engine):
    """Solo g5 tiene posesiones: el per-100 de Howard sale SOLO de g5, sin dividir
    los puntos de g4 entre posesiones que no existen."""
    _set_possessions(engine, "g5", 50.0, 40.0)
    onoff = queries_assistant.player_on_off(engine, "bas", 1).set_index("player_id")
    assert onoff.loc["howard", "on_net_100"] == pytest.approx(20.0)
    assert onoff.loc["howard", "on_possessions"] == pytest.approx(45.0)


def test_combos_carry_net_rating_per_100(engine):
    _set_possessions(engine, "g5", 50.0, 40.0)
    _set_possessions(engine, "g4", 100.0, 125.0)
    pairs = queries_assistant.player_combos(engine, "bas", 1, size=2).set_index("player_ids")

    assert pairs.loc["codi,kotsar", "net_rating_100"] == pytest.approx(20.0)
    assert pairs.loc["costello,lutse", "net_rating_100"] == pytest.approx(0.0)
    assert pairs.loc["howard,moneke", "possessions"] == pytest.approx((150 + 165) / 2)


def test_combos_per_100_is_nan_without_possessions(engine):
    pairs = queries_assistant.player_combos(engine, "bas", 1, size=2)
    assert "net_rating_100" in pairs.columns
    assert pairs["net_rating_100"].isna().all()


def test_on_off_works_on_a_db_without_the_possession_columns(engine):
    """BD sin migrar: la consulta no nombra columnas que no existen (basta con que
    falte `possessions_for`, que es la que se mira)."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE lineup_stints DROP COLUMN possessions_for"))
    onoff = queries_assistant.player_on_off(engine, "bas", 1)
    assert not onoff.empty
    assert onoff["on_net_100"].isna().all()
