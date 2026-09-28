"""Tests del impacto ajustado y el constructor de quintetos (`app/analytics/impact.py`).

Lo que hay que poder demostrar, porque cada cosa falla en silencio:

- **Los segmentos de diez jugadores conservan el marcador.** Cruzar los
  tramos del local con los del visitante parte cada tramo en varios; si el
  margen de cada trozo no sale de las fronteras exactas, la suma deja de
  cuadrar con el partido y el RAPM ajusta sobre puntos inventados.
- **El RAPM separa al jugador de sus compañeros.** Es su única razón de
  existir frente al On/Off: con datos simulados de impacto conocido tiene
  que recuperar el orden, incluido el caso del suplente que juega siempre
  con los titulares.
- **El constructor respeta las restricciones** (disponibles, fijos,
  posiciones) y acompaña cada proyección con lo observado.
"""
import numpy as np
import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.analytics import impact
from app.data import queries

_HOME = ["h1", "h2", "h3", "h4", "h5"]
_HOME_B = ["h1", "h2", "h3", "h4", "h6"]
_AWAY = ["a1", "a2", "a3", "a4", "a5"]


def _rows(stints):
    """`[(stint_id, team, is_home, start, end, pf, pa, margin_start, players)]` -> filas (tramo, jugador)."""
    out = []
    for stint_id, team, is_home, start, end, pf, pa, margin, players in stints:
        for p in players:
            out.append({
                "stint_id": stint_id, "game_id": "g", "team_id": team, "is_home": is_home,
                "start_seconds": start, "end_seconds": end, "points_for": pf,
                "points_against": pa, "margin_start": margin, "player_id": p,
            })
    return pd.DataFrame(out)


def test_segments_split_on_either_teams_change_and_keep_the_score():
    """El local cambia en el 300 y el visitante en el 200: tres segmentos, y la suma cuadra."""
    rows = _rows([
        # local: 0-300 gana 10-4 (margen 0 -> +6); 300-600 pierde 2-8 (+6 -> 0)
        (1, "home", 1, 0.0, 300.0, 10, 4, 0, _HOME),
        (2, "home", 1, 300.0, 600.0, 2, 8, 6, _HOME_B),
        # visitante: 0-200 pierde 2-6 (margen suyo 0 -> -4); 200-600 (-4 -> 0)
        (3, "away", 0, 0.0, 200.0, 2, 6, 0, _AWAY),
        (4, "away", 0, 200.0, 600.0, 12, 8, -4, _AWAY),
    ])

    segments = impact.build_segments(rows)

    assert segments[["start_seconds", "end_seconds"]].values.tolist() == [[0, 200], [200, 300], [300, 600]]
    assert segments["margin_delta"].tolist() == [4, 2, -6]
    assert segments["margin_delta"].sum() == 0  # el partido acaba 0 en el tramo final
    assert segments.iloc[2]["home_players"] == tuple(sorted(_HOME_B))
    assert segments["minutes"].sum() == pytest.approx(10.0)


def test_segments_skip_holes_without_a_valid_lineup():
    """Si un equipo no tiene quinteto válido en un rato, ese rato no entra (no se inventa quién estaba)."""
    rows = _rows([
        (1, "home", 1, 0.0, 300.0, 5, 5, 0, _HOME),
        (2, "away", 0, 0.0, 100.0, 2, 2, 0, _AWAY),
        (3, "away", 0, 200.0, 300.0, 3, 3, 0, _AWAY),
    ])

    segments = impact.build_segments(rows)

    assert segments[["start_seconds", "end_seconds"]].values.tolist() == [[0, 100], [200, 300]]


def test_segments_drop_stints_that_are_not_five_players():
    rows = _rows([
        (1, "home", 1, 0.0, 300.0, 5, 5, 0, _HOME[:4]),
        (2, "away", 0, 0.0, 300.0, 5, 5, 0, _AWAY),
    ])

    assert impact.build_segments(rows).empty


