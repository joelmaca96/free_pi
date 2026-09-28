"""Tests del plan de rotación contra el rival (`app/analytics/rotation_plan.py`).

Partidos sintéticos en los que se sabe de antemano todo lo que el plan tiene
que encontrar:

- El rival (`riv`) sienta a su estrella `star` en los minutos 8-12 (420-720 s)
  y entra `bench`; en ese rato pierde 0-10, el resto del partido va empatado.
- Nosotros (`own`) salimos con `o1..o5`; en 600-900 s entran `o6` y `o7` por
  `o4` y `o5`. En los minutos 8-12 lo habitual es, por tanto, `o1..o5`.
- El RAPM es un ajuste a mano (no la regresión): así la proyección de cada
  quinteto se puede afirmar con números exactos.
"""
import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.analytics import impact
from app.analytics import rotation_plan as rpl
from app.data import queries

_CORE = ["c1", "c2", "c3", "c4"]
_OWN_START = ["o1", "o2", "o3", "o4", "o5"]
_OWN_BENCH = ["o1", "o2", "o3", "o6", "o7"]
_OWN = ["o1", "o2", "o3", "o4", "o5", "o6", "o7"]
#: En estos partidos los cuatro `c*` juegan los 40 minutos: la estrella es el
#: QUINTO jugador por minutos, y por defecto solo se miran los tres primeros.
_KEY = 5

# (equipo, inicio, fin, pf, pa, margen de entrada, jugadores) — margen desde el
# punto de vista de ese equipo. Los dos equipos cuentan el mismo marcador.
_GAME = [
    ("riv", 0.0, 420.0, 10, 10, 0, ["star"] + _CORE),
    ("riv", 420.0, 720.0, 0, 10, 0, ["bench"] + _CORE),
    ("riv", 720.0, 2400.0, 45, 45, -10, ["star"] + _CORE),
    ("own", 0.0, 600.0, 16, 10, 0, _OWN_START),
    ("own", 600.0, 900.0, 9, 5, 6, _OWN_BENCH),
    ("own", 900.0, 2400.0, 40, 40, 10, _OWN_START),
]


def _rows(n_games):
    """Filas (tramo, jugador) de `n_games` partidos idénticos; en los pares jugamos en casa."""
    out, stint_id = [], 0
    for g in range(n_games):
        game_id = f"g{g:02d}"
        own_home = g % 2 == 0
        for team, start, end, pf, pa, margin, players in _GAME:
            stint_id += 1
            for p in players:
                out.append({
                    "stint_id": stint_id, "game_id": game_id, "game_date": f"2026-01-{g + 1:02d}",
                    "team_id": team, "is_home": int(own_home == (team == "own")),
                    "start_seconds": start, "end_seconds": end, "points_for": pf, "points_against": pa,
                    "margin_start": margin, "player_id": p, "player_name": p.upper(),
                })
    return pd.DataFrame(out)


def _fit(home_advantage=2.0):
    values = {"o1": 1.0, "o2": 1.0, "o3": 1.0, "o4": -2.0, "o5": -2.0, "o6": 3.0, "o7": 3.0,
              "star": 4.0, "c1": 0.0, "c2": 0.0, "c3": 0.0, "c4": 0.0, "bench": -3.0}
    players = pd.DataFrame({"player_id": list(values), "rapm": list(values.values())})
    players["minutes"], players["reliable"] = 1000.0, True
    return {"players": players, "home_advantage": home_advantage, "segments": 1, "ridge": 1200.0}


@pytest.fixture(scope="module")
def all_rows():
    return _rows(16)


@pytest.fixture(scope="module")
def rival_rows(all_rows):
    return all_rows[all_rows["team_id"] == "riv"].reset_index(drop=True)


@pytest.fixture(scope="module")
def own_rows(all_rows):
    return all_rows[all_rows["team_id"] == "own"].reset_index(drop=True)


@pytest.fixture(scope="module")
def segments(all_rows):
    return impact.build_segments(all_rows)


# ------------------------------------------------------------------ piezas --


def test_home_term_is_signed_from_our_side():
    assert rpl.home_term(2.5, True) == pytest.approx(2.5)
    assert rpl.home_term(2.5, False) == pytest.approx(-2.5)
    assert rpl.home_term(2.5, None) == 0.0
    assert rpl.home_term(float("nan"), True) == 0.0


def test_project_vs_five_is_our_sum_minus_theirs_plus_court():
    fit = _fit()
    ours, theirs = _OWN_BENCH, _CORE + ["bench"]

    assert rpl.project_vs_five(fit["players"], ours, theirs) == pytest.approx(9.0 + 3.0)
    assert rpl.project_vs_five(
        fit["players"], ours, theirs, home_advantage=2.0, is_home=False
    ) == pytest.approx(10.0)
    # Un jugador sin fila en el ajuste cuenta como jugador medio (0).
    assert rpl.lineup_value(fit["players"], ["o1", "desconocido"]) == pytest.approx(1.0)


