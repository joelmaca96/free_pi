"""Tests de `app/analytics/signals.py` (propuesta 10, señales semanales).

Lógica pura sobre `pandas`/`numpy` —sin Streamlit ni base de datos—, mismo
criterio que `test_zone_matchup.py`: se prueban aquí las dos barreras
obligatorias del documento (corrección por comparaciones múltiples y tamaño
de efecto mínimo) y que cada familia de señal detecta lo que dice detectar,
no cualquier ruido con su misma forma.
"""
import pandas as pd
import pytest

from app.analytics import signals as sg


# --------------------------------------------------------------- estadística --


def test_two_proportion_test_finds_a_real_shift():
    """31% -> 44% con volumen de sobra (el ejemplo del propio documento, §1) es significativo."""
    diff_pp, p_value = sg.two_proportion_test(22, 50, 62, 200)  # 44% reciente vs 31% base
    assert diff_pp == pytest.approx(13.0, abs=0.5)
    assert p_value < 0.10


def test_two_proportion_test_without_attempts_is_a_no_op():
    assert sg.two_proportion_test(0, 0, 5, 10) == (0.0, 1.0)


def test_two_mean_test_needs_at_least_two_values_per_side():
    """Con un único partido a un lado no hay varianza que estimar: nunca puede ser "significativo"."""
    mean_r, mean_b, diff, p_value = sg.two_mean_test([30.0], [20.0, 21.0, 19.0])
    assert (mean_r, mean_b) == (30.0, 20.0)
    assert diff == pytest.approx(10.0)
    assert p_value == 1.0


def test_two_mean_test_finds_a_real_shift_with_enough_games():
    recent = [34.0, 35.0, 33.0, 36.0, 34.0]
    baseline = [20.0, 21.0, 19.0, 22.0, 20.0, 21.0, 19.0, 20.0]
    mean_r, mean_b, diff, p_value = sg.two_mean_test(recent, baseline)
    assert mean_r == pytest.approx(34.4)
    assert mean_b == pytest.approx(20.25)
    assert diff == pytest.approx(14.15)
    assert p_value < 0.01


def test_two_mean_test_ignores_nan_values():
    mean_r, _, _, _ = sg.two_mean_test([10.0, float("nan"), 12.0], [5.0, 6.0, 7.0])
    assert mean_r == pytest.approx(11.0)


def test_benjamini_hochberg_matches_the_textbook_procedure():
    """p = [0.01, 0.02, 0.20, 0.50] a q=0.10: los umbrales por rango son [0.025, 0.05, 0.075, 0.10] —
    0.01 y 0.02 pasan, 0.20 y 0.50 no (el rango 3 ya no cumple, y BH exige cumplir en el propio rango
    o uno posterior, no basta con que un rango ANTERIOR haya pasado)."""
    assert sg.benjamini_hochberg([0.01, 0.02, 0.20, 0.50], q=0.10) == [True, True, False, False]


def test_benjamini_hochberg_promotes_everything_up_to_the_largest_passing_rank():
    """p = [0.5, 0.001] (orden de entrada distinto al de rango): el de rango 2 (0.5) no pasa por sí
    solo, pero si el de rango 1 (0.001) sí, ambos deberían... aquí NO: 0.5 > (2/2)*0.10=0.10, así que
    solo pasa el de rango 1. Comprueba que el orden de ENTRADA no cambia el resultado."""
    assert sg.benjamini_hochberg([0.5, 0.001], q=0.10) == [False, True]


def test_benjamini_hochberg_empty_input():
    assert sg.benjamini_hochberg([]) == []


# ------------------------------------------------------------------- Signal --


def _signal(*, p_value, effect, min_effect=1.0, weight=10.0, family="player") -> sg.Signal:
    return sg.Signal(
        family=family, subject_id="p1", subject_name="Jugador Uno", metric="test_metric",
        recent_value=10.0, baseline_value=5.0, effect=effect, unit="min",
        n_recent=5, n_baseline=10, min_effect=min_effect, weight=weight, p_value=p_value,
        headline_template="{name}: {recent} vs {baseline}",
        context={"name": "Jugador Uno", "recent": 10.0, "baseline": 5.0},
        ask_question="¿Qué ha pasado?",
    )


