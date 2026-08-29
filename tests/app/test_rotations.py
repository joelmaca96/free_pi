"""Tests de las consultas de rotaciones y parciales (`app/data/queries.py`).

La propuesta que implementan (`doc/features/propuestas/01_rotaciones_y_parciales.md`)
se apoya en tres cosas que hay que poder demostrar por separado, porque cada
una falla de una forma distinta y silenciosa:

- **La escalera del marcador** empieza en 0-0 y termina en el marcador
  oficial. Si no empieza en 0-0, un parcial de salida no tiene contra qué
  medirse; si no termina en el oficial, se pierden los puntos que caen
  después del último evento tipado (pasa de verdad: los tiros no son eventos).
- **La detección de parciales** no devuelve el mismo arreón cinco veces con
  ventanas ligeramente distintas, y recorta los segundos planos de los
  extremos. Un parcial que dura tres minutos porque arrastra minuto y medio
  de nada es una cifra falsa presentada con seguridad.
- **La atribución del quinteto** recorta los tramos a la ventana. Es
  exactamente el error que ya documenta `queries_assistant.clutch_lineups`:
  un tramo que empieza antes y acaba después no aporta todos sus minutos.
"""
import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import (
    detect_runs,
    game_end_seconds,
    game_runs,
    game_score_steps,
    game_stints,
    game_window_events,
    window_lineup,
)

#: Quinteto que abre `g5` y quinteto tras el primer cambio: se diferencian en
#: UN jugador (kotsar sale, sedekerskis entra), que es lo que hace visible si
#: `window_lineup` elige bien el tramo que más pesa en la ventana.
_OPENING = ["howard", "moneke", "codi", "nikos", "kotsar"]
_SECOND = ["howard", "moneke", "codi", "nikos", "sedekerskis"]

#: Play-by-play sintético de `g5` (bas local, val visitante, 84-79 final):
#: un 10-0 del Baskonia entre el segundo 120 y el 180, y un 0-9 en contra
#: entre el 600 y el 660. Todo lo demás es marcador plano, para que los dos
#: parciales sean los únicos candidatos posibles y el test pueda afirmar
#: números exactos en vez de "hay alguno".
_EVENTS = [
    (30.0, "Q1", "09:30", "dreb", "bas", 0, 0),
    (60.0, "Q1", "09:00", "dreb", "val", 2, 2),
    (90.0, "Q1", "08:30", "dreb", "bas", 4, 4),
    (120.0, "Q1", "08:00", "dreb", "val", 6, 6),
    (150.0, "Q1", "07:30", "steal", "bas", 11, 6),
    (180.0, "Q1", "07:00", "turnover", "val", 16, 6),
    (210.0, "Q1", "06:30", "dreb", "bas", 16, 6),
    (600.0, "Q2", "10:00", "dreb", "bas", 16, 6),
    (630.0, "Q2", "09:30", "dreb", "val", 16, 11),
    (660.0, "Q2", "09:00", "oreb", "val", 16, 15),
    (690.0, "Q2", "08:30", "dreb", "bas", 16, 15),
]


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """Mismo motivo que en `tests/app/assistant/conftest.py`.

    Las consultas van cacheadas por argumentos y el engine viaja como
    `_engine` (sin hashear): sin vaciar la caché, dos tests con bases de
    datos distintas pero el mismo `game_id` se leerían el resultado el uno
    del otro.
    """
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture()
def engine():
    """BD en memoria con el esquema + seed reales, más el play-by-play de `_EVENTS`."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    with eng.begin() as conn:
        for seconds, quarter, clock, event_type, team_id, home, away in _EVENTS:
            conn.execute(
                text(
                    "INSERT INTO play_events"
                    " (game_id, team_id, player_id, quarter, game_clock, seconds, event_type,"
                    "  home_score, away_score)"
                    " VALUES ('g5', :t, NULL, :q, :c, :s, :e, :h, :a)"
                ),
                {"t": team_id, "q": quarter, "c": clock, "s": seconds, "e": event_type, "h": home, "a": away},
            )
        # Dos tramos del Baskonia con el cambio EN MEDIO del primer parcial
        # (segundo 140, con el parcial entre el 120 y el 180): el tramo que
        # más pesa en la ventana es el segundo, no el que la abre.
        for start, end, points_for, points_against, players in (
            (0.0, 140.0, 6, 6, _OPENING),
            (140.0, 400.0, 10, 0, _SECOND),
        ):
            result = conn.execute(
                text(
                    "INSERT INTO lineup_stints"
                    " (game_id, team_id, start_seconds, end_seconds, points_for, points_against, margin_start)"
                    " VALUES ('g5', 'bas', :s, :e, :pf, :pa, 0)"
                ),
                {"s": start, "e": end, "pf": points_for, "pa": points_against},
            )
            for player_id in players:
                conn.execute(
                    text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                    {"s": result.lastrowid, "p": player_id},
                )
    try:
        yield eng
    finally:
        eng.dispose()


def _steps(*margins_at) -> pd.DataFrame:
    """Escalera mínima para probar `detect_runs` sin base de datos.

    Args:
        margins_at: pares `(segundo, margen)`. El marcador a favor/en contra
            se deriva del margen (todo lo anota el que va ganando), que es
            suficiente: `detect_runs` solo mira `margin` para detectar y
            `score_for`/`score_against` para redactar el resultado.
    """
    rows = [
        {
            "seconds": float(seconds),
            "quarter": "Q1",
            "game_clock": "10:00",
            "score_for": max(margin, 0),
            "score_against": max(-margin, 0),
            "margin": margin,
        }
        for seconds, margin in margins_at
    ]
    return pd.DataFrame(rows)


# ------------------------------------------------------------ game_end_seconds --


@pytest.mark.parametrize(
    ("last_seconds", "expected"),
    [(2384.0, 2400.0), (2400.0, 2400.0), (2401.0, 2700.0), (2694.0, 2700.0), (2700.0, 2700.0), (2701.0, 3000.0)],
)
def test_game_end_seconds_rounds_up_to_the_period_in_play(last_seconds, expected):
    """El último evento no es el final del partido: hay que redondear al final del periodo."""
    assert game_end_seconds(last_seconds) == expected


# ----------------------------------------------------------- game_score_steps --


def test_score_steps_open_at_zero_and_close_with_the_official_score(engine):
    """La escalera va de 0-0 al marcador de `games`, no del primer al último evento.

    El cierre no es cosmético: `play_events` no tipa los tiros, así que la
    última canasta puede caer después del último evento del play-by-play y la
    escalera se quedaría corta justo donde más se mira.
    """
    steps = game_score_steps.__wrapped__(engine, "g5", "bas")

    opening, closing = steps.iloc[0], steps.iloc[-1]
    assert (opening["seconds"], opening["score_for"], opening["score_against"]) == (0.0, 0, 0)
    assert (closing["seconds"], closing["score_for"], closing["score_against"]) == (2400.0, 84, 79)
    # El último evento tipado va en el 690 y solo llega a 16-15: sin el cierre
    # oficial se perderían 68 puntos de partido.
    assert steps["margin"].iloc[-1] == 5


def test_score_steps_are_seen_from_the_team_you_ask_for(engine):
    """El mismo partido, visto desde el visitante, es la misma escalera con el signo cambiado."""
    ours = game_score_steps.__wrapped__(engine, "g5", "bas")
    theirs = game_score_steps.__wrapped__(engine, "g5", "val")

    assert list(ours["margin"]) == [-margin for margin in theirs["margin"]]
    assert theirs["score_for"].iloc[-1] == 79


def test_score_steps_collapse_the_events_that_share_a_second(engine):
    """Un robo y la pérdida que lo acompaña son dos eventos y UN escalón."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO play_events"
                " (game_id, team_id, player_id, quarter, game_clock, seconds, event_type, home_score, away_score)"
                " VALUES ('g5', 'val', NULL, 'Q1', '07:30', 150.0, 'turnover', 11, 6)"
            )
        )
    steps = game_score_steps.__wrapped__(engine, "g5", "bas")

    assert (steps["seconds"] == 150.0).sum() == 1


