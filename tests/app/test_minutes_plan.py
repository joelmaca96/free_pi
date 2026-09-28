"""Planificador de minutos con carga (`app/analytics/minutes_plan.py`, propuesta 17)."""
import datetime as dt
import itertools

import numpy as np
import pandas as pd
import pytest

from app.analytics import minutes_plan as mp


def _roster(**overrides) -> pd.DataFrame:
    """Diez jugadores con RAPM decreciente, dos bases y dos pívots, todos disponibles, tope 32."""
    rows = [
        ("howard", "Howard", "Base", 4.0),
        ("moneke", "Moneke", "Ala-pívot", 3.0),
        ("nikos", "Nikos", "Alero", 2.5),
        ("lutse", "Lutse", "Alero", 2.0),
        ("sedek", "Sedekerskis", "Ala-pívot", 1.5),
        ("kotsar", "Kotsar", "Pívot", 1.0),
        ("codi", "Codi", "Base", 0.5),
        ("costello", "Costello", "Pívot", -0.5),
        ("forrest", "Forrest", "Escolta", -1.0),
        ("rookie", "Rookie", "Alero", -2.0),
    ]
    df = pd.DataFrame(rows, columns=["player_id", "player_name", "position", "rapm"])
    df["available"] = True
    df["min_minutes"] = 0.0
    df["max_minutes"] = 32.0
    df["recent_avg_minutes"] = 20.0
    for column, value in overrides.items():
        df[column] = value
    return df


def _minutes(result) -> dict:
    return dict(zip(result["table"]["player_id"], result["table"]["planned_minutes"]))


# ------------------------------------------------------------ invariantes --


def test_plan_hands_out_exactly_200_minutes_within_caps():
    result = mp.plan_minutes(_roster())

    assert result["feasible"], result["message"]
    table = result["table"]
    assert table["planned_minutes"].sum() == pytest.approx(200.0)
    assert (table["planned_minutes"] <= table["max_minutes"] + 1e-9).all()
    assert (table["planned_minutes"] >= 0).all()


def test_unavailable_players_get_zero_and_say_why():
    roster = _roster()
    roster.loc[roster["player_id"] == "howard", "available"] = False
    roster.loc[roster["player_id"] == "howard", "min_minutes"] = 20.0  # se ignora: no está

    result = mp.plan_minutes(roster)

    row = result["table"].set_index("player_id").loc["howard"]
    assert row["planned_minutes"] == 0.0
    assert row["binding"] == mp.BIND_UNAVAILABLE
    assert result["table"]["planned_minutes"].sum() == pytest.approx(200.0)


def test_minimums_are_respected_even_for_a_bad_player():
    roster = _roster()
    roster.loc[roster["player_id"] == "rookie", "min_minutes"] = 8.0

    result = mp.plan_minutes(roster)

    row = result["table"].set_index("player_id").loc["rookie"]
    assert row["planned_minutes"] == pytest.approx(8.0)
    assert row["binding"] == mp.BIND_MIN


def test_position_floors_force_a_base_and_a_centre_on_court():
    """Sin suelo, los dos pívots se quedarían cortos; con él, suman 40 minutos."""
    roster = _roster()
    roster.loc[roster["player_id"] == "kotsar", "max_minutes"] = 10.0

    free = _minutes(mp.plan_minutes(roster))
    assert free["kotsar"] + free["costello"] < 40.0  # premisa del test

    result = mp.plan_minutes(roster, position_floors=mp.DEFAULT_POSITION_FLOORS)
    planned = _minutes(result)
    assert planned["kotsar"] + planned["costello"] == pytest.approx(40.0)
    assert planned["howard"] + planned["codi"] >= 40.0 - 1e-9
    assert sum(planned.values()) == pytest.approx(200.0)
    costello = result["table"].set_index("player_id").loc["costello"]
    assert costello["binding"].startswith(mp.BIND_FLOOR)
    # Kotsar llega a su tope (10) y Costello completa el suelo.
    assert planned["kotsar"] == pytest.approx(10.0)