def _simulated_segments(n_segments=6000, seed=3):
    """Dos equipos de 8 con impactos conocidos, y un suplente (`b_sub`) pegado a los titulares."""
    rng = np.random.default_rng(seed)
    true = {f"a{i}": 0.0 for i in range(8)}
    true.update({f"b{i}": 0.0 for i in range(8)})
    true["a0"], true["a7"] = 5.0, -5.0
    true["b0"] = 4.0
    rows = []
    for i in range(n_segments):
        home = tuple(sorted(rng.choice([f"a{j}" for j in range(8)], 5, replace=False)))
        away = tuple(sorted(rng.choice([f"b{j}" for j in range(8)], 5, replace=False)))
        if i % 2:
            home, away = away, home
        minutes = rng.uniform(1, 4)
        expected = (sum(true[p] for p in home) - sum(true[p] for p in away) + 2.0) * minutes / 40
        delta = int(round(rng.normal(expected, np.sqrt(5 * minutes))))
        rows.append({
            "game_id": f"g{i // 20}", "start_seconds": 0.0, "end_seconds": minutes * 60, "minutes": minutes,
            "margin_delta": delta, "home_team_id": "A" if home[0].startswith("a") else "B",
            "away_team_id": "B" if home[0].startswith("a") else "A",
            "home_players": home, "away_players": away,
        })
    return pd.DataFrame(rows), true


def test_rapm_recovers_known_impacts():
    segments, true = _simulated_segments()

    fit = impact.fit_rapm(segments)
    players = fit["players"].set_index("player_id")

    assert players["rapm"].idxmax() == "a0"
    assert players["rapm"].idxmin() == "a7"
    assert players.loc["b0", "rapm"] > 1.0
    assert fit["home_advantage"] == pytest.approx(2.0, abs=1.5)
    assert players["reliable"].all()


def test_rapm_shrinks_to_zero_with_huge_ridge():
    segments, _ = _simulated_segments(n_segments=500)

    fit = impact.fit_rapm(segments, ridge=1e9)

    assert fit["players"]["rapm"].abs().max() < 0.01


def test_rapm_on_empty_input():
    fit = impact.fit_rapm(pd.DataFrame())

    assert fit["players"].empty
    assert fit["segments"] == 0


def test_cross_validation_prefers_some_regularisation_over_almost_none():
    segments, _ = _simulated_segments(n_segments=2000)

    cv = impact.cross_validate_ridge(segments, grid=(1.0, 1200.0), folds=4)

    assert cv["ridge"].tolist() == [1.0, 1200.0]
    assert cv.loc[cv["ridge"] == 1200.0, "mse"].iloc[0] < cv.loc[cv["ridge"] == 1.0, "mse"].iloc[0]


def _rapm(values):
    return pd.DataFrame({"player_id": list(values), "rapm": list(values.values())})


def test_best_lineups_picks_the_top_sum_and_reports_what_was_observed():
    rapm = _rapm({"p1": 5, "p2": 4, "p3": 3, "p4": 2, "p5": 1, "p6": -3})
    observed = pd.DataFrame({
        "players": [("p1", "p2", "p3", "p4", "p5")], "minutes": [42.0], "plus_minus": [10], "per_40": [9.5],
    })

    best = impact.best_lineups(rapm, ["p1", "p2", "p3", "p4", "p5", "p6"], observed=observed, top=2)

    assert best.iloc[0]["players"] == ("p1", "p2", "p3", "p4", "p5")
    assert best.iloc[0]["projected_per_40"] == pytest.approx(15)
    assert best.iloc[0]["observed_minutes"] == 42.0
    assert best.iloc[1]["observed_minutes"] == 0.0
    assert pd.isna(best.iloc[1]["observed_per_40"])


def test_best_lineups_respects_availability_must_include_and_positions():
    rapm = _rapm({"p1": 5, "p2": 4, "p3": 3, "p4": 2, "p5": 1, "p6": -3, "p7": -4})
    positions = {"p1": "Alero", "p2": "Alero", "p3": "Escolta", "p4": "Pívot", "p5": "Ala-pívot",
                 "p6": "Base", "p7": "Base"}

    best = impact.best_lineups(
        rapm, ["p2", "p3", "p4", "p5", "p6", "p7"], must_include=["p7"],
        positions=positions, required_positions={"Base": 1, "Pívot": 1},
    )

    assert all("p7" in lineup for lineup in best["players"])
    assert all("p1" not in lineup for lineup in best["players"])  # no disponible
    assert all("p4" in lineup for lineup in best["players"])      # único pívot
    assert best.iloc[0]["players"] == ("p2", "p3", "p4", "p5", "p7")


