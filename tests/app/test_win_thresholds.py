"""Tests de `app/analytics/win_thresholds.py` (propuesta 09, umbrales de victoria).

Lógica pura sobre `pandas`/`numpy` — sin Streamlit ni base de datos, mismo
criterio que `test_shot_quality.py`/`test_zone_matchup.py`: se prueban aquí
las decisiones que hacen que un umbral sea un umbral de verdad y no ruido con
forma de tabla — la muestra mínima por lado, la dirección de la batalla, la
diversidad forzada del panel y la derivación del ajuste por rival.
"""
import numpy as np
import pandas as pd
import pytest

from app.analytics import win_thresholds as wt


# --------------------------------------------------------------- barrido de umbrales --


def test_sweep_threshold_finds_the_split_with_the_largest_win_pct_gap():
    diff = pd.Series(range(80))
    win = pd.Series([1 if v >= 40 else 0 for v in diff])

    result = wt.sweep_threshold(diff, win, min_side=30)

    assert result["threshold"] == 40
    assert result["win_pct_meets"] == pytest.approx(100.0)
    assert result["win_pct_miss"] == pytest.approx(0.0)
    assert result["n_meets"] == 40
    assert result["n_miss"] == 40


def test_sweep_threshold_requires_both_sides_to_meet_the_minimum():
    diff = pd.Series(range(20))  # solo 20 partidos en total
    win = pd.Series([1 if v >= 10 else 0 for v in diff])

    assert wt.sweep_threshold(diff, win, min_side=30) is None


def test_sweep_threshold_rejects_the_wrong_direction():
    """Ganar más la batalla asociado a perder MÁS no es un umbral, es la señal al revés."""
    diff = pd.Series(range(80))
    win = pd.Series([0 if v >= 40 else 1 for v in diff])  # invertido

    assert wt.sweep_threshold(diff, win, min_side=30) is None


def test_sweep_threshold_of_empty_input_is_none():
    assert wt.sweep_threshold(pd.Series(dtype=float), pd.Series(dtype=float)) is None


def test_sweep_threshold_prefers_a_balanced_split_over_a_thin_extreme_one():
    """Verificado en vivo contra `data/baskonia.db`: sin la regularización de
    `SEPARATION_SHRINK_K`, "rebote ofensivo" elegía "al menos 53%" — el pico de la
    temporada, con solo 36 partidos de 1.474, no un objetivo de verdad. Aquí en
    pequeño, con solo tres valores de `diff` para poder controlar los dos únicos
    cortes posibles a mano: el corte fino (`diff == 2`, exactamente los 30 partidos
    mínimos, 100% de victorias) tiene MÁS separación en crudo (80 puntos) que el
    corte que reparte la muestra (`diff >= 1`, 230 partidos, 56,5%) — pero, tras
    regularizar por tamaño del lado pequeño, gana el que reparte."""
    diff = pd.Series([0] * 300 + [1] * 200 + [2] * 30)
    win = pd.Series([0] * 300 + [1] * 100 + [0] * 100 + [1] * 30)

    result = wt.sweep_threshold(diff, win, min_side=30)

    assert result["threshold"] == 1
    assert result["n_meets"] == 230
    assert result["separation"] == pytest.approx(56.5217, abs=1e-3)


def test_sweep_threshold_falls_back_to_raw_separation_without_shrink():
    """Con `shrink_k=0` la regularización desaparece y gana el corte fino de más
    arriba — confirma que es la regularización, y no otra cosa, la que decide."""
    diff = pd.Series([0] * 300 + [1] * 200 + [2] * 30)
    win = pd.Series([0] * 300 + [1] * 100 + [0] * 100 + [1] * 30)

    result = wt.sweep_threshold(diff, win, min_side=30, shrink_k=0)

    assert result["threshold"] == 2
    assert result["n_meets"] == 30


# --------------------------------------------------------------- correlación (§1) --


def test_factor_correlations_reads_the_battle_not_the_raw_percentage():
    df = pd.DataFrame({
        "efg_pct": [51.0, 49.0, 52.0, 48.0],
        "opp_efg_pct": [50.0, 50.0, 50.0, 50.0],
        "ft_rate": [10.0] * 4, "opp_ft_rate": [10.0] * 4,
        "orb_pct": [10.0] * 4, "opp_orb_pct": [10.0] * 4,
        "tov_pct": [10.0] * 4, "opp_tov_pct": [10.0] * 4,
        "team_id": ["a"] * 4,
        "win": [1, 0, 1, 0],
    })

    table = wt.factor_correlations(df)
    efg_row = table.set_index("key").loc["efg_pct"]

    # Batalla = efg_pct - opp_efg_pct: [1, -1, 2, -2]; favorable (>0) en las
    # filas 0 y 2, las dos ganadas -> 100% de victorias cuando se gana la batalla.
    assert efg_row["n_favorable"] == 2
    assert efg_row["win_pct_when_favorable"] == pytest.approx(100.0)
    assert efg_row["correlation"] > 0.9