def test_typical_five_excludes_the_resting_player_and_respects_availability(rival_rows, own_rows):
    from app.analytics import rotation_patterns as rp

    shares = rp.minute_shares(rival_rows)
    # 5-8: la estrella está 3 de 4 minutos, el suplente 1 de 4.
    five, presence = rpl.typical_five(shares, 5, 8)
    assert set(five) == set(_CORE + ["star"])
    assert presence == pytest.approx((4 * 1.0 + 0.75) / 5)
    # En su descanso se le quita aunque algún partido no se siente.
    five, _ = rpl.typical_five(shares, 8, 12, exclude=["star"])
    assert set(five) == set(_CORE + ["bench"])

    own_shares = rp.minute_shares(own_rows)
    assert set(rpl.typical_five(own_shares, 8, 12)[0]) == set(_OWN_START)
    # Sin o4 disponible, entra el siguiente más presente (o6/o7, 0,4).
    five, _ = rpl.typical_five(own_shares, 8, 12, among=["o1", "o2", "o3", "o5", "o6", "o7"])
    assert "o4" not in five and len(five) == 5
    assert rpl.typical_five(own_shares, 8, 12, among=["o1", "o2"]) == ((), 0.0)


def test_window_performance_sums_the_minutes_of_the_window(rival_rows):
    from app.analytics import rotation_patterns as rp

    minutes, per_40 = rpl.window_performance(rp.block_performance(rival_rows, block_minutes=1), 8, 12)

    assert minutes == pytest.approx(5 * 16)
    assert per_40 == pytest.approx(40.0 * (-10 * 16) / (5 * 16))


# --------------------------------------------------------------- ventanas --


def test_attack_windows_find_the_rest_and_the_worst_block_without_repeating_it(rival_rows):
    windows = rpl.attack_windows(rival_rows, key_players=_KEY)

    labels = [(w["kind"], w["label"]) for w in windows]
    # El descanso de la estrella (8-12) y su peor bloque de reloj que NO es ese
    # mismo descanso: el 9-12 (todo dentro del descanso) se descarta, el 5-8
    # (un minuto de cuatro dentro) se queda.
    assert labels == [("tramo", "5-8"), ("descanso", "8-12")]
    rest = windows[1]
    assert rest["player_id"] == "star"
    assert set(rest["rival_five"]) == set(_CORE + ["bench"])
    assert rest["rival_per_40"] == pytest.approx(-80.0)
    assert rest["rival_five_share"] == pytest.approx(1.0)


def test_attack_windows_attach_the_on_off_only_when_reliable(rival_rows):
    on_off = pd.DataFrame({
        "player_id": ["star"], "off_per_40": [-7.5], "on_per_40": [1.0], "reliable": [True],
    })
    assert rpl.attack_windows(rival_rows, on_off, key_players=_KEY)[1]["off_per_40"] == pytest.approx(-7.5)

    on_off["reliable"] = False
    assert rpl.attack_windows(rival_rows, on_off, key_players=_KEY)[1]["off_per_40"] is None


def test_attack_windows_empty_without_stints():
    assert rpl.attack_windows(pd.DataFrame()) == []


# -------------------------------------------------------------------- plan --


def test_build_plan_projects_against_their_usual_five_and_compares_with_ours(rival_rows, own_rows, segments):
    plan = rpl.build_plan(
        rival_rows, own_rows, _fit(), segments, "own", _OWN, is_home=True, top=3, key_players=_KEY
    )

    rest = next(w for w in plan if w["kind"] == "descanso")
    # Su quinteto sin la estrella: 4 × 0 + (−3) = −3.
    assert rest["rival_five_rapm"] == pytest.approx(-3.0)
    # Lo nuestro habitual en 8-12 (o1..o5): 3 − 4 = −1 → −1 + 3 + 2 (casa) = 4.
    assert set(rest["own_usual"]) == set(_OWN_START)
    assert rest["own_usual_margin"] == pytest.approx(4.0)
    best = rest["lineups"].iloc[0]
    assert best["players"] == tuple(sorted(_OWN_BENCH))
    assert best["projected_margin"] == pytest.approx(9.0 + 3.0 + 2.0)
    assert best["gain_vs_usual"] == pytest.approx(10.0)
    assert len(rest["lineups"]) == 3

    # Contra su quinteto con la estrella (bloque 5-8) el margen baja 7 = 4 − (−3).
    block = next(w for w in plan if w["kind"] == "tramo")
    assert block["lineups"].iloc[0]["projected_margin"] == pytest.approx(14.0 - 7.0)


