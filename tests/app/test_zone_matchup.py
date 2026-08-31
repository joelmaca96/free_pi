"""Tests de `app/analytics/zone_matchup.py` (propuesta 08, dónde castigar al rival).

Lógica pura sobre `pandas` —sin Streamlit ni base de datos—, mismo criterio
que `test_shot_quality.py`: se prueban aquí las decisiones que hacen que la
lista de zonas sea un plan de ataque y no ruido con forma de tabla —qué
referencia se usa por competición, cuánto se encoge la diferencia y qué hace
falta para que una zona entre en la lista corta.
"""
import pandas as pd
import pytest

from app.analytics import zone_matchup as zm

#: Igual que en `test_shot_quality.py`: 1 vale 2 puntos, 4 vale 3, 13 se funde en 1.
_LABELS = {1: "Pintura", 4: "Triple esquina izq.", 7: "Media dist. central", 13: "Línea de fondo"}


def _profile(rows) -> pd.DataFrame:
    """`(competition_id, zone_id, fg_pct, volume)` -> perfil de equipo o de liga por (competición, zona)."""
    return pd.DataFrame(
        [(c, z, _LABELS.get(z, f"Zona {z}"), pct, vol) for c, z, pct, vol in rows],
        columns=["competition_id", "zone_id", "zone_label", "fg_pct", "volume"],
    )


def _league() -> pd.DataFrame:
    """Liga de juguete: misma zona con nivel distinto por competición, más una con poca muestra."""
    return _profile([
        (1, 1, 40.0, 1000),  # Pintura ACB
        (2, 1, 50.0, 1000),  # Pintura Euroliga
        (3, 1, 45.0, 20),    # Pintura Copa: no es referencia propia
        (1, 4, 35.0, 600),   # Triple esquina ACB
    ])


def _diff(rows) -> pd.DataFrame:
    """`(zone_id, volume, diff_pp_shrunk)` -> salida mínima de `zone_diff_profile` para `attack_targets`."""
    return pd.DataFrame(rows, columns=["zone_id", "volume", "diff_pp_shrunk"]).assign(
        zone_label=lambda d: d["zone_id"].map(_LABELS)
    )


# --------------------------------------------------------------- fusión de zonas --


def test_the_baseline_track_zone_is_folded_into_the_paint_regardless_of_row_order():
    """"Línea de fondo" (13) se funde en "Pintura" (1) SIN depender de qué fila venga primero —
    si dependiera del orden, la etiqueta ganadora sería una moneda al aire."""
    first_paint = zm.league_baseline(_profile([(1, 1, 50.0, 400), (1, 13, 80.0, 100)]))
    first_track = zm.league_baseline(_profile([(1, 13, 80.0, 100), (1, 1, 50.0, 400)]))

    for baseline in (first_paint, first_track):
        assert not (baseline["zone_id"] == 13).any()
        cell = baseline.loc[(baseline["competition_id"] == 1) & (baseline["zone_id"] == 1)].iloc[0]
        assert cell["zone_label"] == "Pintura"
        assert cell["league_volume"] == 500
        assert cell["league_fg_pct"] == pytest.approx((50 * 400 + 80 * 100) / 500)


def test_team_profile_folds_zones_the_same_way():
    profile = zm.team_profile(_profile([(1, 1, 50.0, 400), (1, 13, 80.0, 100)]))

    assert profile["zone_id"].tolist() == [1]
    assert profile.iloc[0][["zone_label", "volume"]].tolist() == ["Pintura", 500]
    assert profile.iloc[0]["fg_pct"] == pytest.approx((50 * 400 + 80 * 100) / 500)


def test_missing_columns_fail_at_the_door():
    with pytest.raises(ValueError, match="volume"):
        zm.team_profile(pd.DataFrame({"competition_id": [1], "zone_id": [1], "zone_label": ["Pintura"], "fg_pct": [50.0]}))


# --------------------------------------------------------------- línea base de liga --


def test_league_baseline_keeps_each_competition_apart():
    baseline = zm.league_baseline(_league())

    by_key = baseline.set_index(["competition_id", "zone_id"])["league_fg_pct"]
    assert by_key[(1, 1)] == pytest.approx(40.0)
    assert by_key[(2, 1)] == pytest.approx(50.0)


def test_a_thin_competition_borrows_the_pooled_reference():
    baseline = zm.league_baseline(_league())

    pooled = baseline.loc[baseline["competition_id"] == zm.POOLED_COMPETITION_ID].set_index("zone_id")
    thin = baseline.loc[(baseline["competition_id"] == 3) & (baseline["zone_id"] == 1)].iloc[0]

    assert thin["is_pooled"]
    assert thin["league_fg_pct"] == pytest.approx(pooled.loc[1, "league_fg_pct"])
    assert not baseline.loc[(baseline["competition_id"] == 1) & (baseline["zone_id"] == 1)].iloc[0]["is_pooled"]


def test_league_baseline_of_empty_input_is_empty_with_the_right_columns():
    baseline = zm.league_baseline(_profile([]))
    assert baseline.empty
    assert list(baseline.columns) == [
        "competition_id", "zone_id", "zone_label", "league_fg_pct", "league_volume", "is_pooled",
    ]


# --------------------------------------------------------------- cruce con la liga --


def test_zone_diff_profile_weights_the_league_reference_by_where_the_team_plays():
    """Un equipo que concede el 75% de sus tiros en la competición más fácil tiene que
    compararse sobre todo contra ESA referencia, no contra la media aritmética de las dos."""
    baseline = zm.league_baseline(_league())
    team = zm.team_profile(_profile([(1, 1, 50.0, 100), (2, 1, 50.0, 300)]))

    diff = zm.zone_diff_profile(team, baseline)

    row = diff.iloc[0]
    assert row["zone_label"] == "Pintura"
    assert row["volume"] == 400
    assert row["league_fg_pct"] == pytest.approx((40 * 100 + 50 * 300) / 400)
    assert row["fg_pct"] == pytest.approx(50.0)
    assert row["diff_pp"] == pytest.approx(50.0 - 47.5)


