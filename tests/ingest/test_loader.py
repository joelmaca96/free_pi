"""Tests del volcado idempotente de un partido normalizado (`ingest.common.loader`)."""
from ingest.common.loader import load_game
from ingest.common.schema_types import (
    GameAdvancedStat,
    KeyEvent,
    LineupRecord,
    NormalizedGame,
    PlayerGameStat,
    ScoreStep,
    ShotRecord,
)


def _sample_game() -> NormalizedGame:
    return NormalizedGame(
        id="acb-99001",
        season_id=1,
        competition_id=1,
        home_team_id="bas",
        away_team_id="rm",
        game_date="2026-02-10",
        home_score=90,
        away_score=88,
        pace=72.5,
        narrative="Partido de prueba",
        advanced=[
            GameAdvancedStat(team_id="bas", efg_pct=55.0, ts_pct=58.0, tov_pct=12.0, orb_pct=25.0, ortg=112.0, drtg=108.0, net_rating=4.0),
        ],
        boxscore=[
            PlayerGameStat(player_id="howard", minutes=30.0, pts=20, reb=3, ast=5, efg_pct=58.0),
        ],
        lineups=[
            LineupRecord(player_ids=["howard", "moneke", "codi", "sedekerskis", "kotsar"], minutes=15.0, plus_minus=6),
        ],
        shots=[
            ShotRecord(player_id="howard", team_id="bas", pos_x=250, pos_y=400, made=True, zone_id=1),
            ShotRecord(player_id="howard", team_id="bas", pos_x=260, pos_y=410, made=False, zone_id=1),
        ],
        key_events=[KeyEvent(team_id="bas", quarter="Q4", game_clock="00:30", label="Triple decisivo")],
        score_progression=[ScoreStep(step_index=0, home_score=0, away_score=0), ScoreStep(step_index=1, home_score=2, away_score=0)],
    )


def test_load_game_inserts_all_child_tables(engine):
    with engine.begin() as conn:
        load_game(conn, _sample_game())

    with engine.connect() as conn:
        from sqlalchemy import text

        assert conn.execute(text("SELECT home_score, away_score FROM games WHERE id='acb-99001'")).first() == (90, 88)
        assert conn.execute(text("SELECT net_rating FROM game_advanced_stats WHERE game_id='acb-99001'")).scalar_one() == 4.0
        assert conn.execute(text("SELECT pts FROM player_game_stats WHERE game_id='acb-99001'")).scalar_one() == 20
        assert conn.execute(text("SELECT COUNT(*) FROM lineups WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM lineup_players lp JOIN lineups l ON l.id=lp.lineup_id WHERE l.game_id='acb-99001'")).scalar_one() == 5
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-99001'")).scalar_one() == 2
        assert conn.execute(text("SELECT fg_pct, volume FROM game_zone_stats WHERE game_id='acb-99001' AND team_id='bas' AND zone_id=1")).first() == (50.0, 2)
        assert conn.execute(text("SELECT COUNT(*) FROM key_events WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM score_progression WHERE game_id='acb-99001'")).scalar_one() == 2


def test_load_game_is_idempotent_on_rerun(engine):
    game = _sample_game()
    with engine.begin() as conn:
        load_game(conn, game)
    with engine.begin() as conn:
        load_game(conn, game)  # segunda ejecución: no debe duplicar ni fallar

    from sqlalchemy import text

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM games WHERE id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM player_game_stats WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM lineups WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-99001'")).scalar_one() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM key_events WHERE game_id='acb-99001'")).scalar_one() == 1