def test_build_plan_respects_availability_and_court(rival_rows, own_rows, segments):
    plan = rpl.build_plan(
        rival_rows, own_rows, _fit(), segments, "own", [p for p in _OWN if p != "o6"], is_home=False,
        key_players=_KEY,
    )

    rest = next(w for w in plan if w["kind"] == "descanso")
    for players in rest["lineups"]["players"]:
        assert "o6" not in players
    # Fuera se resta la ventaja de campo: o1,o2,o3,o7 + el mejor que queda (o4/o5, −2).
    assert rest["lineups"].iloc[0]["projected_margin"] == pytest.approx((3 + 3 - 2) + 3 - 2)


def test_build_plan_attaches_what_the_lineup_did_for_real(rival_rows, own_rows, segments):
    plan = rpl.build_plan(rival_rows, own_rows, _fit(), segments, "own", _OWN_START, top=1, key_players=_KEY)

    lineup = plan[0]["lineups"].iloc[0]
    assert lineup["players"] == tuple(sorted(_OWN_START))
    assert lineup["observed_minutes"] == pytest.approx(35.0 * 16)


def test_build_plan_without_our_own_stints_still_proposes(rival_rows, segments):
    plan = rpl.build_plan(rival_rows, pd.DataFrame(), _fit(), segments, "own", _OWN, key_players=_KEY)

    assert plan and all(w["own_usual"] == () for w in plan)
    assert plan[0]["lineups"]["gain_vs_usual"].isna().all()
    assert not plan[0]["lineups"].empty


def test_default_candidates_uses_minutes_with_the_team(segments):
    # o6/o7 solo juegan 5 min por partido: 80 en 16 partidos, por debajo de 100.
    assert set(rpl.default_candidates(segments, "own")) == set(_OWN_START)
    assert set(rpl.default_candidates(segments, "own", min_minutes=50)) == set(_OWN)


def test_default_candidates_early_in_the_season_scale_with_the_top_player():
    # Dos partidos: el más usado lleva 80 min, así que el corte baja a 20 y
    # los suplentes (10) siguen fuera, pero los titulares (70) entran.
    segments = impact.build_segments(_rows(2))
    assert set(rpl.default_candidates(segments, "own")) == set(_OWN_START)
    assert rpl.default_candidates(pd.DataFrame(), "own") == []


# ------------------------------------------------------------------ frases --


def test_plan_insights_read_like_a_game_plan(rival_rows, own_rows, segments):
    plan = rpl.build_plan(rival_rows, own_rows, _fit(), segments, "own", _OWN, is_home=True, key_players=_KEY)
    names = {p: p.upper() for p in _OWN + _CORE + ["star", "bench"]}

    lines = rpl.plan_insights("Rival", plan, names)

    assert len(lines) == 2
    rest = lines[1]
    assert rest.startswith("Minutos 8-12, cuando descansa STAR: Rival hace -80.0 por 40")
    assert "O1 · O2 · O3 · O6 · O7, +14.0 por 40 proyectado" in rest
    assert "(80 min reales juntos" in rest  # 600-900 s en cada uno de los 16 partidos
    assert "el cambio vale +10.0 por 40" in rest


def test_plan_insights_say_when_our_usual_rotation_is_already_the_best(rival_rows, own_rows, segments):
    plan = rpl.build_plan(rival_rows, own_rows, _fit(), segments, "own", _OWN_START, top=1, key_players=_KEY)

    lines = rpl.plan_insights("Rival", plan, {})

    assert "ya es de lo mejor" in lines[1]
    assert "(560 min reales juntos" in lines[1]


def test_plan_insights_compact_fit_on_a_slide(rival_rows, own_rows, segments):
    plan = rpl.build_plan(
        rival_rows, own_rows, _fit(), segments, "own", _OWN, is_home=True, top=1, key_players=_KEY
    )

    lines = rpl.plan_insights("Rival", plan, {p: p.upper() for p in _OWN}, compact=True)

    assert lines[1] == (
        "Min 8-12 (descansa STAR, -80.0 por 40): O1 · O2 · O3 · O6 · O7, +14.0 contra su quinteto "
        "(+10.0 sobre lo habitual)."
    )
    assert all(len(line) < 160 for line in lines)


# --------------------------------------------------------------- con la BD --


@pytest.fixture()
def engine():
    st.cache_data.clear()
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    yield eng
    st.cache_data.clear()


