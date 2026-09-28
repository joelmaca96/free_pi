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