def test_best_lineups_needs_five_candidates():
    assert impact.best_lineups(_rapm({"p1": 1}), ["p1", "p2", "p3", "p4"]).empty


def test_unknown_candidate_counts_as_an_average_player():
    rapm = _rapm({"p1": 2, "p2": 2, "p3": 2, "p4": 2})

    best = impact.best_lineups(rapm, ["p1", "p2", "p3", "p4", "new"])

    assert best.iloc[0]["projected_per_40"] == pytest.approx(8)


def test_replacement_options_ranks_by_rapm_and_reports_the_delta():
    rapm = _rapm({"p1": 5, "p2": 1, "p3": 0, "p4": 0, "p5": 0, "s1": 3, "s2": -1})

    out = impact.replacement_options(rapm, ["p1", "p2", "p3", "p4", "p5"], "p1", ["s1", "s2", "p2"])

    assert out["player_id"].tolist() == ["s1", "s2"]
    assert out.iloc[0]["delta"] == pytest.approx(-2)


def test_observed_lineups_and_team_minutes_use_the_team_perspective():
    segments = pd.DataFrame([
        {"game_id": "g", "minutes": 2.0, "margin_delta": 4, "home_team_id": "A", "away_team_id": "B",
         "home_players": ("a",) * 5, "away_players": ("b",) * 5},
        {"game_id": "h", "minutes": 2.0, "margin_delta": 4, "home_team_id": "B", "away_team_id": "A",
         "home_players": ("b",) * 5, "away_players": ("a",) * 5},
    ])

    observed = impact.observed_lineups(segments, "A")

    assert observed.iloc[0]["minutes"] == 4.0
    assert observed.iloc[0]["plus_minus"] == 0  # +4 en casa, -4 fuera
    assert impact.team_player_minutes(segments, "A")["a"] == pytest.approx(20.0)


# ------------------------------------------------------------- consultas --

@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    with eng.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES "
                          "('v1','val','V1',1,''),('v2','val','V2',2,''),('v3','val','V3',3,''),"
                          "('v4','val','V4',4,''),('v5','val','V5',5,'')"))
        for team, players, margin in (
            ("bas", ["howard", "moneke", "codi", "nikos", "kotsar"], 0),
            ("val", ["v1", "v2", "v3", "v4", "v5"], 0),
        ):
            result = conn.execute(text(
                "INSERT INTO lineup_stints (game_id, team_id, start_seconds, end_seconds, points_for,"
                " points_against, margin_start) VALUES ('g5', :t, 0, 600, :pf, :pa, :m)"
            ), {"t": team, "pf": 20 if team == "bas" else 12, "pa": 12 if team == "bas" else 20, "m": margin})
            for p in players:
                conn.execute(text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                             {"s": result.lastrowid, "p": p})
    try:
        yield eng
    finally:
        eng.dispose()


def test_season_stint_rows_flags_home_and_feeds_the_segments(engine):
    rows = queries.season_stint_rows(engine, 1)

    assert len(rows) == 10
    assert set(rows.loc[rows["team_id"] == "bas", "is_home"]) == {1}
    assert set(rows.loc[rows["team_id"] == "val", "is_home"]) == {0}
    segments = impact.build_segments(rows)
    assert segments["margin_delta"].tolist() == [8]


def test_team_stint_rows_filters_by_team_and_last_games(engine):
    rows = queries.team_stint_rows(engine, "bas", 1, last_n_games=1)

    assert set(rows["team_id"]) == {"bas"}
    assert set(rows["player_name"]) >= {"Marcus Howard"}
    st.cache_data.clear()
    assert queries.team_stint_rows(engine, "val", 1, competition_id=2).empty


