"""Tests de la probabilidad de victoria y los momentos clave (propuesta 18, hoja de ruta A4).

`app/analytics/win_probability.py` es lógica pura: se prueba sin base de datos,
con una liga SIMULADA (paseo aleatorio de posesiones con semilla fija) para el
ajuste y con escaleras de marcador escritas a mano para los momentos clave.
Lo que se afirma son las propiedades que, si fallan, hacen mentir a la
pantalla sin que se note:

- el modelo **crece con el margen** en todo el partido, **acaba en 0/1** con
  el partido decidido y **arranca cerca del % de victorias locales**;
- los momentos clave se ordenan por **cambio de probabilidad**, no por puntos:
  un 5-0 al final con el partido igualado va por delante de un 10-0 de salida;
- la WPA por quinteto **suma exactamente** probabilidad final − inicial.

Al final, unas pocas pruebas contra una base de datos en memoria para la capa
de consultas (`app/data/queries_win_probability.py`).
"""
import numpy as np
import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.analytics import win_probability as wp
from app.data import queries_win_probability as qwp


# ------------------------------------------------------------ liga simulada --


def _simulate_game(rng, home_edge=0.012):
    """Un partido: posesiones alternas de 10-20 s, OT de 5 min si hay empate."""
    t, home, away, rows, end = 0.0, 0, 0, [(0.0, 0, 0)], wp.REGULATION_SECONDS
    home_ball = bool(rng.random() < 0.5)
    while True:
        while True:
            t += float(rng.uniform(10.0, 20.0))
            if t >= end:
                break
            p_score = 0.50 + (home_edge if home_ball else -home_edge)
            if rng.random() < p_score:
                points = 3 if rng.random() < 0.3 else 2
                if home_ball:
                    home += points
                else:
                    away += points
            home_ball = not home_ball
            rows.append((t, home, away))
        if home != away:
            break
        t, end = end, end + wp.OVERTIME_SECONDS
    rows.append((end, home, away))
    steps = pd.DataFrame(rows, columns=["seconds", "score_for", "score_against"])
    steps = steps.drop_duplicates("seconds", keep="last").reset_index(drop=True)
    steps["margin"] = steps["score_for"] - steps["score_against"]
    return steps, home > away


def _league_states(n_games, seed=11):
    rng = np.random.default_rng(seed)
    frames = []
    for game in range(n_games):
        steps, home_win = _simulate_game(rng)
        frames.append(wp.sample_game_states(steps, home_win, game_id=f"sim{game}"))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def league_states():
    return _league_states(400)


@pytest.fixture(scope="module")
def fitted(league_states):
    return wp.fit_win_probability(league_states)


# ------------------------------------------------------------------- reloj --


def test_seconds_remaining_counts_to_the_end_of_regulation_or_of_the_current_overtime():
    got = wp.seconds_remaining([0.0, 1800.0, 2400.0, 2500.0, 2700.0, 2750.0])
    assert got.tolist() == [2400.0, 600.0, 0.0, 200.0, 0.0, 250.0]


def test_period_clock_resolves_the_quarter_change_by_start_or_end():
    assert wp.period_clock(0.0) == ("Q1", "10:00")
    assert wp.period_clock(600.0) == ("Q2", "10:00")
    assert wp.period_clock(600.0, at_end=True) == ("Q1", "00:00")
    assert wp.period_clock(2400.0, at_end=True) == ("Q4", "00:00")
    assert wp.period_clock(2450.0) == ("OT1", "04:10")


# ---------------------------------------------------------------- muestreo --


def test_sample_game_states_takes_one_photo_every_30_seconds_as_a_staircase():
    steps = pd.DataFrame({"seconds": [0.0, 45.0, 100.0, 2400.0], "margin": [0, 2, -1, 3]})
    states = wp.sample_game_states(steps, True, game_id="x")

    assert len(states) == 80  # 0, 30, ..., 2370
    assert states["seconds"].iloc[:5].tolist() == [0.0, 30.0, 60.0, 90.0, 120.0]
    # Escalera: el +2 del segundo 45 vale en el 60 y el 90; el -1 del 100, en el 120.
    assert states["margin"].iloc[:5].tolist() == [0, 0, 2, 2, -1]
    assert set(states["home_win"]) == {1}
    assert (states["seconds_remaining"] > 0).all()