def test_select_top_signals_applies_both_mandatory_gates():
    """Un candidato significativo pero con efecto minúsculo NO entra (§4: las dos barreras son
    obligatorias, no basta con pasar Benjamini-Hochberg)."""
    tiny_effect = _signal(p_value=0.001, effect=0.2, min_effect=5.0)
    real_signal = _signal(p_value=0.001, effect=6.0, min_effect=5.0)
    not_significant = _signal(p_value=0.9, effect=8.0, min_effect=5.0)

    survivors = sg.select_top_signals([tiny_effect, real_signal, not_significant])

    assert survivors == [real_signal]
    assert real_signal.headline == "Jugador Uno: 10.0 vs 5.0"
    assert "partidos" in real_signal.confidence.lower() or "calendario" in real_signal.confidence.lower()


def test_select_top_signals_ranks_by_relevance_not_by_p_value():
    """Un cambio grande en un jugador de pocos minutos pesa menos que uno mediano en un titular
    (§4: "ordenar por relevancia práctica (efecto × minutos), no por significación")."""
    bench_player = _signal(p_value=0.001, effect=10.0, min_effect=1.0, weight=5.0)   # relevancia 50
    starter = _signal(p_value=0.04, effect=6.0, min_effect=1.0, weight=30.0)          # relevancia 180

    survivors = sg.select_top_signals([bench_player, starter], q=0.10)

    assert survivors[0] is starter


def test_select_top_signals_caps_at_max_signals():
    candidates = [_signal(p_value=0.001, effect=float(i + 1), min_effect=0.5) for i in range(8)]
    assert len(sg.select_top_signals(candidates, max_signals=5)) == 5


def test_select_top_signals_empty_is_a_valid_answer():
    """"Sin cambios significativos esta semana" (§2) — lista vacía, no una excepción."""
    assert sg.select_top_signals([]) == []
    assert sg.select_top_signals([_signal(p_value=0.9, effect=1.0)]) == []


def test_deterministic_signals_skip_the_bh_correction():
    """Una señal `p_value=None` (carga) no entra en Benjamini-Hochberg, pero sí exige `min_effect`."""
    load_signal = _signal(family="load", p_value=None, effect=15.0, min_effect=0.0)
    survivors = sg.select_top_signals([load_signal])
    assert survivors == [load_signal]
    assert "calendario" in survivors[0].confidence.lower()


# --------------------------------------------------------------- detección: jugador --


def _player_log(rows) -> pd.DataFrame:
    """`(game_date, minutes, tov, pf, oreb, ftm, fta, tpm, tpa)` -> log de UN jugador."""
    return pd.DataFrame(
        [
            {
                "player_id": "howard", "player_name": "Marcus Howard", "game_id": f"g{i}",
                "game_date": date, "competition": "ACB", "minutes": minutes, "tov": tov, "pf": pf,
                "oreb": oreb, "ftm": ftm, "fta": fta, "tpm": tpm, "tpa": tpa,
            }
            for i, (date, minutes, tov, pf, oreb, ftm, fta, tpm, tpa) in enumerate(rows)
        ]
    )


def test_detect_player_signals_finds_a_shooting_change_with_volume():
    """El ejemplo del propio documento: triples de 31% a 44% en 5 partidos, con volumen de sobra."""
    baseline_rows = [(f"2026-10-{d:02d}", 28.0, 2, 2, 1, 1, 1, 3, 10) for d in range(1, 11)]  # 30% en 100 intentos
    recent_rows = [(f"2026-11-{d:02d}", 28.0, 2, 2, 1, 1, 1, 4, 9) for d in range(1, 6)]  # 44% en 45 intentos
    log = _player_log(baseline_rows + recent_rows)

    candidates = sg.detect_player_signals(log, last_n=5)
    fg3 = next(c for c in candidates if c.metric == "fg3_pct")

    assert fg3.recent_value == pytest.approx(44.4, abs=0.5)
    assert fg3.baseline_value == pytest.approx(30.0, abs=0.5)
    assert fg3.effect > 6.0  # supera el tamaño de efecto mínimo del documento
    assert fg3.p_value < 0.10


def test_detect_player_signals_skips_players_without_enough_baseline():
    """Un fichaje con dos partidos en toda la temporada no tiene "resto" con que compararse."""
    log = _player_log([("2026-11-01", 20.0, 1, 1, 1, 0, 0, 1, 2), ("2026-11-03", 22.0, 1, 1, 1, 0, 0, 1, 2)])
    assert sg.detect_player_signals(log, last_n=5) == []