def _add_season(engine, season_id, label, stints, competition_id=1):
    """Temporada nueva con un partido `bas`-`val` y sus tramos `[(team, start, end, pf, pa, margin, players)]`."""
    game_id = f"s{season_id}g1"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (:i, :l)"), {"i": season_id, "l": label})
        conn.execute(text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id, game_date,"
            " home_score, away_score, pace) VALUES (:g, :s, :c, 'bas', 'val', '2026-11-01', 80, 70, 70.0)"
        ), {"g": game_id, "s": season_id, "c": competition_id})
        for team, start, end, pf, pa, margin, players in stints:
            result = conn.execute(text(
                "INSERT INTO lineup_stints (game_id, team_id, start_seconds, end_seconds, points_for,"
                " points_against, margin_start) VALUES (:g, :t, :s, :e, :pf, :pa, :m)"
            ), {"g": game_id, "t": team, "s": start, "e": end, "pf": pf, "pa": pa, "m": margin})
            for p in players:
                conn.execute(text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                             {"s": result.lastrowid, "p": p})


_BAS_SEASON2 = ["howard", "moneke", "codi", "nikos", "sedekerskis"]  # sedekerskis: sin tramos en la 1


def test_season_impact_uses_the_previous_season_as_prior(engine):
    from app.data import queries_assistant

    _add_season(engine, 2, "2026-2027", [
        ("bas", 0, 600, 10, 12, 0, _BAS_SEASON2),
        ("val", 0, 600, 12, 10, 0, ["v1", "v2", "v3", "v4", "v5"]),
    ])

    assert queries_assistant.previous_season(engine, 2) == {"id": 1, "label": "2025-2026"}
    assert queries_assistant.previous_season(engine, 1) is None

    data = queries_assistant.season_impact(engine, 2)
    previous = queries_assistant.season_impact(engine, 1, use_prior=False)["fit"]["players"].set_index("player_id")
    fit = data["fit"]
    players = fit["players"].set_index("player_id")

    assert data["prior_season"] == {"id": 1, "label": "2025-2026"}
    assert fit["prior_used"] and fit["prior_players"] == 9  # 4 de bas + 5 de val jugaron la 1
    assert players.loc["howard", "prior"] == pytest.approx(impact.PRIOR_WEIGHT * previous.loc["howard", "rapm"])
    assert not players.loc["sedekerskis", "has_prior"]
    # En la 1 bas ganó su tramo (+8) y en la 2 pierde (-2): el prior sube a Howard respecto a "solo esta".
    assert players.loc["howard", "rapm"] > players.loc["howard", "rapm_no_prior"]
    assert set(data) >= {"fit", "segments", "names"}


def test_season_impact_without_previous_season_or_prior_is_the_old_behaviour(engine):
    from app.data import queries_assistant

    _add_season(engine, 2, "2026-2027", [
        ("bas", 0, 600, 10, 12, 0, _BAS_SEASON2),
        ("val", 0, 600, 12, 10, 0, ["v1", "v2", "v3", "v4", "v5"]),
    ])

    first = queries_assistant.season_impact(engine, 1)
    assert first["prior_season"] is None and not first["fit"]["prior_used"]

    off = queries_assistant.season_impact(engine, 2, use_prior=False)
    assert off["prior_season"] is None and not off["fit"]["prior_used"]
    plain = impact.fit_rapm(off["segments"])["players"]
    assert off["fit"]["players"]["rapm"].tolist() == plain["rapm"].tolist()

    # Con filtro de competición y la temporada anterior sin tramos en ella: sin prior.
    st.cache_data.clear()
    euro = queries_assistant.season_impact(engine, 2, competition_id=2)
    assert euro["fit"]["players"].empty and euro["prior_season"] is None


def test_previous_season_skips_seasons_without_games(engine):
    from app.data import queries_assistant

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
    _add_season(engine, 3, "2027-2028", [])

    assert queries_assistant.previous_season(engine, 3) == {"id": 1, "label": "2025-2026"}


# ------------------------------------------------ RAPM con prior (A6) --

def _old_fit_rapm_beta(segments, ridge=impact.RIDGE_LAMBDA):
    """El cálculo de `fit_rapm` ANTES del prior, copiado tal cual: la referencia de "sin prior = lo de siempre"."""
    players = sorted(set().union(*segments["home_players"], *segments["away_players"]))
    player_index = {p: i for i, p in enumerate(players)}
    idx, signs, weights, target = impact._design(segments, player_index)
    xtx, xty = impact._normal_equations(idx, signs, weights, target, len(players))
    penalty = np.full(len(xty), ridge)
    penalty[-1] = 1e-6
    beta = np.linalg.solve(xtx + np.diag(penalty), xty)
    return dict(zip(players, beta[:-1])), float(beta[-1])