def test_sample_game_states_keeps_the_end_of_regulation_of_an_overtime_game_as_a_tie():
    """Regresión: si hubo prórroga, el final del tiempo reglamentario era un empate.

    La escalera puede traer otro margen en el segundo 2400 (el marcador se
    observa en el siguiente evento tipado: una falta en la bocina lleva el
    marcador de ANTES de los tiros libres del empate). Antes, esa foto se
    descartaba como "partido decidido"; es justo el estado que informa de la
    constante del modelo (quién gana la prórroga).
    """
    steps = pd.DataFrame({"seconds": [0.0, 2300.0, 2400.0, 2410.0, 2700.0], "margin": [0, -2, -2, 0, 4]})
    states = wp.sample_game_states(steps, True, game_id="ot")

    end_of_regulation = states[states["seconds"] == 2400.0]
    assert len(end_of_regulation) == 1
    assert end_of_regulation.iloc[0]["seconds_remaining"] == 0.0
    assert end_of_regulation.iloc[0]["margin"] == 0
    assert states["seconds"].max() == 2670.0  # la rejilla no llega al final oficial
    assert len(states) == 90


# ------------------------------------------------------------------ modelo --


def test_enough_games_fit_the_logistic_and_too_few_fall_back_to_the_normal_model(league_states, fitted):
    assert fitted.kind == "logistic"
    assert fitted.n_games == 400
    few = league_states[league_states["game_id"].isin([f"sim{i}" for i in range(10)])]
    assert wp.fit_win_probability(few).kind == "normal"
    assert wp.fit_win_probability(pd.DataFrame()).kind == "normal"


@pytest.mark.parametrize("model_name", ["fitted", "normal"])
def test_win_probability_grows_with_the_margin_at_every_point_of_the_game(model_name, fitted):
    model = fitted if model_name == "fitted" else wp.normal_model()
    margins = np.arange(-20, 21)
    for t in (2400.0, 1800.0, 600.0, 120.0, 10.0):
        p = wp.predict_home_wp(model, margins, np.full(len(margins), t))
        assert np.all(np.diff(p) >= 0), t
        # Estrictamente creciente mientras no está saturado (a 10 s del final
        # un +15 y un +20 son los dos un 1,0 en coma flotante).
        live = (p[:-1] > 1e-9) & (p[1:] < 1 - 1e-9)
        assert np.all(np.diff(p)[live] > 0), t


@pytest.mark.parametrize("model_name", ["fitted", "normal"])
def test_win_probability_goes_to_zero_or_one_at_the_end(model_name, fitted):
    model = fitted if model_name == "fitted" else wp.normal_model()
    # Partido acabado: exacto, no una probabilidad del modelo.
    assert wp.predict_home_wp(model, [1, -1], [0.0, 0.0]).tolist() == [1.0, 0.0]
    # A falta de 5 segundos, 6 puntos ya son casi seguros.
    late = wp.predict_home_wp(model, [6, -6], [5.0, 5.0])
    assert late[0] > 0.97 and late[1] < 0.03
    # Un empate al final no es 0/1: es la prórroga.
    assert 0.3 < wp.predict_home_wp(model, [0], [0.0])[0] < 0.7


def test_fitted_model_starts_near_the_home_win_rate(fitted):
    start = wp.predict_home_wp(fitted, [0], [wp.REGULATION_SECONDS])[0]
    assert abs(start - fitted.home_win_rate) < 0.04


def test_normal_model_starts_at_the_home_edge_prior():
    start = wp.predict_home_wp(wp.normal_model(), [0], [wp.REGULATION_SECONDS])[0]
    # Φ(3/12) con σ·sqrt(t + c) ≈ σ en el salto inicial.
    assert start == pytest.approx(0.598, abs=0.01)


def test_pregame_edge_moves_the_start_but_not_the_end():
    model = wp.normal_model()
    base, favoured = wp.predict_home_wp(model, [0, 0], [2400.0, 2400.0], 0.0)[0], wp.predict_home_wp(
        model, [0], [2400.0], 6.0
    )[0]
    assert favoured > base
    assert wp.predict_home_wp(model, [-2], [0.0], 6.0)[0] == 0.0


# ---------------------------------------------------------- momentos clave --


def _ladder(points):
    """`[(segundo, a_favor, en_contra), ...]` -> escalera como la de `game_score_steps`."""
    df = pd.DataFrame(points, columns=["seconds", "score_for", "score_against"])
    df["quarter"], df["game_clock"] = "", ""
    df["margin"] = df["score_for"] - df["score_against"]
    return df