def test_ala_pivot_does_not_count_as_pivot():
    """Misma regla que `impact._position_ok`: igualdad exacta de etiqueta."""
    roster = _roster()
    roster.loc[roster["position"] == "Pívot", "available"] = False

    result = mp.plan_minutes(roster, position_floors={"Pívot": 40.0})

    assert not result["feasible"]
    assert "Pívot" in result["message"]
    assert result["table"]["planned_minutes"].isna().all()


def test_infeasible_when_caps_do_not_reach_200():
    roster = _roster(max_minutes=19.0)  # 10 × 19 = 190

    result = mp.plan_minutes(roster)

    assert not result["feasible"]
    assert "190" in result["message"]
    assert np.isnan(result["projected_margin"])


def test_infeasible_when_minimum_exceeds_cap():
    roster = _roster()
    roster.loc[roster["player_id"] == "howard", ["min_minutes", "max_minutes"]] = [30.0, 24.0]

    result = mp.plan_minutes(roster)

    assert not result["feasible"]
    assert "Howard" in result["message"]


def test_caps_above_a_full_game_are_clipped_to_40():
    roster = _roster(max_minutes=60.0)

    planned = _minutes(mp.plan_minutes(roster))

    assert max(planned.values()) == pytest.approx(40.0)
    assert sum(planned.values()) == pytest.approx(200.0)


def test_projected_margin_is_rapm_weighted_by_minutes_over_40():
    result = mp.plan_minutes(_roster())
    table = result["table"]

    expected = float((table["rapm"] * table["planned_minutes"]).sum() / 40.0)
    assert result["projected_margin"] == pytest.approx(expected)
    # Tope 32: los seis mejores a 32 y 8 para el séptimo.
    assert result["projected_margin"] == pytest.approx((4 + 3 + 2.5 + 2 + 1.5 + 1) * 32 / 40 + 0.5 * 8 / 40)


def test_explanation_reads_recent_to_planned_with_the_binding_reason():
    roster = _roster()
    roster.loc[roster["player_id"] == "howard", ["max_minutes", "recent_avg_minutes"]] = [26.0, 31.0]
    roster["cap_source"] = "general"
    roster["cap_detail"] = ""
    roster.loc[roster["player_id"] == "howard", ["cap_source", "cap_detail"]] = ["carga", "152 min en 7 días"]

    table = mp.plan_minutes(roster)["table"].set_index("player_id")

    assert table.loc["howard", "explanation"] == "Howard: 31 → 26 min (tope por carga: 152 min en 7 días)"
    assert table.loc["howard", "delta"] == pytest.approx(-5.0)
    assert table.loc["rookie", "binding"] == mp.BIND_OUT
    assert table.loc["kotsar", "binding"] == mp.BIND_CAP_GENERAL
    assert table.loc["codi", "binding"] == mp.BIND_MARGINAL


# ---------------------------------------------------------- optimalidad --


def _brute_force(values, lower, upper, groups, floors, total):
    best = None
    ranges = [range(int(lo), int(hi) + 1) for lo, hi in zip(lower, upper)]
    for combo in itertools.product(*ranges):
        if sum(combo) != total:
            continue
        ok = all(
            sum(m for m, g in zip(combo, groups) if g == label) >= floor for label, floor in floors.items()
        )
        if not ok:
            continue
        value = sum(v * m for v, m in zip(values, combo))
        best = value if best is None else max(best, value)
    return best


