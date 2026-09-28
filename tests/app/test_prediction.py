"""Tests de la predicción del partido (`app/analytics/prediction.py` + `app/data/queries_prediction.py`).

Liga sintética con fuerza de equipo, ventaja de campo y efecto del descanso
CONOCIDOS (ruido con semilla fija): el modelo tiene que recuperar el orden de
los equipos, el signo de la ventaja de campo y el del descanso, y su
descomposición tiene que sumar exactamente el margen que predice. Lo que NO
se fija es el valor exacto de cada rating: depende del ruido y del ridge, y un
test que lo congelara solo estorbaría al recalibrar λ.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.analytics import prediction
from app.data import queries_prediction

TEAMS = [f"t{i}" for i in range(12)]
TRUE_RATING = dict(zip(TEAMS, np.linspace(9.0, -9.0, len(TEAMS))))
TRUE_HOME = 3.0
TRUE_REST = 1.0


def _synthetic_league(seed: int = 7, days: int = 220, noise: float = 10.0, home: float = TRUE_HOME,
                      rest: float = TRUE_REST, competition: str = "ACB") -> pd.DataFrame:
    """Calendario irregular (cada equipo juega con probabilidad 0,4 cada día) → descansos variados."""
    rng = np.random.default_rng(seed)
    last = {t: dt.date(2025, 9, 20) for t in TEAMS}
    rows, number = [], 0
    for day in range(days):
        date = dt.date(2025, 10, 1) + dt.timedelta(days=day)
        available = [t for t in TEAMS if rng.random() < 0.4]
        rng.shuffle(available)
        for i in range(0, len(available) - 1, 2):
            h, a = available[i], available[i + 1]
            rh = prediction.clip_rest((date - last[h]).days)
            ra = prediction.clip_rest((date - last[a]).days)
            margin = TRUE_RATING[h] - TRUE_RATING[a] + home + rest * (rh - ra) + rng.normal(0, noise)
            margin = int(round(margin)) or 1
            number += 1
            rows.append({
                "game_id": f"g{number}", "game_date": date, "home_team_id": h, "away_team_id": a,
                "home_score": 80 + margin, "away_score": 80, "competition": competition,
            })
            last[h] = last[a] = date
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def league():
    return prediction.prepare_games(_synthetic_league())


@pytest.fixture(scope="module")
def fit(league):
    return prediction.fit_ratings(league)


# ---------- ajuste ----------


def test_recovers_the_order_of_known_team_strengths(fit):
    estimated = fit.ratings.reindex(TEAMS).to_numpy()
    truth = np.array([TRUE_RATING[t] for t in TEAMS])
    assert np.corrcoef(estimated, truth)[0, 1] > 0.9
    assert fit.ratings.index[0] in ("t0", "t1")
    assert fit.ratings.index[-1] in ("t10", "t11")


def test_ratings_are_centered_on_an_average_team(fit):
    assert abs(fit.ratings.mean()) < 1e-9


def test_home_court_has_the_right_sign_and_size(fit):
    assert 1.0 < fit.home_court < 5.0


def test_rest_effect_is_estimated_positive_and_within_bounds(fit):
    assert fit.rest_estimated
    assert 0.0 < fit.rest_per_day <= prediction.MAX_REST_EFFECT


def test_rest_effect_never_goes_negative():
    """Con un efecto real NEGATIVO (ruido), la restricción de signo lo deja en 0, no lo cuenta al revés."""
    games = prediction.prepare_games(_synthetic_league(seed=3, rest=-2.0))
    assert prediction.fit_ratings(games).rest_per_day == 0.0


def test_sigma_does_not_collapse_when_the_fit_is_perfect():
    """Sin ruido el ajuste explica todo; σ se encoge hacia el prior en vez de dar probabilidades del 100%."""
    games = prediction.prepare_games(_synthetic_league(noise=0.0, days=60))
    fitted = prediction.fit_ratings(games)
    assert fitted.sigma > 3.0


def test_a_team_with_few_games_is_shrunk_towards_average(league):
    """Un equipo nuevo que gana de 30 su único partido no sale como el mejor de la liga."""
    extra = pd.DataFrame([{
        "game_id": "new1", "game_date": dt.date(2026, 5, 20), "home_team_id": "nuevo", "away_team_id": "t6",
        "home_score": 110, "away_score": 80, "competition": "ACB",
    }])
    games = prediction.prepare_games(pd.concat([league[extra.columns], extra], ignore_index=True))
    fitted = prediction.fit_ratings(games)
    assert fitted.games("nuevo") == 1
    assert 0 < fitted.rating("nuevo") < 30 / 2
    assert fitted.rating("nuevo") < fitted.rating("t0")


def test_neutral_games_do_not_carry_home_court():
    """En sede neutral (Copa) la columna de campo es 0: si solo hubiera partidos neutrales, no hay campo que
    estimar y se queda en el prior (no aprende nada de ellos, ni siquiera un campo de 0)."""
    games = prediction.prepare_games(_synthetic_league(days=120, home=0.0, competition="Copa del Rey"))
    assert games["neutral"].all()
    assert prediction.fit_ratings(games).home_court == pytest.approx(prediction.HOME_PRIOR, abs=1e-6)


def test_home_court_does_not_swallow_the_margin_with_few_games():
    """Regresión: con el campo sin penalizar, UN partido ganado de 10 por el local daba "+10 por jugar en
    casa". Con el prior débil, pocas muestras dejan el campo cerca del prior."""
    one = pd.DataFrame([{
        "game_id": "a", "game_date": "2026-01-01", "home_team_id": "x", "away_team_id": "y",
        "home_score": 90, "away_score": 70, "competition": "ACB",
    }])
    fitted = prediction.fit_ratings(one)
    assert prediction.HOME_PRIOR < fitted.home_court < prediction.HOME_PRIOR + 2.0