def _early_run_then_late_swing():
    """10-0 de salida (min 2-4), el rival lo empata a mitad de partido, marcador
    igualado hasta el final y un 5-0 en el minuto 38: el que decide."""
    points = [(0.0, 0, 0), (120.0, 0, 0), (150.0, 3, 0), (180.0, 5, 0), (210.0, 8, 0), (240.0, 10, 0)]
    score_for, score_against = 10, 0
    for second in range(600, 1100, 100):  # el rival recorta de 2 en 2: 10-0 -> 10-10
        score_against += 2
        points.append((float(second), score_for, score_against))
    for second in range(1200, 2260, 60):  # igualado: los dos anotan en el mismo escalón
        score_for += 2
        score_against += 2
        points.append((float(second), score_for, score_against))
    points += [
        (2280.0, score_for + 3, score_against),
        (2310.0, score_for + 5, score_against),
        (2400.0, score_for + 5, score_against + 2),
    ]
    return _ladder(points)


@pytest.mark.parametrize("model_name", ["fitted", "normal"])
def test_key_moments_rank_the_late_swing_above_the_bigger_early_run(model_name, fitted):
    model = fitted if model_name == "fitted" else wp.normal_model()
    curve = wp.game_wp_curve(_early_run_then_late_swing(), model, is_home=True)
    moments = wp.key_moments(curve, n=5)

    top = moments.iloc[0]
    assert top["rank"] == 1
    assert top["start_seconds"] >= 2200
    assert abs(top["margin_after"] - top["margin_before"]) <= 5
    early = moments[moments["start_seconds"] < 300]
    assert not early.empty, "el 10-0 de salida también es un momento, solo que menor"
    assert abs(early.iloc[0]["margin_after"] - early.iloc[0]["margin_before"]) == 10
    assert early["wpa"].abs().max() < top["wpa"]
    # Ordenados de más a menos |ΔWP|, sin ventanas solapadas.
    assert list(moments["wpa"].abs()) == sorted(moments["wpa"].abs(), reverse=True)
    spans = sorted(zip(moments["start_seconds"], moments["end_seconds"]))
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))


def test_key_moments_describe_clock_score_and_probability():
    curve = wp.game_wp_curve(_early_run_then_late_swing(), wp.normal_model(), is_home=True)
    top = wp.key_moments(curve, n=1).iloc[0]
    assert top["quarter_start"] == "Q4"
    assert top["points_against"] == 0 and top["points_for"] > 0
    assert top["wp_after"] > top["wp_before"]
    assert "Q4" in top["label"] and "pp" in top["label"]


def test_key_moments_ignore_windows_where_only_the_clock_moves():
    flat = _ladder([(0.0, 0, 0), (600.0, 10, 0), (1200.0, 10, 0), (2300.0, 10, 0), (2400.0, 10, 0)])
    moments = wp.key_moments(wp.game_wp_curve(flat, wp.normal_model(), is_home=True), window_s=900.0)
    assert (moments["margin_after"] != moments["margin_before"]).all()


def test_curve_from_the_away_side_is_the_complement():
    steps = _early_run_then_late_swing()
    home = wp.game_wp_curve(steps, wp.normal_model(), is_home=True)
    # Mismo partido visto desde el visitante que va por DETRÁS en el marcador.
    mirrored = steps.assign(
        score_for=steps["score_against"], score_against=steps["score_for"], margin=-steps["margin"]
    )
    away = wp.game_wp_curve(mirrored, wp.normal_model(), is_home=False)
    np.testing.assert_allclose(away["wp"].to_numpy(), 1.0 - home["wp"].to_numpy())
    assert home["wp"].iloc[-1] == 1.0 and away["wp"].iloc[-1] == 0.0


def _overtime_with_stale_regulation_end():
    """Prórroga tras una falta en la bocina: el evento de Q4 00:00 (segundo
    2400) lleva el marcador de antes de los tiros libres (80-82) y el empate
    se ve en el primer evento de la prórroga; el Baskonia gana 92-88."""
    return _ladder([
        (0.0, 0, 0), (2300.0, 80, 82), (2400.0, 80, 82), (2410.0, 82, 82), (2600.0, 90, 88), (2700.0, 92, 88),
    ])