def test_rapm_without_prior_is_exactly_the_old_calculation():
    segments, _ = _simulated_segments(n_segments=800)
    old_rapm, old_home = _old_fit_rapm_beta(segments)

    fit = impact.fit_rapm(segments)
    players = fit["players"].set_index("player_id")

    assert fit["home_advantage"] == old_home
    for pid, value in old_rapm.items():
        assert players.loc[pid, "rapm"] == value  # igualdad EXACTA, no aproximada
    assert (players["rapm_no_prior"] == players["rapm"]).all()
    assert players["prior"].isna().all()
    assert not players["has_prior"].any()
    assert fit["prior_used"] is False and fit["prior_players"] == 0 and fit["prior_weight"] is None
    # Claves y columnas de siempre, intactas.
    assert {"players", "home_advantage", "segments", "ridge"} <= set(fit)
    assert list(fit["players"].columns[:4]) == ["player_id", "rapm", "minutes", "reliable"]


def test_empty_prior_with_neutral_newcomers_changes_nothing():
    segments, _ = _simulated_segments(n_segments=800)

    plain = impact.fit_rapm(segments)["players"].set_index("player_id")["rapm"]
    with_empty = impact.fit_rapm(segments, prior={})["players"].set_index("player_id")["rapm"]

    assert (with_empty == plain.loc[with_empty.index]).all()


def test_prior_pulls_the_estimate_towards_it_with_little_data():
    """20 segmentos (~50 min): sin prior todo sale cerca de 0; con prior, cerca del punto de partida."""
    segments, _ = _simulated_segments(n_segments=20)

    fit = impact.fit_rapm(segments, prior={"a0": 10.0, "b0": -6.0}, prior_weight=1.0)
    players = fit["players"].set_index("player_id")

    assert fit["prior_used"] and fit["prior_players"] == 2
    assert abs(players.loc["a0", "rapm_no_prior"]) < 1.5
    assert players.loc["a0", "rapm"] == pytest.approx(10.0, abs=1.5)
    assert players.loc["b0", "rapm"] == pytest.approx(-6.0, abs=1.5)
    assert players.loc["a0", "prior"] == 10.0 and players.loc["a0", "has_prior"]
    # Quien no trae prior sigue encogiéndose hacia 0.
    assert pd.isna(players.loc["a1", "prior"]) and not players.loc["a1", "has_prior"]
    assert abs(players.loc["a1", "rapm"]) < 2.0


def test_prior_weight_scales_the_starting_point_and_is_validated():
    segments, _ = _simulated_segments(n_segments=20)

    fit = impact.fit_rapm(segments, prior={"a0": 10.0}, prior_weight=0.5)

    assert fit["players"].set_index("player_id").loc["a0", "prior"] == 5.0
    assert fit["prior_weight"] == 0.5
    with pytest.raises(ValueError):
        impact.fit_rapm(segments, prior={"a0": 10.0}, prior_weight=1.5)


def test_newcomer_prior_is_configurable():
    segments, _ = _simulated_segments(n_segments=20)

    fit = impact.fit_rapm(segments, prior={"a0": 0.0}, newcomer_prior=-2.0)
    rest = fit["players"].set_index("player_id").drop(index="a0")

    assert rest["rapm"].mean() < rest["rapm_no_prior"].mean() - 1.0


def test_with_lots_of_data_the_prior_barely_matters():
    """Un prior absurdo (el mejor como el peor) apenas mueve a quien tiene miles de minutos."""
    segments, _ = _simulated_segments()  # ~9.000 minutos por jugador
    few, _ = _simulated_segments(n_segments=20)
    wrong = {"a0": -10.0, "a7": 10.0}

    def pulled(fit):
        """Fracción del camino entre "solo esta temporada" y el prior que recorre el valor final."""
        p = fit["players"].set_index("player_id")
        return {k: (p.loc[k, "rapm"] - p.loc[k, "rapm_no_prior"]) / (v - p.loc[k, "rapm_no_prior"])
                for k, v in wrong.items()}, p

    big, players = pulled(impact.fit_rapm(segments, prior=wrong, prior_weight=1.0))
    small, _ = pulled(impact.fit_rapm(few, prior=wrong, prior_weight=1.0))

    assert all(v > 0.9 for v in small.values())   # ~40 minutos: manda el prior
    assert all(v < 0.25 for v in big.values())    # ~9.400 minutos: mandan los datos
    assert players.loc["a0", "rapm"] > 0 > players.loc["a7", "rapm"]  # ni siquiera le da la vuelta al signo
    others = players.drop(index=list(wrong))
    assert (others["rapm"] - others["rapm_no_prior"]).abs().max() < 0.25