def test_score_steps_are_empty_without_typed_play_by_play(engine):
    """Un partido sin play-by-play es una sección vacía, no un error."""
    assert game_score_steps.__wrapped__(engine, "g1", "bas").empty


# ----------------------------------------------------------------- detect_runs --


def test_detect_runs_finds_both_directions_with_their_points(engine):
    """Los dos parciales de `_EVENTS`, ordenados por magnitud y con el signo correcto."""
    runs = game_runs.__wrapped__(engine, "g5", "bas", 180.0, 8)

    assert len(runs) == 2
    best, worst = runs.iloc[0], runs.iloc[1]
    assert (best["start_seconds"], best["end_seconds"], best["swing"]) == (120.0, 180.0, 10)
    assert (best["points_for"], best["points_against"], best["direction"]) == (10, 0, "favor")
    assert (worst["start_seconds"], worst["end_seconds"], worst["swing"]) == (600.0, 660.0, -9)
    assert (worst["points_for"], worst["points_against"], worst["direction"]) == (0, 9, "contra")
    assert "10-0 (+10)" in best["label"]


def test_detect_runs_trims_the_flat_seconds_at_both_ends():
    """Un 10-0 en un minuto no es un parcial de tres minutos porque antes hubo calma.

    Sin el recorte, la ventana que un entrenador se lleva al vídeo empieza dos
    minutos antes de que pase nada.
    """
    steps = _steps((0, 0), (60, 0), (120, 0), (150, 5), (180, 10), (210, 10), (240, 10))

    runs = detect_runs(steps, window_s=180.0, min_swing=8)

    assert len(runs) == 1
    assert (runs.iloc[0]["start_seconds"], runs.iloc[0]["end_seconds"]) == (120.0, 180.0)
    assert runs.iloc[0]["duration_s"] == 60.0