@pytest.mark.parametrize("model_name", ["fitted", "normal"])
def test_overtime_curve_does_not_decide_the_game_at_the_end_of_regulation(model_name, fitted):
    """Regresión: con el margen de la escalera (-2 a 0 s) la curva caía a 0% en
    el segundo 2400 y la prórroga la "resucitaba" al instante: un momento clave
    falso de +50 pp en OT1 05:00, y el momento real (los tiros libres del
    empate) partido en dos por el recorte."""
    model = fitted if model_name == "fitted" else wp.normal_model()
    curve = wp.game_wp_curve(_overtime_with_stale_regulation_end(), model, is_home=True)

    assert 2400.0 not in set(curve["seconds"])
    assert ((curve["wp"] > 0.0) & (curve["wp"] < 1.0)).iloc[:-1].all()
    assert curve["wp"].iloc[-1] == 1.0

    moments = wp.key_moments(curve, n=5)
    tying = moments[moments["score_after"] == "82-82"].iloc[0]
    assert tying["start_seconds"] == 2300.0 and tying["end_seconds"] == 2410.0
    assert tying["wp_before"] < 0.4 and tying["wpa"] > 0.15
    # Ninguna ventana arranca desde un "0%" ficticio.
    assert (moments["wp_before"] > 0.0).all()


def test_lineup_wpa_does_not_zero_out_at_the_end_of_regulation_before_overtime():
    """Regresión: un cambio de quinteto en el 2400 de un partido con prórroga
    evaluaba la probabilidad con el margen retrasado de la escalera (-2 a 0 s:
    0% exacto). El quinteto de la prórroga se llevaba el partido entero
    (+100 pp) y el del cuarto, todo lo contrario."""
    model = wp.normal_model()
    steps = _overtime_with_stale_regulation_end()
    curve = wp.game_wp_curve(steps, model, is_home=True)
    stints = _stints([(1, 0.0, 2400.0, _STARTERS), (2, 2400.0, 2700.0, _BENCH)])
    own = wp.lineup_wpa(curve, stints, model, is_home=True).set_index("lineup")["wpa"]

    tie = wp.predict_home_wp(model, 0.0, 0.0)[0]
    assert own["F · G · H · I · J"] == pytest.approx(1.0 - tie)
    assert own["A · B · C · D · E"] == pytest.approx(tie - curve["wp"].iloc[0])
    assert own.sum() == pytest.approx(curve["wp"].iloc[-1] - curve["wp"].iloc[0], abs=1e-9)


# -------------------------------------------------------- WPA por quinteto --


def _stints(rows):
    """`[(stint_id, inicio, fin, [jugadores]), ...]` -> formato de `queries.game_stints`."""
    out = []
    for stint_id, start, end, players in rows:
        for name in players:
            out.append({
                "stint_id": stint_id, "start_seconds": start, "end_seconds": end,
                "points_for": 0, "points_against": 0, "margin_start": 0,
                "player_id": name.lower(), "player_name": name,
            })
    return pd.DataFrame(out)


_STARTERS = ["A", "B", "C", "D", "E"]
_BENCH = ["F", "G", "H", "I", "J"]


@pytest.mark.parametrize("model_name", ["fitted", "normal"])
def test_lineup_wpa_adds_up_to_final_minus_initial_probability(model_name, fitted):
    model = fitted if model_name == "fitted" else wp.normal_model()
    curve = wp.game_wp_curve(_early_run_then_late_swing(), model, is_home=True)
    stints = _stints([
        (1, 0.0, 700.0, _STARTERS),
        (2, 700.0, 2250.0, _BENCH),
        (3, 2250.0, 2400.0, _STARTERS),
    ])
    own = wp.lineup_wpa(curve, stints, model, is_home=True)
    rival = wp.lineup_wpa(curve, stints, model, is_home=True, team_is_curve_team=False)

    expected = curve["wp"].iloc[-1] - curve["wp"].iloc[0]
    assert own["wpa"].sum() == pytest.approx(expected, abs=1e-9)
    assert rival["wpa"].sum() == pytest.approx(-expected, abs=1e-9)
    assert set(own["lineup"]) == {"A · B · C · D · E", "F · G · H · I · J"}
    starters = own.set_index("lineup").loc["A · B · C · D · E"]
    assert starters["stints"] == 2
    assert starters["minutes"] == pytest.approx((700.0 + 150.0) / 60.0)