def test_detect_player_signals_flags_a_role_change_in_minutes():
    baseline_rows = [(f"2026-10-{d:02d}", 12.0, 1, 1, 0, 0, 0, 0, 0) for d in range(1, 9)]
    recent_rows = [(f"2026-11-{d:02d}", 24.0, 1, 1, 0, 0, 0, 0, 0) for d in range(1, 6)]
    log = _player_log(baseline_rows + recent_rows)

    candidates = sg.detect_player_signals(log, last_n=5)
    minutes_signal = next(c for c in candidates if c.metric == "minutes")

    assert minutes_signal.effect == pytest.approx(12.0)
    assert minutes_signal.passes_effect()


def test_detect_player_signals_notes_when_every_recent_game_is_the_same_competition():
    baseline_rows = [(f"2026-10-{d:02d}", 20.0, 1, 1, 1, 0, 0, 1, 3) for d in range(1, 9)]
    recent_rows = [(f"2026-11-{d:02d}", 20.0, 1, 1, 1, 0, 0, 1, 3) for d in range(1, 6)]
    log = _player_log(baseline_rows + recent_rows)
    log.loc[log["game_date"] >= "2026-11-01", "competition"] = "Euroliga"

    candidates = sg.detect_player_signals(log, last_n=5)
    assert candidates and all(c.extra_note and "Euroliga" in c.extra_note for c in candidates)


# ------------------------------------------------------------------ detección: equipo --


def _team_log(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"game_id": f"g{i}", "game_date": date, "competition": "ACB", "efg_pct": efg,
             "tov_pct": tov, "orb_pct": orb, "ft_rate": ft, "pace": pace}
            for i, (date, efg, tov, orb, ft, pace) in enumerate(rows)
        ]
    )


def test_detect_team_signals_finds_a_shooting_swing():
    baseline_rows = [(f"2026-10-{d:02d}", 48.0, 13.0, 25.0, 22.0, 70.0) for d in range(1, 9)]
    recent_rows = [(f"2026-11-{d:02d}", 60.0, 13.0, 25.0, 22.0, 70.0) for d in range(1, 6)]
    log = _team_log(baseline_rows + recent_rows)

    candidates = sg.detect_team_signals(log, team_id="bas", team_name="Baskonia", last_n=5)
    efg_signal = next(c for c in candidates if c.metric == "efg_pct")

    assert efg_signal.effect == pytest.approx(12.0)
    assert efg_signal.subject_name == "Baskonia"
    assert efg_signal.weight == sg.TEAM_SIGNAL_WEIGHT


def test_detect_team_signals_empty_without_enough_games():
    assert sg.detect_team_signals(_team_log([("2026-11-01", 50.0, 12.0, 25.0, 20.0, 70.0)]), team_id="bas", team_name="Baskonia") == []


# ------------------------------------------------------ detección: equipo por zona --


def _zone_log(rows) -> pd.DataFrame:
    """`(game_id, game_date, zone_id, zone_label, shots, made)` -> log de zonas de UN equipo.

    `located` siempre 1 (mismo criterio que `shot_quality.split_usable`: sin
    coordenada no hay zona que atribuir, y `detect_team_zone_signals` ya
    descarta `located=0` antes de nada)."""
    return pd.DataFrame(
        [
            {
                "game_id": game_id, "game_date": date, "competition": "ACB",
                "zone_id": zone_id, "zone_label": zone_label, "located": 1,
                "shots": shots, "made": made,
            }
            for game_id, date, zone_id, zone_label, shots, made in rows
        ]
    )


def test_detect_team_zone_signals_finds_a_shot_selection_shift():
    """El equipo pasaba la mitad de sus tiros por la pintura y ahora casi todos: un cambio de
    patrón de ataque real, no ruido de un par de tiros."""
    rows = []
    for i in range(1, 9):  # línea base: 8 partidos, reparto 50/50 entre pintura y triple exterior
        rows.append((f"g{i}", f"2026-10-{i:02d}", 1, "Pintura", 10, 5))
        rows.append((f"g{i}", f"2026-10-{i:02d}", 6, "Triple exterior", 10, 4))
    for i in range(1, 6):  # recientes: 5 partidos, casi todo pintura
        rows.append((f"g1{i}", f"2026-11-{i:02d}", 1, "Pintura", 18, 9))
        rows.append((f"g1{i}", f"2026-11-{i:02d}", 6, "Triple exterior", 2, 1))
    log = _zone_log(rows)

    candidates = sg.detect_team_zone_signals(log, team_id="bas", team_name="Baskonia", last_n=5)
    pintura = next(c for c in candidates if "Pintura" in c.context["label"])

    assert pintura.baseline_value == pytest.approx(50.0, abs=1.0)
    assert pintura.recent_value == pytest.approx(90.0, abs=1.0)
    assert pintura.effect > sg.TEAM_ZONE_MIN_EFFECT_PP
    assert pintura.p_value < 0.10
    assert pintura.weight == sg.TEAM_SIGNAL_WEIGHT
    assert pintura.family == "team"