def test_factor_correlations_of_missing_columns_raises():
    with pytest.raises(ValueError, match="opp_efg_pct"):
        wt.factor_correlations(pd.DataFrame({"win": [1], "team_id": ["a"]}))


# --------------------------------------------------------------- panel de objetivos (§2a) --


def _toy_league(n=200, seed=0) -> pd.DataFrame:
    """Liga de juguete con separación clara y en la dirección correcta en los 4 factores."""
    rng = np.random.default_rng(seed)
    efg_diff = rng.normal(0, 5, n)
    ft_diff = rng.normal(0, 3, n)
    orb_diff = rng.normal(0, 4, n)
    tov_diff = rng.normal(0, 2, n)  # favorable = opp_tov - own_tov

    score = efg_diff + 0.3 * ft_diff + 0.3 * orb_diff + 0.3 * tov_diff
    win = (score > np.median(score)).astype(int)

    return pd.DataFrame({
        "team_id": ["a"] * n,
        "win": win,
        "efg_pct": 45 + efg_diff, "opp_efg_pct": 45.0,
        "ft_rate": 25 + ft_diff, "opp_ft_rate": 25.0,
        "orb_pct": 27 + orb_diff, "opp_orb_pct": 27.0,
        "tov_pct": 14 - tov_diff, "opp_tov_pct": 14.0,
    })


def test_league_objectives_returns_cards_sorted_by_separation():
    cards = wt.league_objectives(_toy_league(), min_side=30)

    assert cards  # al menos una batalla separa con esta muestra
    separations = [c["separation"] for c in cards]
    assert separations == sorted(separations, reverse=True)
    for card in cards:
        assert card["display_threshold"] is not None


def test_league_objectives_enforces_the_min_side_sample():
    cards = wt.league_objectives(_toy_league(n=40), min_side=1000)
    assert cards == []


def test_select_diverse_swaps_a_shooting_card_for_a_process_card_when_short():
    """Con `max_cards=2` y las dos mejores siendo de acierto, la regla de §5 cambia la
    peor de las dos por la mejor de proceso que quedó fuera."""
    candidates = [
        {"key": "shoot_a", "category": "shooting", "separation": 30.0},
        {"key": "shoot_b", "category": "shooting", "separation": 25.0},
        {"key": "process_a", "category": "process", "separation": 20.0},
        {"key": "process_b", "category": "process", "separation": 10.0},
    ]

    chosen = wt._select_diverse(candidates, max_cards=2, min_non_shooting=2)

    assert {c["key"] for c in chosen} == {"process_a", "process_b"}


def test_select_diverse_leaves_a_short_list_untouched():
    candidates = [{"key": "only_one", "category": "shooting", "separation": 5.0}]
    assert wt._select_diverse(candidates, max_cards=3, min_non_shooting=2) == candidates


# --------------------------------------------------------------- ajuste por rival (§4) --


def test_rival_concession_averages_reads_what_the_opponent_did_against_that_team():
    df = pd.DataFrame({
        "team_id": ["rival", "rival", "other"],
        "opp_orb_pct": [30.0, 34.0, 99.0],  # el 99 es de OTRO equipo, no debe entrar
    })

    averages = wt.rival_concession_averages(df, "rival", factors=[
        {"key": "orb_pct", "opp_col": "opp_orb_pct"},
    ])

    assert averages["orb_pct"] == pytest.approx(32.0)
    assert averages["n_games"] == 2


def test_rival_concession_averages_of_a_team_with_no_rows_is_empty():
    df = pd.DataFrame({"team_id": ["a"], "opp_orb_pct": [30.0]})
    assert wt.rival_concession_averages(df, "nobody") == {}


def test_rival_adjusted_card_shifts_the_league_threshold_by_the_rival_delta():
    """Derivación de §4: ajustado = umbral de liga + (concesión del rival − concesión de la liga)."""
    card = {"key": "orb_pct", "higher_is_better": True, "display_threshold": 30.0}
    rival_avg = {"orb_pct": 35.0}
    league_avg = {"orb_pct": 28.0}

    adjusted = wt.rival_adjusted_card(card, rival_avg, league_avg)

    assert adjusted["adjusted_threshold"] == pytest.approx(37.0)
    assert adjusted["rival_shift"] == pytest.approx(7.0)
    assert adjusted["is_rival_adjusted"] is True


def test_rival_adjusted_card_without_a_rival_profile_falls_back_to_the_league_threshold():
    card = {"key": "orb_pct", "higher_is_better": True, "display_threshold": 30.0}

    adjusted = wt.rival_adjusted_card(card, {}, {})

    assert adjusted["adjusted_threshold"] == 30.0
    assert adjusted["is_rival_adjusted"] is False


# --------------------------------------------------------------- comprobación por partido (§2b) --


