"""Tests de las consultas de la propuesta 10 (`app/data/queries.py`, `app/data/queries_assistant.py`).

Usan una temporada NUEVA (`season_id=2`) sobre el seed real, igual que
`test_queries_zone_matchup.py`: jugadores y equipos ya existen (`howard`,
`kotsar`, `bas`, `rm`), solo hace falta calendario y estadísticas propias.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import team_game_advanced_log, team_game_zone_counts, team_player_game_log
from app.data.queries_assistant import pair_minutes_by_window

SEASON_ID = 2
N_GAMES = 9  # 5 recientes + 4 de referencia, para que las ventanas de la propuesta 10 tengan algo que partir


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_games(conn) -> None:
    """9 partidos de `bas` como local contra `rm`, uno cada 3 días desde el 1 de octubre."""
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
    for i in range(1, N_GAMES + 1):
        conn.execute(
            text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                " game_date, home_score, away_score, pace) VALUES"
                " (:id, 2, 1, 'bas', 'rm', :date, 80, 75, :pace)"
            ),
            {"id": f"gx{i}", "date": f"2026-10-{i:02d}", "pace": 68.0 + i},
        )
        conn.execute(
            text(
                "INSERT INTO game_advanced_stats (game_id, team_id, efg_pct, ts_pct, tov_pct, orb_pct, ft_rate)"
                " VALUES (:g, 'bas', :efg, 55.0, 13.0, 25.0, 22.0)"
            ),
            {"g": f"gx{i}", "efg": 48.0 + i},  # sube monótonamente: los últimos 5 partidos son los más altos
        )
        conn.execute(
            text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct,"
                " tov, pf, oreb, ftm, fta) VALUES (:g, 'howard', 25.0, 12, 4, 3, 50.0, 2, 2, 1, 2, 3)"
            ),
            {"g": f"gx{i}"},
        )
    # Triples de Howard solo en los tres últimos partidos con coordenadas —
    # los seis primeros se quedan sin fila de `shots`, que es justo el caso
    # "sin dato" que `team_player_game_log` tiene que dejar en NaN, no en 0.
    for i in (7, 8, 9):
        for made in (1, 0):
            conn.execute(
                text(
                    "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located)"
                    " VALUES (:g, 'howard', 6, 250, 100, :made, 1)"
                ),
                {"g": f"gx{i}", "made": made},
            )


def _add_stint(conn, game_id: str, players, seconds: float = 300.0) -> None:
    result = conn.execute(
        text(
            "INSERT INTO lineup_stints (game_id, team_id, start_seconds, end_seconds, points_for,"
            " points_against, margin_start) VALUES (:g, 'bas', 0, :secs, 6, 4, 0)"
        ),
        {"g": game_id, "secs": seconds},
    )
    for player_id in players:
        conn.execute(
            text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
            {"s": result.lastrowid, "p": player_id},
        )


def test_team_player_game_log_returns_one_row_per_played_game(engine):
    with engine.begin() as conn:
        _seed_games(conn)

    log = team_player_game_log.__wrapped__(engine, "bas", SEASON_ID)

    assert len(log) == N_GAMES
    assert set(log["player_id"]) == {"howard"}
    assert log["minutes"].tolist() == [25.0] * N_GAMES
    assert log["tov"].tolist() == [2] * N_GAMES
    # Cronológico
    assert log["game_date"].tolist() == sorted(log["game_date"].tolist())


def test_team_player_game_log_distinguishes_no_shot_data_from_zero_triples(engine):
    with engine.begin() as conn:
        _seed_games(conn)

    log = team_player_game_log.__wrapped__(engine, "bas", SEASON_ID)
    by_game = log.set_index("game_id")

    assert by_game.loc["gx1", "tpm"] != by_game.loc["gx1", "tpm"]  # NaN: sin tiros con coordenadas
    assert by_game.loc["gx9", "tpm"] == 1  # de las dos filas de shots.made insertadas (1, 0)
    assert by_game.loc["gx9", "tpa"] == 2


def test_team_player_game_log_ignores_inactive_players(engine):
    with engine.begin() as conn:
        _seed_games(conn)
        conn.execute(text("UPDATE players SET active = 0 WHERE id = 'howard'"))

    log = team_player_game_log.__wrapped__(engine, "bas", SEASON_ID)
    assert log.empty


def test_team_game_advanced_log_carries_pace_and_four_factors(engine):
    with engine.begin() as conn:
        _seed_games(conn)

    log = team_game_advanced_log.__wrapped__(engine, "bas", SEASON_ID)

    assert len(log) == N_GAMES
    assert log["game_date"].tolist() == sorted(log["game_date"].tolist())
    first, last = log.iloc[0], log.iloc[-1]
    assert first["efg_pct"] == pytest.approx(49.0)  # partido 1: 48 + 1
    assert last["efg_pct"] == pytest.approx(48.0 + N_GAMES)
    assert last["pace"] == pytest.approx(68.0 + N_GAMES)


def test_team_game_zone_counts_returns_one_row_per_game_and_zone(engine):
    """`_seed_games` solo pone tiros (zona 6) en los partidos 7-9, dos filas cada uno (made=1,
    made=0) que la consulta agrega en una sola fila por (partido, zona, localizado)."""
    with engine.begin() as conn:
        _seed_games(conn)

    zone_counts = team_game_zone_counts.__wrapped__(engine, "bas", SEASON_ID)

    assert set(zone_counts["game_id"]) == {"gx7", "gx8", "gx9"}
    row = zone_counts[zone_counts["game_id"] == "gx7"].iloc[0]
    assert row["zone_id"] == 6
    assert row["shots"] == 2
    assert row["made"] == 1
    assert row["competition"]
    assert zone_counts["game_date"].tolist() == sorted(zone_counts["game_date"].tolist())


def test_team_game_zone_counts_empty_without_shots(engine):
    with engine.begin() as conn:
        _seed_games(conn)
    assert team_game_zone_counts.__wrapped__(engine, "rm", SEASON_ID).empty  # 'rm' nunca tira, solo es el rival


def test_pair_minutes_by_window_splits_by_game_date(engine):
    with engine.begin() as conn:
        _seed_games(conn)
        # Baseline (partidos 1-4): Howard y Kotsar comparten poco.
        for i in range(1, 5):
            _add_stint(conn, f"gx{i}", ["howard", "kotsar"], seconds=60.0)
            _add_stint(conn, f"gx{i}", ["howard"], seconds=540.0)
        # Recientes (partidos 5-9): comparten mucho más.
        for i in range(5, 10):
            _add_stint(conn, f"gx{i}", ["howard", "kotsar"], seconds=480.0)
            _add_stint(conn, f"gx{i}", ["howard"], seconds=120.0)

    pairs = pair_minutes_by_window.__wrapped__(engine, "bas", SEASON_ID, last_n_games=5)

    assert not pairs.empty
    row = pairs[pairs["player_ids"] == "howard,kotsar"].iloc[0]
    assert row["n_recent_games"] == 5
    assert row["n_baseline_games"] == 4
    assert row["recent_share"] == pytest.approx(80.0)   # 480 / (480+120)
    assert row["baseline_share"] == pytest.approx(10.0)  # 60 / (60+540)
    assert row["recent_share"] > row["baseline_share"]


def test_pair_minutes_by_window_empty_without_enough_games_on_each_side(engine):
    with engine.begin() as conn:
        _seed_games(conn)
        for i in range(1, 4):  # solo 3 partidos con tramos: no llega a last_n_games + PAIR_WINDOW_MIN_GAMES
            _add_stint(conn, f"gx{i}", ["howard", "kotsar"])

    pairs = pair_minutes_by_window.__wrapped__(engine, "bas", SEASON_ID, last_n_games=5)
    assert pairs.empty


def test_pair_minutes_by_window_empty_without_any_stints(engine):
    with engine.begin() as conn:
        _seed_games(conn)
    assert pair_minutes_by_window.__wrapped__(engine, "bas", SEASON_ID, last_n_games=5).empty