def test_prior_from_fit_takes_the_final_rapm_of_each_player():
    segments, _ = _simulated_segments(n_segments=500)
    fit = impact.fit_rapm(segments)

    prior = impact.prior_from_fit(fit)

    assert prior == dict(zip(fit["players"]["player_id"], fit["players"]["rapm"]))
    assert impact.prior_from_fit(impact.fit_rapm(pd.DataFrame())) == {}
    assert impact.prior_from_fit(None) == {}


def _league_season(true, n_segments, rng, weight, n_teams=6, per_team=9):
    """Liga de `n_teams` × `per_team` con impactos conocidos; `weight[p]` = probabilidad relativa de jugar."""
    teams = [[f"t{t}p{j}" for j in range(per_team)] for t in range(n_teams)]
    rows = []
    for i in range(n_segments):
        ta, tb = rng.choice(n_teams, 2, replace=False)
        lineups = []
        for t in (ta, tb):
            w = np.array([weight[p] for p in teams[t]])
            lineups.append(tuple(sorted(rng.choice(teams[t], 5, replace=False, p=w / w.sum()))))
        home, away = lineups
        minutes = rng.uniform(1, 4)
        expected = (sum(true[p] for p in home) - sum(true[p] for p in away) + 2.0) * minutes / 40
        rows.append({
            "game_id": f"g{i // 25}", "start_seconds": 0.0, "end_seconds": minutes * 60, "minutes": minutes,
            "margin_delta": int(round(rng.normal(expected, np.sqrt(5 * minutes)))),
            "home_team_id": f"T{ta}", "away_team_id": f"T{tb}", "home_players": home, "away_players": away,
        })
    return pd.DataFrame(rows)


def test_prior_from_last_season_reduces_the_error_of_low_minute_players():
    """Dos temporadas simuladas con impacto real conocido, que cambia algo de un año a otro.

    Temporada 1 con minutos repartidos; en la 2, tres de cada plantilla casi no
    juegan (~150 min). Con el prior de la 1, el error de esos jugadores frente
    a su impacto REAL de la 2 baja, el de la liga entera también, y la
    validación cruzada por partido (fuera de muestra) lo confirma. (Se ha
    comprobado con 8 semillas: mejora en todas.)
    """
    rng = np.random.default_rng(0)
    players = [f"t{t}p{j}" for t in range(6) for j in range(9)]
    true1 = {p: rng.normal(0, 3) for p in players}
    true2 = {p: true1[p] + rng.normal(0, 1) for p in players}
    season1 = _league_season(true1, 3000, rng, {p: 1.0 for p in players})
    season2 = _league_season(true2, 1500, rng, {p: 0.08 if int(p.split("p")[1]) >= 6 else 1.0 for p in players})

    prior = impact.prior_from_fit(impact.fit_rapm(season1))
    df = impact.fit_rapm(season2, prior=prior)["players"].set_index("player_id")
    df["true"] = pd.Series(true2)
    low = df[df["minutes"] < impact.MIN_RELIABLE_MINUTES]

    assert len(low) >= 12
    error_with = (low["rapm"] - low["true"]).abs().mean()
    error_without = (low["rapm_no_prior"] - low["true"]).abs().mean()
    assert error_with < error_without
    assert ((df["rapm"] - df["true"]) ** 2).mean() < ((df["rapm_no_prior"] - df["true"]) ** 2).mean()
    cv_with = impact.cross_validate_ridge(season2, grid=(1200.0,), folds=4, prior=prior)["mse"].iloc[0]
    cv_without = impact.cross_validate_ridge(season2, grid=(1200.0,), folds=4)["mse"].iloc[0]
    assert cv_with < cv_without
