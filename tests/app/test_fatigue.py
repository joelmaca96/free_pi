"""Tests de las consultas de fatiga y calendario (`app/data/queries.py`).

La propuesta que implementan (`doc/features/propuestas/04_fatiga_y_calendario.md`)
se apoya en un cálculo de descanso que es fácil de escribir mal en silencio:
"días desde el partido anterior EN CUALQUIER COMPETICIÓN", no desde el
anterior de la misma. El seed de seasons.sql ya tiene justo eso —g1 (Euroliga,
29-dic), g2 (ACB, 5-ene), g3 (Euroliga, 10-ene), g4 (ACB, 15-ene), g5 (ACB,
18-ene)—, así que estos tests pueden afirmar el número exacto de días sin
tener que sembrar datos nuevos.
"""
import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data import queries


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """Mismo motivo que en `tests/app/test_rotations.py`: las consultas van
    cacheadas por argumentos y el engine viaja sin hashear (`_engine`)."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture()
def engine():
    """BD en memoria con el esquema + seed reales (5 partidos del Baskonia)."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ---------- rest_days ----------


def test_rest_days_is_none_for_the_first_game_in_the_database(engine):
    """g1 (29-dic-2025) es el primer partido del Baskonia en todo el seed: sin
    "anterior" del que restar, el descanso es `None`, no 0 ni un número inventado."""
    df = queries.rest_days(engine, "bas", 1)
    row = df[df["game_id"] == "g1"].iloc[0]
    assert pd.isna(row["rest_days"])


def test_rest_days_counts_across_competitions(engine):
    """g3 es de Euroliga y su partido anterior (g2) es de ACB: el descanso se
    calcula igual, cruzando competiciones — es el matiz que motiva la función."""
    df = queries.rest_days(engine, "bas", 1)
    by_game = df.set_index("game_id")["rest_days"]
    assert by_game["g2"] == 7  # 29-dic -> 5-ene
    assert by_game["g3"] == 5  # 5-ene -> 10-ene (Euroliga tras ACB)
    assert by_game["g4"] == 5  # 10-ene -> 15-ene
    assert by_game["g5"] == 3  # 15-ene -> 18-ene


def test_rest_days_is_chronological_and_carries_rival_and_competition(engine):
    df = queries.rest_days(engine, "bas", 1)
    assert list(df["game_id"]) == ["g1", "g2", "g3", "g4", "g5"]
    g3 = df[df["game_id"] == "g3"].iloc[0]
    assert g3["competition"] == "Euroliga"
    assert g3["rival"] == "FC Barcelona"
    assert g3["condicion"] == "Local"


def test_rest_days_empty_for_a_team_without_games_in_the_season(engine):
    df = queries.rest_days(engine, "bas", 999)
    assert df.empty


# ---------- rolling_load ----------


def test_rolling_load_window_excludes_the_game_exactly_n_days_before(engine):
    """g2 (5-ene) queda EXACTAMENTE 7 días después de g1 (29-dic): una ventana
    de 7 días terminando en g2 debe excluir a g1 (ventana semiabierta), así
    que `rolling_minutes` de g2 es solo lo jugado en g2."""
    df = queries.rolling_load(engine, "bas", 1, 7)
    howard = df[df["player_name"] == "Marcus Howard"].set_index("game_date")
    g2_minutes = howard.loc["2026-01-05", "minutes"]
    assert howard.loc["2026-01-05", "rolling_minutes"] == pytest.approx(g2_minutes)
    assert howard.loc["2026-01-05", "games_in_window"] == 1


