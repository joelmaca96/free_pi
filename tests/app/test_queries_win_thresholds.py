"""Tests de `queries.game_factor_rows` (propuesta 09, umbrales de victoria).

Usan una temporada NUEVA (`season_id=2`) para no arrastrar el seed de
`schema.sql` en la temporada 1 y poder verificar los números a mano — mismo
criterio que `test_queries_zone_matchup.py`.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import game_factor_rows


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_two_games(conn) -> None:
    """Temporada 2: `bas` gana los dos, una vez de local (ACB) y otra de visitante (Euroliga)."""
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
    conn.execute(text(
        "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
        " game_date, home_score, away_score, pace) VALUES"
        " ('gx1', 2, 1, 'bas', 'rm', '2026-10-01', 80, 75, 70.0)"
    ))
    conn.execute(text(
        "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
        " game_date, home_score, away_score, pace) VALUES"
        " ('gx2', 2, 2, 'rm', 'bas', '2026-10-08', 78, 82, 72.0)"
    ))
    conn.execute(text(
        "INSERT INTO game_advanced_stats"
        " (game_id, team_id, efg_pct, ts_pct, tov_pct, orb_pct, ft_rate) VALUES"
        " ('gx1', 'bas', 50.0, 55.0, 12.0, 25.0, 30.0),"
        " ('gx1', 'rm',  45.0, 50.0, 15.0, 20.0, 25.0),"
        " ('gx2', 'bas', 52.0, 56.0, 10.0, 30.0, 28.0),"
        " ('gx2', 'rm',  48.0, 52.0, 14.0, 22.0, 26.0)"
    ))


def test_two_rows_per_game_with_opponent_columns_mirrored(engine):
    with engine.begin() as conn:
        _seed_two_games(conn)

    rows = game_factor_rows.__wrapped__(engine, 2)

    assert len(rows) == 4  # 2 partidos x 2 equipos
    bas_gx1 = rows.loc[(rows["game_id"] == "gx1") & (rows["team_id"] == "bas")].iloc[0]
    rm_gx1 = rows.loc[(rows["game_id"] == "gx1") & (rows["team_id"] == "rm")].iloc[0]

    assert bas_gx1["efg_pct"] == pytest.approx(50.0)
    assert bas_gx1["opp_efg_pct"] == pytest.approx(45.0)
    assert bas_gx1["opponent_team_id"] == "rm"
    # Mismo partido, filas espejo: lo que es "propio" en una es "opp_*" en la otra.
    assert rm_gx1["efg_pct"] == pytest.approx(45.0)
    assert rm_gx1["opp_efg_pct"] == pytest.approx(50.0)
    assert rm_gx1["opponent_team_id"] == "bas"


def test_win_follows_the_score_not_home_or_away(engine):
    with engine.begin() as conn:
        _seed_two_games(conn)

    rows = game_factor_rows.__wrapped__(engine, 2)

    # bas gana los dos partidos: el gx1 de local (80-75) y el gx2 de VISITANTE (82-78).
    assert rows.loc[(rows["game_id"] == "gx1") & (rows["team_id"] == "bas"), "win"].iloc[0] == 1
    assert rows.loc[(rows["game_id"] == "gx1") & (rows["team_id"] == "rm"), "win"].iloc[0] == 0
    assert rows.loc[(rows["game_id"] == "gx2") & (rows["team_id"] == "bas"), "win"].iloc[0] == 1
    assert rows.loc[(rows["game_id"] == "gx2") & (rows["team_id"] == "rm"), "win"].iloc[0] == 0


def test_season_filter_excludes_other_seasons(engine):
    with engine.begin() as conn:
        _seed_two_games(conn)

    assert game_factor_rows.__wrapped__(engine, 999).empty
    assert (game_factor_rows.__wrapped__(engine, 2)["season_id"] == 2).all()


def test_a_game_with_a_null_factor_on_either_side_is_excluded_entirely(engine):
    """`ft_rate` nullable a nivel de esquema (columna añadida sobre BDs ya cargadas, ver
    `schema.sql`): un partido con un hueco en un lado no debe colarse ni siquiera por el
    lado que sí lo tiene completo — el barrido de umbrales necesita los cuatro factores
    de LOS DOS equipos para poder calcular la batalla."""
    with engine.begin() as conn:
        _seed_two_games(conn)
        conn.execute(text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace) VALUES"
            " ('gx3', 2, 1, 'bas', 'fcb', '2026-10-15', 90, 88, 71.0)"
        ))
        conn.execute(text(
            "INSERT INTO game_advanced_stats"
            " (game_id, team_id, efg_pct, ts_pct, tov_pct, orb_pct, ft_rate) VALUES"
            " ('gx3', 'bas', 50.0, 55.0, 12.0, 25.0, NULL),"
            " ('gx3', 'fcb', 46.0, 51.0, 13.0, 24.0, 27.0)"
        ))

    rows = game_factor_rows.__wrapped__(engine, 2)

    assert "gx3" not in rows["game_id"].tolist()
    assert len(rows) == 4  # solo gx1/gx2, sin cambios


def test_returns_the_natural_unit_box_columns_too(engine):
    """§3 del documento: la caja larga viaja aunque `win_thresholds` no la use todavía."""
    with engine.begin() as conn:
        _seed_two_games(conn)
        conn.execute(text(
            "UPDATE game_advanced_stats SET stl = 8, blk = 3, oreb = 10, dreb = 25, pf = 18, pf_drawn = 16 "
            "WHERE game_id = 'gx1' AND team_id = 'bas'"
        ))

    rows = game_factor_rows.__wrapped__(engine, 2)
    bas_gx1 = rows.loc[(rows["game_id"] == "gx1") & (rows["team_id"] == "bas")].iloc[0]

    assert (bas_gx1["stl"], bas_gx1["blk"], bas_gx1["oreb"], bas_gx1["dreb"], bas_gx1["pf"], bas_gx1["pf_drawn"]) \
        == (8, 3, 10, 25, 18, 16)