def test_game_card_results_flags_what_was_met():
    cards = [
        {"key": "orb_pct", "label": "rebote ofensivo", "own_col": "orb_pct",
         "higher_is_better": True, "display_threshold": 30.0},
        {"key": "tov_pct", "label": "pérdidas", "own_col": "tov_pct",
         "higher_is_better": False, "display_threshold": 12.0},
    ]
    game_row = pd.Series({"orb_pct": 32.0, "tov_pct": 15.0})

    results = wt.game_card_results(game_row, cards)

    assert results[0]["met"] is True   # 32 >= 30
    assert results[1]["met"] is False  # 15 no es <= 12


def test_game_card_results_of_a_missing_value_is_none_not_false():
    cards = [{"key": "orb_pct", "own_col": "orb_pct", "higher_is_better": True, "display_threshold": 30.0}]
    game_row = pd.Series({"orb_pct": None})

    assert wt.game_card_results(game_row, cards)[0]["met"] is None


# --------------------------------------------------------------- texto de tarjeta --


def test_card_value_text_reads_the_direction():
    higher = {"higher_is_better": True, "display_threshold": 32.04}
    lower = {"higher_is_better": False, "display_threshold": 11.0}

    assert wt.card_value_text(higher) == "Al menos 32,0%"
    assert wt.card_value_text(lower) == "Como mucho 11,0%"


def test_card_value_text_prefers_the_rival_adjusted_threshold_when_present():
    card = {"higher_is_better": True, "display_threshold": 30.0, "adjusted_threshold": 37.0}
    assert wt.card_value_text(card) == "Al menos 37,0%"


def test_card_value_text_without_a_threshold_says_so():
    card = {"higher_is_better": True, "display_threshold": None}
    assert wt.card_value_text(card) == "Sin referencia suficiente"


def test_card_headline_keeps_acronyms_intact():
    """Ninguna sigla de la etiqueta se pierde al construir la frase (ver el aviso sobre
    `str.capitalize()` en `shot_quality.verdict`, mismo motivo aquí)."""
    card = {"label": "acierto efectivo (eFG%)", "higher_is_better": True, "display_threshold": 54.0}
    assert wt.card_headline(card) == "Al menos 54,0% de acierto efectivo (eFG%)"


def test_format_pct_uses_a_comma_and_handles_missing_values():
    assert wt.format_pct(32.0) == "32,0%"
    assert wt.format_pct(None) == "—"
    assert wt.format_pct(float("nan")) == "—"


def test_objectives_caveat_text_defaults_to_both_competitions():
    assert "ACB y Euroliga juntas" in wt.objectives_caveat_text()
    assert wt.objectives_caveat_text() == wt.OBJECTIVES_CAVEAT


def test_objectives_caveat_text_names_the_single_competition_when_scoped():
    """§5 del documento: "conviene poder separarlas" — el aviso no puede seguir diciendo
    "ACB y Euroliga juntas" cuando el panel se acotó a una sola."""
    text = wt.objectives_caveat_text("ACB")
    assert "partidos de ACB" in text
    assert "ACB y Euroliga juntas" not in text


# --------------------------------------------------------------- v2: modelo --


def _separable_by_efg_only(n=300) -> pd.DataFrame:
    """Solo `efg_pct` lleva señal; los otros tres factores son constantes (diff = 0 siempre)."""
    efg_diff = np.linspace(-20, 20, n)
    win = (efg_diff > 0).astype(int)
    return pd.DataFrame({
        "team_id": ["a"] * n, "win": win,
        "efg_pct": efg_diff, "opp_efg_pct": 0.0,
        "ft_rate": 10.0, "opp_ft_rate": 10.0,
        "orb_pct": 10.0, "opp_orb_pct": 10.0,
        "tov_pct": 10.0, "opp_tov_pct": 10.0,
    })


def test_logistic_model_predicts_higher_probability_for_a_bigger_favorable_diff():
    model = wt.fit_logistic_model(_separable_by_efg_only())

    high = wt.predict_win_probability(model, {"efg_pct": 15, "ft_rate": 0, "orb_pct": 0, "tov_pct": 0})
    low = wt.predict_win_probability(model, {"efg_pct": -15, "ft_rate": 0, "orb_pct": 0, "tov_pct": 0})

    assert high > 0.5 > low


def test_factor_importance_puts_all_the_weight_on_the_only_variable_with_signal():
    model = wt.fit_logistic_model(_separable_by_efg_only())

    importance = wt.factor_importance(model)

    assert importance.iloc[0]["key"] == "efg_pct"
    assert importance.iloc[0]["share_pct"] == pytest.approx(100.0)
    others = importance.loc[importance["key"] != "efg_pct", "weight"]
    assert (others == 0.0).all()


def test_fit_logistic_model_of_empty_input_is_none():
    df = pd.DataFrame({
        "team_id": [], "win": [],
        "efg_pct": [], "opp_efg_pct": [], "ft_rate": [], "opp_ft_rate": [],
        "orb_pct": [], "opp_orb_pct": [], "tov_pct": [], "opp_tov_pct": [],
    })
    assert wt.fit_logistic_model(df) is None