def test_detect_team_zone_signals_ignores_zones_below_the_minimum_attempts():
    rows = [(f"g{i}", f"2026-10-{i:02d}", 4, "Triple esquina izq.", 1, 1) for i in range(1, 14)]
    log = _zone_log(rows)
    candidates = sg.detect_team_zone_signals(log, team_id="bas", team_name="Baskonia", last_n=5)
    assert candidates == []  # 13 intentos en total, por debajo de TEAM_ZONE_MIN_ATTEMPTS


def test_detect_team_zone_signals_merges_zones_folded_by_zone_merges():
    """"Línea de fondo" (13) se funde en "Pintura" (1, `shot_quality.ZONE_MERGES`) antes de contar,
    igual que en `shot_quality`/`zone_matchup` — dos filas con `zone_id` distinto, una sola señal."""
    rows = []
    for i in range(1, 9):
        rows.append((f"g{i}", f"2026-10-{i:02d}", 1, "Pintura", 8, 4))
        rows.append((f"g{i}", f"2026-10-{i:02d}", 13, "Línea de fondo", 2, 1))
    for i in range(1, 6):
        rows.append((f"g1{i}", f"2026-11-{i:02d}", 1, "Pintura", 18, 9))
    log = _zone_log(rows)

    candidates = sg.detect_team_zone_signals(log, team_id="bas", team_name="Baskonia", last_n=5)
    assert sum(1 for c in candidates if "Pintura" in c.context["label"]) == 1


def test_detect_team_zone_signals_empty_without_enough_games():
    log = _zone_log([("g1", "2026-11-01", 1, "Pintura", 10, 5)])
    assert sg.detect_team_zone_signals(log, team_id="bas", team_name="Baskonia") == []


def test_detect_team_zone_signals_empty_dataframe():
    assert sg.detect_team_zone_signals(pd.DataFrame(), team_id="bas", team_name="Baskonia") == []


# --------------------------------------------------------------- detección: rotación --


def _pair_row(**overrides) -> pd.DataFrame:
    base = {
        "player_ids": "howard,kotsar", "jugadores": "Marcus Howard · Rytis Kotsar",
        "recent_minutes": 60.0, "recent_share": 60.0, "recent_seconds": 3600.0, "recent_total_seconds": 6000.0,
        "n_recent_games": 5,
        "baseline_minutes": 40.0, "baseline_share": 20.0, "baseline_seconds": 2400.0, "baseline_total_seconds": 12000.0,
        "n_baseline_games": 8,
    }
    base.update(overrides)
    return pd.DataFrame([base])


def test_detect_rotation_signals_flags_a_pair_gaining_weight():
    candidates = sg.detect_rotation_signals(_pair_row())
    assert len(candidates) == 1
    signal = candidates[0]
    assert signal.effect == pytest.approx(40.0)  # 60% - 20%
    assert signal.family == "rotation"
    assert signal.p_value < 0.01


def test_detect_rotation_signals_skips_pairs_with_too_little_recent_court_time():
    candidates = sg.detect_rotation_signals(_pair_row(recent_minutes=2.0, recent_seconds=120.0))
    assert candidates == []


# ------------------------------------------------------------------- detección: carga --


def test_detect_load_signals_flags_players_over_the_alert_threshold():
    rolling = pd.DataFrame([
        {"player_id": "howard", "player_name": "Marcus Howard", "rolling_minutes": 160.0, "games_in_window": 3},
        {"player_id": "kotsar", "player_name": "Rytis Kotsar", "rolling_minutes": 90.0, "games_in_window": 3},
    ])
    candidates = sg.detect_load_signals(rolling)
    assert len(candidates) == 1
    assert candidates[0].subject_name == "Marcus Howard"
    assert candidates[0].p_value is None
    assert candidates[0].effect == pytest.approx(20.0)


def test_detect_load_signals_empty_dataframe():
    assert sg.detect_load_signals(pd.DataFrame()) == []


def test_all_candidates_merges_the_four_families():
    load = sg.detect_load_signals(pd.DataFrame([
        {"player_id": "howard", "player_name": "Marcus Howard", "rolling_minutes": 200.0, "games_in_window": 3},
    ]))
    combined = sg.all_candidates(load=load)
    assert combined == load