@pytest.mark.parametrize("seed", range(200))
def test_greedy_matches_brute_force_on_small_cases(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(3, 6))
    total = int(rng.integers(4, 12))
    values = rng.normal(0, 2, n).round(1)
    lower = rng.integers(0, 2, n)
    upper = lower + rng.integers(0, 6, n)
    groups = list(rng.choice(["A", "B", None], n))
    floors = {"A": int(rng.integers(0, 5)), "B": int(rng.integers(0, 4))}

    expected = _brute_force(values, lower, upper, groups, floors, total)
    if expected is None:
        with pytest.raises(mp.InfeasiblePlan):
            mp.optimise_minutes(values, lower, upper, groups=groups, floors=floors, total=total)
        return
    minutes, _ = mp.optimise_minutes(values, lower, upper, groups=groups, floors=floors, total=total)
    assert minutes.sum() == pytest.approx(total)
    assert (minutes >= lower - 1e-9).all() and (minutes <= upper + 1e-9).all()
    for label, floor in floors.items():
        assert sum(m for m, g in zip(minutes, groups) if g == label) >= floor - 1e-9
    assert float(np.dot(values, minutes)) == pytest.approx(expected)
    # Con datos enteros, solución entera.
    assert np.allclose(minutes, np.round(minutes))


# ------------------------------------------------------------- prudente --


def test_risk_adjusted_rapm_penalises_short_samples_more():
    adjusted = mp.risk_adjusted_rapm([1.0, 1.0], [200.0, 3000.0])

    assert adjusted[0] < adjusted[1] < 1.0
    assert mp.risk_adjusted_rapm([1.0], [500.0], kappa=0.0)[0] == pytest.approx(1.0)


def test_prudent_plan_prefers_the_proven_player_on_a_near_tie():
    roster = _roster()
    roster["fit_minutes"] = 2500.0
    # Rookie casi igual de bueno que Kotsar según la media, pero con 150 minutos.
    roster.loc[roster["player_id"] == "rookie", ["rapm", "fit_minutes"]] = [1.1, 150.0]
    roster["prudent"] = mp.risk_adjusted_rapm(roster["rapm"], roster["fit_minutes"])

    expected = _minutes(mp.plan_minutes(roster))
    prudent_result = mp.plan_minutes(roster, value_column="prudent")
    prudent = _minutes(prudent_result)

    assert (expected["rookie"], expected["kotsar"]) == (32.0, 8.0)
    assert (prudent["kotsar"], prudent["rookie"]) == (32.0, 8.0)
    # El margen se mide siempre con el RAPM: el prudente espera algo menos.
    assert prudent_result["projected_margin"] <= mp.plan_minutes(roster)["projected_margin"]


# ----------------------------------------------------------------- carga --


def _log():
    """Dos jugadores, cuatro partidos del equipo en 9 días; 'b' no jugó el último."""
    dates = ["2026-02-01", "2026-02-04", "2026-02-06", "2026-02-08"]
    rows = []
    for date, (ma, mb) in zip(dates, [(30, 20), (36, 22), (38, 25), (40, None)]):
        rows.append(("a", date, ma))
        rows.append(("b", date, mb))
    return pd.DataFrame(rows, columns=["player_id", "game_date", "minutes"])


def test_recent_summary_uses_a_calendar_window_before_the_game():
    summary = mp.recent_minutes_summary(_log(), dt.date(2026, 2, 10), days=7).set_index("player_id")

    # Ventana (3 feb, 10 feb): partidos del 4, 6 y 8.
    assert summary.loc["a", "load_minutes"] == pytest.approx(36 + 38 + 40)
    assert summary.loc["a", "games_in_window"] == 3
    assert summary.loc["b", "load_minutes"] == pytest.approx(22 + 25)
    assert summary.loc["a", "last_game_minutes"] == 40
    assert summary.loc["b", "last_game_minutes"] == 0
    # Media reciente: solo partidos jugados.
    assert summary.loc["b", "recent_avg_minutes"] == pytest.approx((20 + 22 + 25) / 3)


def test_recent_summary_ignores_the_game_being_planned_and_later_ones():
    summary = mp.recent_minutes_summary(_log(), dt.date(2026, 2, 6), days=7).set_index("player_id")

    assert summary.loc["a", "load_minutes"] == pytest.approx(30 + 36)


