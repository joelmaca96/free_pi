"""Tests de las consultas de la propuesta 11 (similitud de jugadores).

`league_player_index`/`league_player_zone_volume` en `app/data/queries.py`
y `league_player_percentiles` en `app/data/queries_assistant.py` son la
materia prima de `app/analytics/similarity.py` — aquí solo se comprueba que
el SQL trae lo que dice traer, no la lógica de percentiles/distancia (eso
está en `test_similarity.py`, sin base de datos).

Usa una temporada NUEVA (`season_id=2`), mismo criterio que
`test_queries_zone_matchup.py`: no arrastrar lo que ya trae el seed de
`schema.sql` en la temporada 1.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import league_player_index, league_player_zone_volume, opponent_team_ids
from app.data.queries_assistant import league_player_percentiles


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _insert_games_and_stats(conn, games: list) -> None:
    """`games`: `(game_id, competition_id, home, away, home_score, away_score)`.
    Cada partido recibe boxscore de un jugador fijo por equipo (`<team>_p`), 20 minutos, 10 puntos."""
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
    for i, (game_id, competition_id, home, away, home_score, away_score) in enumerate(games):
        conn.execute(text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace) VALUES"
            " (:g, 2, :c, :h, :a, :date, :hs, :as_, 70.0)"
        ), {
            "g": game_id, "c": competition_id, "h": home, "a": away,
            "date": f"2026-10-{i + 1:02d}", "hs": home_score, "as_": away_score,
        })
        for team in (home, away):
            player_id = f"{team}_p"
            conn.execute(text(
                "INSERT OR IGNORE INTO players (id, team_id, name, number, position)"
                " VALUES (:p, :t, :p, 9, 'Base')"
            ), {"p": player_id, "t": team})
            conn.execute(text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                " VALUES (:g, :p, 20.0, 10, 4, 3, 50.0)"
            ), {"g": game_id, "p": player_id})


def test_league_player_index_totals_minutes_across_competitions(engine):
    """`minutes_total` = gp * min_avg de `player_stats_combined`, sumando TODAS las competiciones."""
    with engine.begin() as conn:
        _insert_games_and_stats(conn, [
            ("gx1", 1, "bas", "rm", 80, 75),
            ("gx2", 2, "bas", "fcb", 78, 82),
        ])

    index_df = league_player_index.__wrapped__(engine, 2)
    row = index_df.set_index("player_id").loc["bas_p"]

    assert row["gp_total"] == 2
    assert row["minutes_total"] == pytest.approx(40.0)  # 20 min en cada uno de los 2 partidos
    assert row["team_name"] == "Baskonia"


def test_league_player_index_excludes_players_without_games_this_season(engine):
    with engine.begin() as conn:
        _insert_games_and_stats(conn, [("gx1", 1, "bas", "rm", 80, 75)])

    index_df = league_player_index.__wrapped__(engine, 2)
    assert "fcb_p" not in set(index_df["player_id"])


def test_league_player_zone_volume_counts_shots_per_player_competition_and_zone(engine):
    with engine.begin() as conn:
        _insert_games_and_stats(conn, [("gx1", 1, "bas", "rm", 80, 75)])
        conn.execute(text(
            "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located) VALUES"
            " ('gx1', 'bas_p', 1, 250, 400, 1, 1),"
            " ('gx1', 'bas_p', 1, 250, 410, 0, 1),"
            " ('gx1', 'bas_p', 6, 250, 100, 1, 1)"
        ))

    volume = league_player_zone_volume.__wrapped__(engine, 2)
    by_zone = volume.loc[volume["player_id"] == "bas_p"].set_index("zone_id")["volume"]

    assert by_zone[1] == 2
    assert by_zone[6] == 1
    assert (volume.loc[volume["player_id"] == "bas_p", "competition_id"] == 1).all()


def test_league_player_zone_volume_ignores_shots_without_a_zone(engine):
    with engine.begin() as conn:
        _insert_games_and_stats(conn, [("gx1", 1, "bas", "rm", 80, 75)])
        conn.execute(text(
            "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located)"
            " VALUES ('gx1', 'bas_p', NULL, 0, 0, 1, 0)"
        ))

    volume = league_player_zone_volume.__wrapped__(engine, 2)
    assert volume.empty


def test_opponent_team_ids_finds_rivals_home_and_away(engine):
    with engine.begin() as conn:
        _insert_games_and_stats(conn, [
            ("gx1", 1, "bas", "rm", 80, 75),
            ("gx2", 2, "fcb", "bas", 78, 82),
        ])

    opponents = opponent_team_ids.__wrapped__(engine, "bas", 2)
    assert set(opponents) == {"rm", "fcb"}


def test_opponent_team_ids_of_a_team_with_no_games_is_empty(engine):
    with engine.begin() as conn:
        _insert_games_and_stats(conn, [("gx1", 1, "bas", "rm", 80, 75)])

    assert opponent_team_ids.__wrapped__(engine, "uni", 2) == []


def _make_percentile_pool(conn, team_prefix: str, competition_id: int, n_players: int, n_games: int) -> None:
    """`n_players` jugadores de un equipo sintético, cada uno con `n_games` partidos (>= 5, el
    mínimo de `player_percentiles`), puntuaciones crecientes para poder afirmar el orden del percentil."""
    team_id = f"{team_prefix}"
    conn.execute(text("INSERT OR IGNORE INTO teams (id, name, is_own_team) VALUES (:t, :t, 0)"), {"t": team_id})
    for p in range(n_players):
        player_id = f"{team_prefix}_{p}"
        conn.execute(text(
            "INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES (:p, :t, :p, :n, 'Base')"
        ), {"p": player_id, "t": team_id, "n": p})
        for g in range(n_games):
            game_id = f"{team_prefix}_{p}_{g}"
            conn.execute(text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                " game_date, home_score, away_score, pace) VALUES"
                " (:g, 2, :c, :t, :t, :date, 80, 75, 70.0)"
            ), {"g": game_id, "c": competition_id, "t": team_id, "date": f"2026-11-{p * n_games + g + 1:02d}"})
            conn.execute(text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                " VALUES (:g, :p, 20.0, :pts, 4, 3, 50.0)"
            ), {"g": game_id, "p": player_id, "pts": 5 + p})  # más puntos cuanto mayor el índice


def test_league_player_percentiles_covers_every_player_of_every_competition(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
        _make_percentile_pool(conn, "acbteam", 1, n_players=5, n_games=5)
        _make_percentile_pool(conn, "eurteam", 2, n_players=5, n_games=5)

    percentiles = league_player_percentiles.__wrapped__(engine, 2)

    assert set(percentiles["player_id"]) == {f"acbteam_{p}" for p in range(5)} | {f"eurteam_{p}" for p in range(5)}
    # El de más puntos de su competición sale en el percentil más alto, DENTRO de su competición.
    acb = percentiles.loc[percentiles["competition_id"] == 1].set_index("player_id")
    assert acb.loc["acbteam_4", "pts_pct"] == pytest.approx(1.0)
    assert acb.loc["acbteam_0", "pts_pct"] == pytest.approx(0.0)


def test_league_player_percentiles_excludes_players_below_the_five_game_minimum(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
        _make_percentile_pool(conn, "acbteam", 1, n_players=5, n_games=4)  # por debajo del mínimo de la vista

    percentiles = league_player_percentiles.__wrapped__(engine, 2)
    assert percentiles.empty