def test_detect_runs_does_not_report_the_same_run_from_five_starting_points():
    """Cada escalón de la subida abre una ventana válida; todas describen UN parcial."""
    steps = _steps((0, 0), (30, 3), (60, 6), (90, 9), (120, 12), (150, 12))

    runs = detect_runs(steps, window_s=180.0, min_swing=8)

    assert len(runs) == 1
    assert runs.iloc[0]["swing"] == 12


def test_detect_runs_keeps_two_runs_that_do_not_overlap():
    """Dos arreones separados son dos parciales, no uno grande."""
    steps = _steps((0, 0), (60, 10), (120, 10), (600, 10), (660, 0), (720, 0))

    runs = detect_runs(steps, window_s=180.0, min_swing=8)

    assert len(runs) == 2
    assert sorted(runs["swing"]) == [-10, 10]


def test_detect_runs_respects_the_thresholds_it_is_given():
    """Los umbrales son de la interfaz, no del código: subirlos tiene que vaciar la lista."""
    steps = _steps((0, 0), (60, 8), (120, 8))

    assert len(detect_runs(steps, window_s=180.0, min_swing=8)) == 1
    assert detect_runs(steps, window_s=180.0, min_swing=9).empty
    # La misma subida, pero pedida en una ventana que no le da tiempo a pasar.
    assert detect_runs(steps, window_s=30.0, min_swing=8).empty


def test_detect_runs_survives_a_staircase_too_short_to_have_any():
    assert detect_runs(_steps((0, 0)), window_s=180.0, min_swing=8).empty
    assert detect_runs(pd.DataFrame(), window_s=180.0, min_swing=8).empty


# --------------------------------------------------------------- window_lineup --


def test_window_lineup_picks_the_stint_that_weighs_most_in_the_window(engine):
    """El quinteto del parcial es el que más segundos aporta, no el que lo abre.

    En `_EVENTS` el cambio cae en el segundo 140 y el parcial va del 120 al
    180: el tramo de apertura aporta 20 segundos y el siguiente 40, así que el
    quinteto del parcial es el segundo — con sedekerskis y sin kotsar.
    """
    stints = game_stints.__wrapped__(engine, "g5", "bas")

    lineup = window_lineup(stints, 120.0, 180.0)

    assert len(lineup) == 5
    assert set(lineup["player_id"]) == set(_SECOND)
    assert "kotsar" not in set(lineup["player_id"])


def test_window_lineup_adds_up_each_players_seconds_across_stints(engine):
    """Un jugador que sigue en pista tras el cambio lleva el parcial entero.

    Es la diferencia entre los minutos del QUINTETO y los del JUGADOR: los
    cuatro que no se mueven acumulan los 60 segundos de la ventana aunque su
    quinteto solo dure 40.
    """
    stints = game_stints.__wrapped__(engine, "g5", "bas")

    lineup = window_lineup(stints, 120.0, 180.0).set_index("player_id")

    assert lineup.loc["howard", "seconds"] == 60.0
    assert lineup.loc["howard", "share"] == 100.0
    assert lineup.loc["sedekerskis", "seconds"] == 40.0
    assert lineup.loc["sedekerskis", "share"] == pytest.approx(66.67, abs=0.01)


def test_window_lineup_clips_a_stint_that_spills_out_of_the_window(engine):
    """Mismo recorte que `clutch_lineups`: solo cuenta el trozo dentro de la ventana."""
    stints = game_stints.__wrapped__(engine, "g5", "bas")

    # El segundo tramo va del 140 al 400; en una ventana 150..200 aporta 50 s.
    lineup = window_lineup(stints, 150.0, 200.0).set_index("player_id")

    assert lineup.loc["howard", "seconds"] == 50.0


def test_window_lineup_is_empty_without_stints():
    """Un partido sin tramos deja la sección vacía, no reventada."""
    assert window_lineup(pd.DataFrame(), 0.0, 100.0).empty


# ---------------------------------------------------------- game_window_events --


def test_window_events_only_returns_the_window_and_marks_who_is_ours(engine):
    """La segunda capa de la vista: qué pasó, de quién, y cómo iba el marcador."""
    events = game_window_events.__wrapped__(engine, "g5", 120.0, 180.0, "bas")

    assert list(events["seconds"]) == [120.0, 150.0, 180.0]
    assert list(events["event_type"]) == ["dreb", "steal", "turnover"]
    assert list(events["is_own"]) == [False, True, False]
    # Marcador desde el Baskonia (local): el robo del 150 se ve con 11-6.
    assert (events.iloc[1]["score_for"], events.iloc[1]["score_against"]) == (11, 6)


def test_window_events_flip_the_score_for_the_other_team(engine):
    """El mismo evento, contado desde el rival, tiene el marcador al revés."""
    events = game_window_events.__wrapped__(engine, "g5", 120.0, 180.0, "val")

    assert (events.iloc[1]["score_for"], events.iloc[1]["score_against"]) == (6, 11)
    assert list(events["is_own"]) == [True, False, True]


def test_window_events_are_empty_outside_the_play_by_play(engine):
    assert game_window_events.__wrapped__(engine, "g5", 1000.0, 1100.0, "bas").empty