def test_zone_diff_profile_shrinks_the_difference_towards_zero():
    baseline = zm.league_baseline(_league())
    team = zm.team_profile(_profile([(1, 1, 60.0, 200)]))  # diff_pp bruta = 20 (60 - 40)

    diff = zm.zone_diff_profile(team, baseline)

    row = diff.iloc[0]
    assert row["diff_pp"] == pytest.approx(20.0)
    assert row["diff_pp_shrunk"] == pytest.approx(20.0 * 200 / (200 + zm.SHRINK_K))
    assert abs(row["diff_pp_shrunk"]) < abs(row["diff_pp"])


def test_zone_diff_profile_falls_back_to_pooled_for_a_competition_the_baseline_never_saw():
    """Un amistoso o una competición sin referencia propia no deja la zona sin valorar:
    cae a la agrupada de todas las competiciones, igual que `shot_quality.with_expected`."""
    baseline = zm.league_baseline(_league())
    team = zm.team_profile(_profile([(9, 1, 60.0, 100)]))

    diff = zm.zone_diff_profile(team, baseline)

    pooled_fg = baseline.loc[baseline["competition_id"] == zm.POOLED_COMPETITION_ID].set_index("zone_id")
    assert diff.iloc[0]["league_fg_pct"] == pytest.approx(pooled_fg.loc[1, "league_fg_pct"])


def test_zone_diff_profile_columns_are_the_ones_the_heatmap_already_knows():
    baseline = zm.league_baseline(_league())
    diff = zm.zone_diff_profile(zm.team_profile(_profile([(1, 1, 50.0, 100)])), baseline)

    assert {"zone_label", "volume", "made", "fg_pct", "league_fg_pct", "diff_pp"} <= set(diff.columns)


def test_zone_diff_profile_of_empty_input_is_empty():
    baseline = zm.league_baseline(_league())
    assert zm.zone_diff_profile(zm.team_profile(_profile([])), baseline).empty
    assert zm.zone_diff_profile(_profile([(1, 1, 50.0, 100)]).assign(made=0), zm.league_baseline(_profile([]))).empty


# --------------------------------------------------------------- lista de zonas a atacar --


def test_attack_targets_requires_both_sides_to_agree_in_sign():
    """Zona 4: el rival defiende BIEN ahí (diff negativa) — no es zona a atacar aunque
    nosotros produzcamos por encima de la liga."""
    concede = _diff([(1, 200, 5.0), (4, 200, -3.0)])
    produce = _diff([(1, 200, 4.0), (4, 200, 6.0)])

    targets = zm.attack_targets(concede, produce, games_played=20)

    assert targets["zone_label"].tolist() == ["Pintura"]


def test_attack_targets_hides_zones_below_the_minimum_sample_on_either_side():
    concede = _diff([(1, zm.MIN_SHOTS - 1, 5.0)])
    produce = _diff([(1, 200, 5.0)])

    assert zm.attack_targets(concede, produce, games_played=20).empty


def test_attack_targets_orders_by_points_per_game_not_by_raw_percentage():
    """Zona 1 (2 puntos): +8 pp con 3 tiros/partido. Zona 4 (3 puntos): +3 pp con 15 tiros/partido.
    Ordenar por % pondría la 1 primero; por puntos/partido gana la 4 (§2b del documento)."""
    games = 10
    concede = _diff([(1, games * 3, 8.0), (4, games * 15, 3.0)])
    produce = _diff([(1, games * 3, 5.0), (4, games * 15, 5.0)])

    # `min_shots` bajo a propósito: aquí se prueba el ORDEN, no el filtro de
    # muestra mínima (ya cubierto en su propio test) — con el `MIN_SHOTS` real
    # (100), la zona 1 de este ejemplo (30 tiros) quedaría fuera por muestra,
    # no por orden.
    targets = zm.attack_targets(concede, produce, games_played=games, min_shots=10)

    assert targets["zone_label"].tolist() == ["Triple esquina izq.", "Pintura"]
    paint = targets.set_index("zone_label").loc["Pintura"]
    assert paint["value_pts_per_game"] == pytest.approx(0.08 * 2 * 3)
    corner = targets.set_index("zone_label").loc["Triple esquina izq."]
    assert corner["value_pts_per_game"] == pytest.approx(0.03 * 3 * 15)


def test_attack_targets_respects_top_n():
    concede = _diff([(1, 200, 5.0), (4, 200, 5.0), (7, 200, 5.0)])
    produce = _diff([(1, 200, 5.0), (4, 200, 5.0), (7, 200, 5.0)])

    targets = zm.attack_targets(concede, produce, games_played=10, top_n=2)

    assert len(targets) == 2


def test_attack_targets_is_empty_without_games_played():
    concede = _diff([(1, 200, 5.0)])
    produce = _diff([(1, 200, 5.0)])

    assert zm.attack_targets(concede, produce, games_played=0).empty


def test_attack_targets_of_empty_input_is_empty_with_the_right_columns():
    targets = zm.attack_targets(_diff([]), _diff([]), games_played=10)

    assert targets.empty
    assert list(targets.columns) == [
        "zone_label", "concede_diff_pp", "produce_diff_pp", "shot_value",
        "shots_per_game", "value_pts_per_game", "concede_volume", "produce_volume",
    ]