def test_home_court_prior_barely_moves_a_full_season(league):
    """Con una temporada entera el prior no manda: el campo estimado sigue cerca del real."""
    fitted = prediction.fit_ratings(_synthetic_league(home=6.0))
    assert fitted.home_court > 4.5


def test_prepare_games_counts_rest_across_competitions():
    games = pd.DataFrame([
        {"game_id": "a", "game_date": "2026-01-01", "home_team_id": "x", "away_team_id": "y",
         "home_score": 80, "away_score": 70, "competition": "Euroliga"},
        {"game_id": "b", "game_date": "2026-01-03", "home_team_id": "z", "away_team_id": "x",
         "home_score": 75, "away_score": 77, "competition": "ACB"},
    ])
    prepared = prediction.prepare_games(games).set_index("game_id")
    assert pd.isna(prepared.loc["a", "home_rest"])
    assert prepared.loc["b", "away_rest"] == 2  # x: Euroliga el 1 → ACB el 3
    # z sin partido previo cuenta como descansado (tope); x con 2 días.
    assert prepared.loc["b", "rest_diff"] == prediction.REST_CAP_DAYS - 2
    assert prepared.loc["b", "margin"] == -2


# ---------- predicción ----------


def test_decomposition_sums_to_the_expected_margin(fit):
    pred = prediction.predict_matchup(fit, "t2", "t8", venue="away", own_rest=2, rival_rest=4)
    assert [c["key"] for c in pred.components] == ["rating", "home", "rest"]
    assert sum(c["points"] for c in pred.components) == pytest.approx(pred.expected_margin)
    by_key = {c["key"]: c["points"] for c in pred.components}
    assert by_key["home"] == pytest.approx(-fit.home_court)
    assert by_key["rest"] < 0  # llegamos con menos descanso
    assert by_key["rating"] == pytest.approx(fit.rating("t2") - fit.rating("t8"))


def test_venue_changes_only_the_home_piece(fit):
    home = prediction.predict_matchup(fit, "t3", "t4", venue="home")
    away = prediction.predict_matchup(fit, "t3", "t4", venue="away")
    neutral = prediction.predict_matchup(fit, "t3", "t4", venue="neutral")
    assert home.expected_margin - away.expected_margin == pytest.approx(2 * fit.home_court)
    assert neutral.components[1]["points"] == 0.0
    assert home.win_probability > neutral.win_probability > away.win_probability


def test_unknown_venue_is_rejected(fit):
    with pytest.raises(ValueError):
        prediction.predict_matchup(fit, "t0", "t1", venue="casa")


def test_win_probability_is_monotonic_and_symmetric():
    margins = np.linspace(-30, 30, 61)
    probs = [prediction.win_probability(m, 11.0) for m in margins]
    assert all(b > a for a, b in zip(probs, probs[1:]))
    assert prediction.win_probability(0.0, 11.0) == pytest.approx(0.5)
    assert prediction.win_probability(5.0, 11.0) + prediction.win_probability(-5.0, 11.0) == pytest.approx(1.0)


def test_explanation_reads_like_the_coach_would_say_it(fit):
    pred = prediction.predict_matchup(fit, "t0", "t5", venue="home", own_rest=2, rival_rest=4)
    lines = prediction.explain_components(pred, "Rival")
    assert any("por jugar en casa" in line for line in lines)
    assert any("llegamos con 2 días de descanso y ellos con 4 días" in line for line in lines)
    assert "," in prediction.fmt_points(2.14) and prediction.fmt_points(-1.5) == "−1,5"
    # Regresión: el signo sale del número que se enseña (nunca "−0,0").
    assert prediction.fmt_points(-0.04) == "+0,0"
    assert prediction.fmt_points(-0.06) == "−0,1"
    sentence = prediction.summary_sentence(pred, "Rival")
    assert sentence.startswith("Predicción del modelo: ")
    assert "% de victoria" in sentence