def test_rolling_load_sums_games_that_fall_inside_the_window(engine):
    """g4 (15-ene) sí tiene a g3 (10-ene, 5 días antes) dentro de la ventana
    de 7 días: la suma de la ventana debe incluir a los dos partidos."""
    df = queries.rolling_load(engine, "bas", 1, 7)
    howard = df[df["player_name"] == "Marcus Howard"].set_index("game_date")
    expected = howard.loc["2026-01-10", "minutes"] + howard.loc["2026-01-15", "minutes"]
    assert howard.loc["2026-01-15", "rolling_minutes"] == pytest.approx(expected)
    assert howard.loc["2026-01-15", "games_in_window"] == 2


def test_rolling_load_counts_a_dnp_as_zero_minutes_not_as_a_gap(engine):
    """Un jugador sin fila en `player_game_stats` para un partido de la
    ventana (no convocado/lesión) debe sumar 0, no dejar la ventana vacía ni
    reventar con un NaN propagado. El seed trae los 8 jugadores completos en
    los 5 partidos, así que esta ausencia se simula quitando la fila de
    Lutse en "g5" (14.1.15-18, dentro de la ventana de 14 días de g4/g5)."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM player_game_stats WHERE game_id = 'g5' AND player_id = 'lutse'"))

    df = queries.rolling_load(engine, "bas", 1, 14)
    lutse = df[df["player_name"] == "Tadas Lutse"].set_index("game_date")
    assert not lutse["rolling_minutes"].isna().any()
    assert pd.isna(lutse.loc["2026-01-18", "minutes"])  # sin fila = no jugó ese partido
    # Ventana de 14 días en g5 (18-ene) llega hasta el 05-ene (exclusive el
    # 04-ene): entran g2 + g3 + g4 (jugados) y g5 suma 0 por la fila borrada.
    expected = (
        lutse.loc["2026-01-05", "minutes"] + lutse.loc["2026-01-10", "minutes"] + lutse.loc["2026-01-15", "minutes"]
    )
    assert lutse.loc["2026-01-18", "rolling_minutes"] == pytest.approx(expected)


def test_rolling_load_minutes_per_day_divides_by_the_window_size(engine):
    df = queries.rolling_load(engine, "bas", 1, 7)
    row = df[(df["player_name"] == "Marcus Howard") & (df["game_date"] == "2026-01-15")].iloc[0]
    assert row["minutes_per_day"] == pytest.approx(row["rolling_minutes"] / 7)


def test_rolling_load_empty_for_a_team_without_active_players_or_games(engine):
    df = queries.rolling_load(engine, "bas", 999, 7)
    assert df.empty


# ---------- performance_by_rest ----------


def test_performance_by_rest_buckets_in_fixed_order_not_alphabetical(engine):
    """'≥5' no puede salir antes que '≤1' solo porque su punto de código
    ordena antes — el orden es el de lectura (menos a más descanso)."""
    df = queries.performance_by_rest(engine, "bas", 1)
    assert list(df["rest_bucket"]) == [b for b in ["≤1", "2", "3-4", "≥5"] if b in set(df["rest_bucket"])]


def test_performance_by_rest_excludes_the_first_game_without_a_previous_one(engine):
    """g1 no tiene descanso calculable (primer partido del seed): sus 4
    partidos restantes (g2..g5, todos con `game_advanced_stats`) deben sumar
    exactamente el `gp` total de los tramos, sin g1 escondido en ninguno."""
    df = queries.performance_by_rest(engine, "bas", 1)
    assert df["gp"].sum() == 4


def test_performance_by_rest_puts_each_game_in_the_right_bucket(engine):
    # g2 (7 días) y g3 (5 días) -> '≥5'; g4 (5 días) -> '≥5'; g5 (3 días) -> '3-4'.
    df = queries.performance_by_rest(engine, "bas", 1).set_index("rest_bucket")
    assert int(df.loc["≥5", "gp"]) == 3
    assert int(df.loc["3-4", "gp"]) == 1
    assert "≤1" not in df.index
    assert "2" not in df.index


def test_performance_by_rest_empty_for_a_team_without_advanced_stats(engine):
    df = queries.performance_by_rest(engine, "bas", 999)
    assert df.empty