_DB_VAL = ["v1", "v2", "v3", "v4", "v5", "v6", "v7"]
_DB_BAS_START = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
_DB_BAS_BENCH = ["howard", "moneke", "nikos", "lutse", "costello"]
_DB_STINTS = [
    ("bas", 0.0, 600.0, 16, 10, 0, _DB_BAS_START),
    ("bas", 600.0, 900.0, 9, 5, 6, _DB_BAS_BENCH),
    ("bas", 900.0, 2400.0, 40, 40, 10, _DB_BAS_START),
    ("val", 0.0, 420.0, 10, 10, 0, _DB_VAL[:5]),
    ("val", 420.0, 720.0, 0, 10, 0, ["v6", "v2", "v3", "v4", "v5"]),
    ("val", 720.0, 2400.0, 45, 45, -10, _DB_VAL[:5]),
]


def _seed_db(engine, stints=_DB_STINTS):
    """Valencia (`v1..v6`) y los tramos dados en el partido `g5` del seed (temporada 1)."""
    val = _DB_VAL
    with engine.begin() as conn:
        for i, pid in enumerate(val):
            conn.execute(
                text("INSERT INTO players (id, team_id, name, number, position) VALUES (:p, 'val', :n, :i, '')"),
                {"p": pid, "n": f"Valencia {i}", "i": i},
            )
        for team, start, end, pf, pa, margin, players in stints:
            stint_id = conn.execute(
                text(
                    "INSERT INTO lineup_stints (game_id, team_id, start_seconds, end_seconds, points_for,"
                    " points_against, margin_start) VALUES ('g5', :t, :s, :e, :pf, :pa, :m)"
                ),
                {"t": team, "s": start, "e": end, "pf": pf, "pa": pa, "m": margin},
            ).lastrowid
            for pid in players:
                conn.execute(
                    text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                    {"s": stint_id, "p": pid},
                )


def test_plan_from_the_database_end_to_end(engine):
    """`team_stint_rows` de los dos equipos + ajuste de la temporada → plan con nombres reales."""
    _seed_db(engine)
    from app.data import queries_assistant

    data = queries_assistant.season_impact(engine, 1, None)
    plan = rpl.build_plan(
        queries.team_stint_rows(engine, "val", 1),
        queries.team_stint_rows(engine, "bas", 1),
        data["fit"], data["segments"], "bas",
        rpl.default_candidates(data["segments"], "bas", min_minutes=0),
        is_home=True, key_players=_KEY,
    )
    lines = rpl.plan_insights("Valencia Basket", plan, data["names"])

    rest = next(w for w in plan if w["kind"] == "descanso")
    assert rest["player_id"] == "v1" and rest["label"] == "8-12"
    assert "v1" not in rest["rival_five"]
    assert any("cuando descansa Valencia 0" in line for line in lines)
    assert any("Marcus Howard" in line for line in lines)


#: Valencia con `v1` como jugador de MÁS minutos (los demás titulares también
#: descansan, seis minutos cada pareja), para que su descanso 8-12 salga con
#: los valores por defecto (`KEY_PLAYERS`), como en el dossier.
_DB_VAL_KEY = [
    ("val", 0.0, 420.0, 10, 10, 0, _DB_VAL[:5]),
    ("val", 420.0, 720.0, 0, 10, 0, ["v6", "v2", "v3", "v4", "v5"]),
    ("val", 720.0, 1500.0, 45, 45, -10, _DB_VAL[:5]),
    ("val", 1500.0, 1860.0, 0, 0, -10, ["v1", "v6", "v7", "v4", "v5"]),
    ("val", 1860.0, 2000.0, 0, 0, -10, _DB_VAL[:5]),
    ("val", 2000.0, 2360.0, 0, 0, -10, ["v1", "v2", "v3", "v6", "v7"]),
    ("val", 2360.0, 2400.0, 0, 0, -10, _DB_VAL[:5]),
]


def test_rotation_plan_bullets_for_the_dossier(engine):
    from app.reports import scouting_ppt

    _seed_db(engine, _DB_STINTS[:3] + _DB_VAL_KEY)

    bullets = scouting_ppt.rotation_plan_bullets(engine, "val", "bas", 1, "Valencia Basket", True)

    assert any(line.startswith("Min 8-12 (descansa Valencia 0") for line in bullets)
    assert all("contra su quinteto" in line for line in bullets)


def test_rotation_plan_bullets_skip_the_slide_without_our_lineups(engine):
    """Regresión: sin tramos propios salía una diapositiva de "plan" con solo las ventanas del rival."""
    from app.reports import scouting_ppt

    _seed_db(engine, _DB_VAL_KEY)

    assert queries.team_stint_rows(engine, "val", 1).shape[0] > 0
    assert scouting_ppt.rotation_plan_bullets(engine, "val", "bas", 1, "Valencia Basket", True) == []