def test_load_caps_follow_the_rules():
    summary = mp.recent_minutes_summary(_log(), dt.date(2026, 2, 10), days=7)
    rest = mp.rest_days_before(_log(), dt.date(2026, 2, 10))
    assert rest == 2

    caps = mp.load_caps(summary, rest_days=rest).set_index("player_id")

    # 'a': 114 min en 7 días → le quedan 26 hasta el aviso de 140.
    assert caps.loc["a", "max_minutes"] == 26.0
    assert caps.loc["a", "cap_source"] == "carga"
    assert caps.loc["a", "cap_detail"] == "114 min en 7 días"
    # 'b': poca carga y no jugó el último → tope general.
    assert caps.loc["b", "max_minutes"] == 32.0
    assert caps.loc["b", "cap_source"] == "general"


def test_short_rest_after_a_long_game_caps_at_28():
    summary = pd.DataFrame([{"player_id": "a", "load_minutes": 70.0, "last_game_minutes": 34.0}])

    caps = mp.load_caps(summary, rest_days=1).set_index("player_id")
    assert caps.loc["a", "max_minutes"] == 28.0
    assert caps.loc["a", "cap_source"] == "descanso corto"

    relaxed = mp.load_caps(summary, rest_days=3).set_index("player_id")
    assert relaxed.loc["a", "max_minutes"] == 32.0


def test_load_cap_never_goes_below_the_floor():
    summary = pd.DataFrame([{"player_id": "a", "load_minutes": 170.0, "last_game_minutes": 10.0}])

    caps = mp.load_caps(summary, rest_days=4, rules=mp.LoadRules(min_load_cap=10.0))

    assert caps.iloc[0]["max_minutes"] == 10.0


def test_default_reference_date():
    last = dt.date(2026, 2, 8)
    assert mp.default_reference_date(last, dt.date(2026, 2, 11)) == dt.date(2026, 2, 11)
    # Calendario lejano (temporada cerrada): partido hipotético 3 días después.
    assert mp.default_reference_date(last, dt.date(2026, 10, 1)) == dt.date(2026, 2, 11)
    assert mp.default_reference_date(last, None) == dt.date(2026, 2, 11)


def test_recent_distribution_margin_rescales_available_players():
    roster = _roster(recent_avg_minutes=10.0)  # 10 × 10 = 100 → se dobla a 200
    roster.loc[roster["player_id"] == "rookie", "available"] = False

    margin = mp.recent_distribution_margin(roster)

    available = roster[roster["available"]]
    assert margin == pytest.approx(float((available["rapm"] * 200 / 9).sum() / 40))


# ------------------------------------------------------ topes relajados --


def test_relax_caps_raises_suggested_caps_just_enough_and_spares_the_coach():
    roster = _roster(max_minutes=18.0)  # 10 × 18 = 180 < 200
    roster["cap_source"] = "carga"
    roster["cap_detail"] = "150 min en 7 días"
    roster.loc[roster["player_id"] == "howard", "cap_source"] = "entrenador"

    relaxed, notes = mp.relax_caps(roster)

    caps = relaxed.set_index("player_id")["max_minutes"]
    assert caps["howard"] == 18.0  # el del entrenador no se toca
    assert caps["moneke"] == 21.0  # 18 + 9·3 = 207 ≥ 200; con +2 serían 198
    assert notes == ["3 min los topes sugeridos para llegar a 200"]
    assert relaxed.set_index("player_id").loc["moneke", "cap_detail"] == "150 min en 7 días, +3 para llegar a 200"
    assert mp.plan_minutes(relaxed)["feasible"]


def test_relax_caps_covers_a_position_floor_first():
    roster = _roster()
    roster.loc[roster["position"] == "Base", "max_minutes"] = 15.0  # 30 < 40

    relaxed, notes = mp.relax_caps(roster, position_floors=mp.DEFAULT_POSITION_FLOORS)

    caps = relaxed.set_index("player_id")["max_minutes"]
    assert caps["howard"] == 20.0 and caps["codi"] == 20.0
    assert caps["moneke"] == 32.0  # los demás, intactos
    assert notes == ["5 min los topes sugeridos para cubrir Base"]


def test_relax_caps_does_nothing_when_not_needed():
    relaxed, notes = mp.relax_caps(_roster())

    assert notes == []
    assert (relaxed["max_minutes"] == 32.0).all()