def test_lineup_wpa_keeps_the_unattributed_part_instead_of_losing_it():
    model = wp.normal_model()
    curve = wp.game_wp_curve(_early_run_then_late_swing(), model, is_home=True)
    # Hueco entre el 700 y el 2250: nadie en pista según los tramos.
    stints = _stints([(1, 0.0, 700.0, _STARTERS), (3, 2250.0, 2400.0, _BENCH)])
    own = wp.lineup_wpa(curve, stints, model, is_home=True)

    assert "(sin tramo de quinteto)" in set(own["lineup"])
    assert own["wpa"].sum() == pytest.approx(curve["wp"].iloc[-1] - curve["wp"].iloc[0], abs=1e-9)
    # El quinteto del cierre se lleva el 5-0 decisivo: la mayor WPA.
    assert own.iloc[0]["lineup"] == "F · G · H · I · J"


def test_lineup_wpa_without_stints_is_all_unattributed():
    model = wp.normal_model()
    curve = wp.game_wp_curve(_early_run_then_late_swing(), model, is_home=True)
    own = wp.lineup_wpa(curve, pd.DataFrame(), model, is_home=True)
    assert own["lineup"].tolist() == ["(sin tramo de quinteto)"]


# ------------------------------------------------------------------- clips --


def test_lineups_table_without_stints_says_so_instead_of_a_fake_lineup(monkeypatch):
    """Regresión: sin tramos, `lineup_wpa` devuelve solo la fila "(sin tramo
    de quinteto)" y la pestaña la pintaba como si fuera un quinteto."""
    from app.components import key_moments as component

    shown = {"caption": [], "dataframe": 0}
    monkeypatch.setattr(component.st, "caption", lambda text, **_: shown["caption"].append(text))
    monkeypatch.setattr(component.st, "dataframe", lambda *a, **k: shown.__setitem__("dataframe", shown["dataframe"] + 1))

    model = wp.normal_model()
    curve = wp.game_wp_curve(_early_run_then_late_swing(), model, is_home=True)
    component._lineups_table(wp.lineup_wpa(curve, pd.DataFrame(), model, is_home=True), key="x")
    assert shown == {"caption": ["Sin tramos de quinteto en este partido."], "dataframe": 0}

    stints = _stints([(1, 0.0, 2400.0, _STARTERS)])
    component._lineups_table(wp.lineup_wpa(curve, stints, model, is_home=True), key="y")
    assert shown["dataframe"] == 1


def test_clip_list_is_chronological_padded_and_csv_ready():
    curve = wp.game_wp_curve(_early_run_then_late_swing(), wp.normal_model(), is_home=True)
    moments = wp.key_moments(curve, n=3)
    clips = wp.clip_list(moments, game_end=2400.0, team_label="Baskonia")

    assert list(clips.columns) == [
        "orden", "cuarto_inicio", "reloj_inicio", "cuarto_fin", "reloj_fin",
        "segundo_inicio", "segundo_fin", "descripcion",
    ]
    assert clips["segundo_inicio"].is_monotonic_increasing
    first = moments.sort_values("start_seconds").iloc[0]
    assert clips.iloc[0]["segundo_inicio"] == pytest.approx(first["start_seconds"] - wp.CLIP_PAD_BEFORE_S)
    assert (clips["segundo_fin"] <= 2400.0).all()
    assert clips["descripcion"].str.contains("Baskonia").all()
    assert clips.to_csv(index=False).startswith("orden,cuarto_inicio")
    assert wp.clip_list(moments.iloc[0:0]).empty


def test_clip_pre_roll_goes_back_in_game_time_across_quarters_and_into_regulation():
    """El reloj va hacia atrás: 10 s antes del inicio es un reloj MAYOR en el
    mismo cuarto, o los últimos segundos del cuarto anterior."""
    moments = pd.DataFrame([
        {"rank": 1, "start_seconds": 1205.0, "end_seconds": 1290.0, "points_for": 5, "points_against": 0,
         "score_before": "40-40", "score_after": "45-40", "wp_before": 0.5, "wp_after": 0.6, "wpa": 0.1},
        {"rank": 2, "start_seconds": 2404.0, "end_seconds": 2698.0, "points_for": 6, "points_against": 2,
         "score_before": "80-80", "score_after": "86-82", "wp_before": 0.5, "wp_after": 1.0, "wpa": 0.5},
        {"rank": 3, "start_seconds": 1500.0, "end_seconds": 1560.0, "points_for": 4, "points_against": 0,
         "score_before": "50-50", "score_after": "54-50", "wp_before": 0.5, "wp_after": 0.55, "wpa": 0.05},
    ])
    clips = wp.clip_list(moments, game_end=2700.0)

    first, middle, last = clips.to_dict("records")
    # 1205 - 10 = 1195: cinco segundos antes del final del segundo cuarto.
    assert (first["cuarto_inicio"], first["reloj_inicio"]) == ("Q2", "00:05")
    assert (first["cuarto_fin"], first["reloj_fin"]) == ("Q3", "08:25")
    assert (middle["cuarto_inicio"], middle["reloj_inicio"]) == ("Q3", "05:10")  # 09:00 + 10 s
    assert (middle["cuarto_fin"], middle["reloj_fin"]) == ("Q3", "03:55")
    # 2404 - 10 = 2394: el clip de la prórroga arranca en el último cuarto; el
    # final (2698 + 5) se recorta al final del partido: OT1 00:00, no "OT2".
    assert (last["cuarto_inicio"], last["reloj_inicio"]) == ("Q4", "00:06")
    assert (last["cuarto_fin"], last["reloj_fin"]) == ("OT1", "00:00")
    assert last["segundo_fin"] == 2700.0


