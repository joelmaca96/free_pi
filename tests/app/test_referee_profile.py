"""Tests de las consultas de perfil arbitral (Fase 5, `app/data/queries_assistant.py`).

`doc/features/propuestas/05_perfil_arbitral.md` §4 fija el mecanismo a
probar: el ajuste OBLIGATORIO por competición (el residuo, no la media
bruta, es lo que ordena el ranking) y los umbrales de presentación (>=15
partidos entra en el ranking, 8-14 se enseña con aviso, <8 no se enseña).
"""
import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data import queries_assistant


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture()
def engine():
    """BD en memoria con el esquema + seed reales (temporada 2025-2026, season_id=1)."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _add_officiated_game(
    conn, game_id, *, competition_id=1, game_date, pf_home, pf_away, fta_home, fta_away,
    pace=70.0, referees=None, home_score=80, away_score=70,
):
    """Inserta un partido bas-rm con faltas/TL de equipo y, opcionalmente, terna.

    Siempre local 'bas' / visitante 'rm' (ya existen en el seed): lo único que
    varía entre llamadas es la fecha (para no chocar con la `UNIQUE` de
    `games`), así que un test entero puede describirse solo con los números
    que le importan.
    """
    conn.execute(
        text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace)"
            " VALUES (:id, 1, :comp, 'bas', 'rm', :date, :hs, :as_, :pace)"
        ),
        {"id": game_id, "comp": competition_id, "date": game_date, "hs": home_score, "as_": away_score, "pace": pace},
    )
    for team_id, pf, fta in (("bas", pf_home, fta_home), ("rm", pf_away, fta_away)):
        conn.execute(
            text(
                "INSERT INTO game_advanced_stats (game_id, team_id, efg_pct, ts_pct, tov_pct, orb_pct, pf, fta)"
                " VALUES (:g, :team_id, 50.0, 55.0, 12.0, 25.0, :pf, :fta)"
            ),
            {"g": game_id, "team_id": team_id, "pf": pf, "fta": fta},
        )
    for position, name in enumerate(referees or [], start=1):
        conn.execute(
            text("INSERT INTO game_referees (game_id, referee_name, position) VALUES (:g, :n, :p)"),
            {"g": game_id, "n": name, "p": position},
        )


@pytest.fixture()
def ranked_engine(engine):
    """Escenario con tres árbitros de muestra distinta sobre un fondo de
    partidos "de relleno" que fija la media de la competición ACB en 40
    faltas totales/partido — así el residuo de cada árbitro es un número
    exacto y fácil de comprobar a mano.

    - "Referee Alto": 16 partidos a 50 faltas totales (residuo +10), con
      sesgo local claro (bas, local, recibe más TL que rm). Sample 'ok'.
    - "Referee Bajo": 16 partidos a 30 faltas totales (residuo -10). Sample 'ok'.
    - "Referee Medio": 10 partidos a 40 faltas totales (residuo 0). Sample 'caution'.
    - "Referee Poco": 5 partidos (por debajo del mínimo para enseñarse).
    """
    with engine.begin() as conn:
        for i in range(30):
            _add_officiated_game(
                conn, f"filler-{i}", game_date=f"2025-10-{i + 1:02d}",
                pf_home=20, pf_away=20, fta_home=20, fta_away=20,
            )
        for i in range(16):
            _add_officiated_game(
                conn, f"alto-{i}", game_date=f"2025-11-{i + 1:02d}",
                pf_home=25, pf_away=25, fta_home=15, fta_away=10,
                referees=["Referee Alto"],
            )
        for i in range(16):
            _add_officiated_game(
                conn, f"bajo-{i}", game_date=f"2025-12-{i + 1:02d}",
                pf_home=15, pf_away=15, fta_home=10, fta_away=10,
                referees=["Referee Bajo"],
            )
        for i in range(10):
            _add_officiated_game(
                conn, f"medio-{i}", game_date=f"2026-01-{i + 1:02d}",
                pf_home=20, pf_away=20, fta_home=10, fta_away=10,
                referees=["Referee Medio"],
            )
        for i in range(5):
            _add_officiated_game(
                conn, f"poco-{i}", game_date=f"2026-02-{i + 1:02d}",
                pf_home=20, pf_away=20, fta_home=10, fta_away=10,
                referees=["Referee Poco"],
            )
    return engine


# ---------- referees_for_game ----------


def test_referees_for_game_returns_the_terna_in_order(engine):
    with engine.begin() as conn:
        _add_officiated_game(
            conn, "g100", game_date="2026-03-01", pf_home=20, pf_away=20, fta_home=10, fta_away=10,
            referees=["Uno", "Dos", "Tres"],
        )
    df = queries_assistant.referees_for_game(engine, "g100")
    assert list(df["referee_name"]) == ["Uno", "Dos", "Tres"]
    assert list(df["position"]) == [1, 2, 3]


def test_referees_for_game_is_empty_without_a_terna(engine):
    """`g1` del seed no tiene fila en `game_referees` (nunca se cargó terna)."""
    assert queries_assistant.referees_for_game(engine, "g1").empty


# ---------- referee_rankings ----------


def test_rankings_excludes_referees_below_the_minimum_to_show(ranked_engine):
    df = queries_assistant.referee_rankings(ranked_engine, 1)
    assert "Referee Poco" not in set(df["referee_name"])
    assert {"Referee Alto", "Referee Bajo", "Referee Medio"} <= set(df["referee_name"])


def test_rankings_pf_residual_is_adjusted_by_competition_average(ranked_engine):
    """40 faltas totales/partido de media en los partidos de relleno (ACB):
    +10/-10/0 de residuo son exactos con esos números."""
    df = queries_assistant.referee_rankings(ranked_engine, 1).set_index("referee_name")
    assert df.loc["Referee Alto", "pf_residual"] == pytest.approx(10.0)
    assert df.loc["Referee Bajo", "pf_residual"] == pytest.approx(-10.0)
    assert df.loc["Referee Medio", "pf_residual"] == pytest.approx(0.0)


def test_rankings_sample_size_flags_ok_vs_caution(ranked_engine):
    df = queries_assistant.referee_rankings(ranked_engine, 1).set_index("referee_name")
    assert df.loc["Referee Alto", "sample_size"] == "ok"       # 16 partidos
    assert df.loc["Referee Bajo", "sample_size"] == "ok"       # 16 partidos
    assert df.loc["Referee Medio", "sample_size"] == "caution"  # 10 partidos


def test_rankings_percentile_is_computed_within_the_ok_pool_only(ranked_engine):
    """El percentil de faltas compara solo contra el grupo de muestra plena
    ('ok'): "Medio" (10 partidos, 'caution') no puede mover el percentil de
    los otros dos ni tener uno propio fiable."""
    df = queries_assistant.referee_rankings(ranked_engine, 1).set_index("referee_name")
    # Pool 'ok' = {Alto: +10, Bajo: -10}. Alto es más alto que TODO el resto
    # del pool (Bajo) -> percentil 1.0; Bajo no es más alto que nadie -> 0.0.
    assert df.loc["Referee Alto", "pf_residual_pct"] == pytest.approx(1.0)
    assert df.loc["Referee Bajo", "pf_residual_pct"] == pytest.approx(0.0)


def test_rankings_home_bias_reflects_the_free_throw_gap(ranked_engine):
    """"Referee Alto" da 15 TL de casa (bas) por 10 de fuera (rm) en cada
    partido: sesgo local exacto de +5."""
    df = queries_assistant.referee_rankings(ranked_engine, 1).set_index("referee_name")
    assert df.loc["Referee Alto", "home_bias_fta"] == pytest.approx(5.0)
    assert df.loc["Referee Bajo", "home_bias_fta"] == pytest.approx(0.0)


def test_rankings_sorted_by_pf_residual_descending(ranked_engine):
    df = queries_assistant.referee_rankings(ranked_engine, 1)
    assert list(df["referee_name"]) == ["Referee Alto", "Referee Medio", "Referee Bajo"]


def test_rankings_is_empty_without_the_game_referees_table():
    """Una BD sin `game_referees` (Fase 5 sin migrar todavía) no revienta."""
    eng = create_scouting_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE seasons (id INTEGER PRIMARY KEY, label TEXT)"))
    assert queries_assistant.referee_rankings(eng, 1).empty
    eng.dispose()


# ---------- referee_profile ----------


def test_profile_returns_the_matching_ranking_row(ranked_engine):
    profile = queries_assistant.referee_profile(ranked_engine, "Referee Alto", 1)
    assert profile is not None
    assert profile["gp"] == 16
    assert profile["pf_residual"] == pytest.approx(10.0)


def test_profile_is_none_below_the_minimum_sample(ranked_engine):
    """"Referee Poco" tiene 5 partidos: por debajo del mínimo, no se muestra (§4)."""
    assert queries_assistant.referee_profile(ranked_engine, "Referee Poco", 1) is None


def test_profile_is_none_for_an_unknown_name(ranked_engine):
    assert queries_assistant.referee_profile(ranked_engine, "Nadie De Nadie", 1) is None


# ---------- referee_team_history ----------


def test_team_history_reports_fouls_and_balance(ranked_engine):
    """'bas' gana todos los partidos de "Referee Alto" en el fixture (80-70) y
    recibe 25 faltas de media (`pf` de su propia fila)."""
    history = queries_assistant.referee_team_history(ranked_engine, "Referee Alto", 1, "bas")
    assert history is not None
    assert history["gp"] == 16
    assert history["wins"] == 16
    assert history["losses"] == 0
    assert history["pf_avg"] == pytest.approx(25.0)


def test_team_history_is_none_without_shared_games(ranked_engine):
    assert queries_assistant.referee_team_history(ranked_engine, "Referee Alto", 1, "fcb") is None


# ---------- player_referee_effect ----------


def test_player_referee_effect_compares_per_40_rates(engine):
    with engine.begin() as conn:
        # Dos partidos con "Referee X": howard cita 4 faltas en 30 minutos
        # (pf/40 = 5.33...).
        for i, game_id in enumerate(["px-1", "px-2"]):
            _add_officiated_game(
                conn, game_id, game_date=f"2026-04-0{i + 1}", pf_home=20, pf_away=20,
                fta_home=10, fta_away=10, referees=["Referee X"],
            )
            conn.execute(
                text(
                    "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct, pf)"
                    " VALUES (:g, 'howard', 30.0, 10, 3, 2, 50.0, 4)"
                ),
                {"g": game_id},
            )
        # Un partido con otro árbitro (o sin árbitro): howard con menos faltas,
        # para que la media de temporada sea distinta a la de "Referee X".
        _add_officiated_game(
            conn, "px-3", game_date="2026-04-03", pf_home=20, pf_away=20, fta_home=10, fta_away=10,
        )
        conn.execute(
            text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct, pf)"
                " VALUES ('px-3', 'howard', 30.0, 10, 3, 2, 50.0, 1)"
            )
        )

    effect = queries_assistant.player_referee_effect(engine, "howard", "Referee X", 1)
    assert effect is not None
    assert effect["gp"] == 2
    # 8 faltas en 60 minutos; redondeado a 2 decimales, como el resto de la función.
    assert effect["pf_per40"] == pytest.approx(round(40.0 * 8 / 60.0, 2))
    # Temporada completa: 9 faltas en 90 minutos (incluye los dos partidos de arriba + el tercero).
    assert effect["season_pf_per40"] == pytest.approx(round(40.0 * 9 / 90.0, 2))
    assert effect["diff"] == pytest.approx(round(effect["pf_per40"] - effect["season_pf_per40"], 2))


def test_player_referee_effect_is_none_without_games_under_that_referee(engine):
    assert queries_assistant.player_referee_effect(engine, "howard", "Referee Fantasma", 1) is None


# ---------- all_referee_names ----------


def test_all_referee_names_includes_low_sample_referees(ranked_engine):
    """Sin umbral: "Referee Poco" (5 partidos) tiene que poder buscarse igual,
    aunque no aparezca en `referee_rankings`."""
    names = queries_assistant.all_referee_names(ranked_engine)
    assert names == sorted(names)
    assert "Referee Poco" in names
    assert "Referee Alto" in names


def test_all_referee_names_is_empty_without_the_table():
    eng = create_scouting_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE seasons (id INTEGER PRIMARY KEY, label TEXT)"))
    assert queries_assistant.all_referee_names(eng) == []
    eng.dispose()