# ---------- backtest ----------


def test_backtest_beats_always_picking_the_home_team(league):
    bt = prediction.backtest(league)
    assert bt["n"] > 100
    assert bt["hit_rate"] > bt["home_hit_rate"]
    assert 5.0 < bt["mae"] < 15.0
    assert 0.0 < bt["brier"] < 0.25


def test_backtest_needs_enough_past_games(league):
    assert prediction.backtest(league, min_train_games=len(league) + 1) is None
    assert prediction.backtest(league.iloc[0:0]) is None


# ---------- consultas (seed real de `schema.sql`) ----------


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_season_game_results_filters_by_date(engine):
    all_games = queries_prediction.season_game_results(engine, 1)
    assert len(all_games) == 5
    before = queries_prediction.season_game_results(engine, 1, before=dt.date(2026, 1, 10))
    assert list(before["game_id"]) == ["g1", "g2"]


def test_rest_until_crosses_competitions(engine):
    # g4 (ACB, 15-ene) es el último de `bas` antes del 18-ene; g3 era Euroliga.
    assert queries_prediction.rest_until(engine, "bas", dt.date(2026, 1, 18)) == 3
    assert queries_prediction.rest_until(engine, "bas", dt.date(2025, 12, 1)) is None


def test_matchup_prediction_on_the_seed(engine):
    result = queries_prediction.matchup_prediction(engine, "bas", "val", 1, dt.date(2026, 2, 1), True, "ACB")
    pred = result["prediction"]
    assert result["venue"] == "home"
    assert sum(c["points"] for c in pred.components) == pytest.approx(pred.expected_margin)
    assert 0.0 < pred.win_probability < 1.0
    assert result["backtest"] is None  # 5 partidos: nada que probar hacia atrás
    assert set(result["ratings"]["team_id"]) >= {"bas", "val"}


def test_backtest_is_cached_per_season_and_date_not_per_rival(engine, monkeypatch):
    """Regresión de rendimiento: el backtest (un reajuste por fecha, ~1-2 s por temporada) iba dentro de la
    caché de `matchup_prediction`, cuya clave incluye rival y pista — se repetía para cada rival."""
    calls = []
    real = prediction.backtest
    monkeypatch.setattr(prediction, "backtest", lambda games, **kw: calls.append(len(games)) or real(games, **kw))
    day = dt.date(2026, 2, 1)
    queries_prediction.matchup_prediction(engine, "bas", "val", 1, day, True, "ACB")
    queries_prediction.matchup_prediction(engine, "bas", "val", 1, day, False, "ACB")
    queries_prediction.matchup_prediction(engine, "bas", "rm", 1, day, True, "ACB")
    assert len(calls) == 1


def test_matchup_prediction_is_neutral_in_the_cup(engine):
    result = queries_prediction.matchup_prediction(engine, "bas", "val", 1, dt.date(2026, 2, 1), True, "Copa del Rey")
    assert result["venue"] == "neutral"
    assert result["prediction"].components[1]["points"] == 0.0


def test_matchup_prediction_none_without_past_games(engine):
    assert queries_prediction.matchup_prediction(engine, "bas", "val", 1, dt.date(2025, 1, 1), True) is None


def test_upcoming_matchup_vs_finds_the_rival(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO upcoming_matchups (opponent_team_id, competition_id, match_date, is_home)"
            " VALUES ('val', 1, '2030-01-05', 0)"
        ))
    row = queries_prediction.upcoming_matchup_vs(engine, "val", dt.date(2029, 12, 1))
    assert row["competition"] == "ACB" and not row["is_home"]
    assert queries_prediction.upcoming_matchup_vs(engine, "val", dt.date(2030, 2, 1)) is None


# ---------- dossier .pptx ----------


def test_dossier_cover_carries_the_prediction_sentence(engine):
    """`generate_scouting_ppt` de punta a punta sobre el seed: la portada lleva la frase de la predicción."""
    import io

    from pptx import Presentation

    from app.reports import scouting_ppt

    data = scouting_ppt.generate_scouting_ppt(
        engine, rival_team_id="val", rival_name="Valencia Basket", is_home=True, competition="ACB",
        fecha="01 Feb 2026", own_team_id="bas", scouting_season_id=1, scouting_season_label="2025-2026",
        is_fallback_season=False, today=dt.date(2026, 1, 30), match_date=dt.date(2026, 2, 1),
    )
    cover = list(Presentation(io.BytesIO(data)).slides)[0]
    cover_text = " ".join(shape.text_frame.text for shape in cover.shapes if shape.has_text_frame)
    assert "Predicción del modelo" in cover_text
    assert "por jugar en casa" in cover_text