# --------------------------------------------------- consultas (BD en memoria) --

#: `g5` del seed (bas local 84-79 val): igualado hasta el final y 5-0 en el 38.
_G5_EVENTS = [
    (120.0, "Q1", "08:00", 4, 4),
    (900.0, "Q2", "05:00", 30, 30),
    (1800.0, "Q4", "10:00", 60, 60),
    (2250.0, "Q4", "02:30", 77, 77),
    (2280.0, "Q4", "02:00", 80, 77),
    (2310.0, "Q4", "01:30", 82, 77),
    (2390.0, "Q4", "00:10", 82, 79),
]


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """Mismo motivo que en `tests/app/test_rotations.py` (caché por argumentos)."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    with eng.begin() as conn:
        for seconds, quarter, clock, home, away in _G5_EVENTS:
            conn.execute(
                text(
                    "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                    " event_type, home_score, away_score) VALUES ('g5', 'bas', NULL, :q, :c, :s, 'dreb', :h, :a)"
                ),
                {"q": quarter, "c": clock, "s": seconds, "h": home, "a": away},
            )
        result = conn.execute(
            text(
                "INSERT INTO lineup_stints (game_id, team_id, start_seconds, end_seconds, points_for,"
                " points_against, margin_start) VALUES ('g5', 'bas', 0, 2400, 84, 79, 0)"
            )
        )
        for player_id in ("howard", "moneke", "codi", "nikos", "kotsar"):
            conn.execute(
                text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                {"s": result.lastrowid, "p": player_id},
            )
    try:
        yield eng
    finally:
        eng.dispose()


def test_league_score_states_only_uses_finished_games_with_play_by_play(engine):
    states = qwp.league_score_states(engine, 1)
    assert set(states["game_id"]) == {"g5"}
    assert set(states["home_win"]) == {1}


def test_game_key_moments_bundle_matches_the_official_result(engine):
    bundle = qwp.game_key_moments(engine, "g5", "bas", team_label="Baskonia")

    assert bundle["is_home"] and bundle["rival_team_id"] == "val"
    assert bundle["model"].kind == "normal"  # un solo partido: modelo de reserva
    curve = bundle["curve"]
    assert curve["score_for"].iloc[-1] == 84 and curve["wp"].iloc[-1] == 1.0
    top = bundle["moments"].iloc[0]
    assert top["start_seconds"] >= 2250
    assert "Marcus Howard" in top["lineup_own"]
    assert top["lineup_rival"] is None  # sin tramos del rival
    assert bundle["lineups_own"]["wpa"].sum() == pytest.approx(curve["wp"].iloc[-1] - curve["wp"].iloc[0])
    assert not bundle["clips"].empty


def test_game_key_moments_from_the_rival_side_and_for_a_foreign_team(engine):
    rival = qwp.game_key_moments(engine, "g5", "val")
    assert not rival["is_home"] and rival["curve"]["wp"].iloc[-1] == 0.0
    assert rival["moments"].iloc[0]["wpa"] < 0
    assert qwp.game_key_moments(engine, "g5", "rm") is None
    assert qwp.game_key_moments(engine, "no-existe", "bas") is None


def test_game_key_moments_without_play_by_play_comes_back_empty(engine):
    bundle = qwp.game_key_moments(engine, "g4", "bas")
    assert bundle["curve"].empty and bundle["moments"].empty and bundle["clips"].empty
