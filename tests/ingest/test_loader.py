"""Tests del volcado idempotente de un partido normalizado (`ingest.common.loader`)."""
from ingest.common.loader import list_existing_external_ids, load_game
from ingest.common.schema_types import (
    GameAdvancedStat,
    KeyEvent,
    LineupRecord,
    NormalizedGame,
    PlayerAdvancedStat,
    PlayerGameStat,
    PlayerQuarterStat,
    PlayEvent,
    QuarterStat,
    ScoreStep,
    ShotRecord,
    StintRecord,
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
            GameAdvancedStat(
                team_id="bas", efg_pct=55.0, ts_pct=58.0, tov_pct=12.0, orb_pct=25.0, ortg=112.0, drtg=108.0, net_rating=4.0,
                # Fase 1: boxscore ampliado de equipo.
                stl=7, tov=11, blk=3, blk_against=2, pf=18, pf_drawn=20, oreb=9, dreb=27, plus_minus=2, pir=108,
            ),
        ],
        boxscore=[
            PlayerGameStat(
                player_id="howard", minutes=30.0, pts=20, reb=3, ast=5, efg_pct=58.0,
                # Fase 1: boxscore ampliado de jugador.
                stl=2, tov=1, blk=0, blk_against=1, pf=2, pf_drawn=3, oreb=1, dreb=2, plus_minus=6, pir=24, dunks=1,
            ),
        ],
        # Fase 2: play-by-play tipado — dos faltas de "bas" y una de "rm" en
        # el primer cuarto, para comprobar que el loader deriva
        # `fouls_for`/`fouls_against` de `game_team_quarter_stats` a partir
        # de esto, no del adapter.
        play_events=[
            PlayEvent(team_id="bas", player_id="howard", quarter="Q1", game_clock="08:00", seconds=2320.0,
                       event_type="foul_personal", home_score=2, away_score=0),
            PlayEvent(team_id="bas", player_id=None, quarter="Q1", game_clock="05:00", seconds=2500.0,
                       event_type="foul_personal", home_score=4, away_score=2),
            PlayEvent(team_id="rm", player_id=None, quarter="Q1", game_clock="03:00", seconds=2580.0,
                       event_type="foul_personal", home_score=6, away_score=2),
            PlayEvent(team_id="bas", player_id="howard", quarter="Q1", game_clock="07:00", seconds=2380.0,
                       event_type="steal", home_score=2, away_score=0),
        ],
        quarter_stats=[
            QuarterStat(team_id="bas", quarter=1, points_for=20, points_against=18),
            QuarterStat(team_id="rm", quarter=1, points_for=18, points_against=20),
        ],
        # Fase 3: metadata de partido + boxscore de jugador por cuarto (ACB-only).
        arena="Fernando Buesa Arena", attendance=8200,
        referees="Antonio Conde · Martín Caballero", home_coach="Pedro Martínez", away_coach="Xavi Pascual",
        quarter_boxscore=[
            PlayerQuarterStat(player_id="howard", quarter=1, pts=6, reb=1, ast=2, stl=1, pf=1),
        ],
        # Fase 4: avanzadas oficiales por jugador (ACB-only).
        player_advanced=[
            PlayerAdvancedStat(player_id="howard", ts_pct=61.2, ppt=1.3, possessions=71.0, pace=345.0),
        ],
        lineups=[
            LineupRecord(
                player_ids=["howard", "moneke", "codi", "sedekerskis", "kotsar"],
                minutes=15.0, plus_minus=6, team_id="bas",
            ),
        ],
        stints=[
            StintRecord(
                team_id="bas",
                player_ids=["howard", "moneke", "codi", "sedekerskis", "kotsar"],
                start_seconds=2100.0, end_seconds=2400.0,
                points_for=10, points_against=6, margin_start=-2,
            ),
        ],
        shots=[
            ShotRecord(player_id="howard", team_id="bas", pos_x=250, pos_y=400, made=True, zone_id=1),
            ShotRecord(player_id="howard", team_id="bas", pos_x=260, pos_y=410, made=False, zone_id=1),
            # Sin coordenadas en origen (un mate de ACB): se carga igual, marcado.
            ShotRecord(player_id="howard", team_id="bas", pos_x=250, pos_y=455, made=True, zone_id=1, located=False),
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
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-99001'")).scalar_one() == 3
        # `located` viaja hasta la BD (por defecto 1; 0 solo el que lo pide).
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-99001' AND located=0")).scalar_one() == 1
        assert conn.execute(text("SELECT fg_pct, volume FROM game_zone_stats WHERE game_id='acb-99001' AND team_id='bas' AND zone_id=1")).first() == (66.7, 3)
        assert conn.execute(text("SELECT COUNT(*) FROM key_events WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM score_progression WHERE game_id='acb-99001'")).scalar_one() == 2
        # Equipo explícito del quinteto: sin esto había que inferirlo por el
        # equipo ACTUAL de sus jugadores (ver `lineups.team_id` en schema.sql).
        assert conn.execute(text("SELECT team_id FROM lineups WHERE game_id='acb-99001'")).scalar_one() == "bas"
        # Tramos con reloj y marcador: lo que hace contestable el "clutch".
        assert conn.execute(
            text("SELECT start_seconds, end_seconds, margin_start FROM lineup_stints WHERE game_id='acb-99001'")
        ).first() == (2100.0, 2400.0, -2)
        assert conn.execute(
            text("SELECT COUNT(*) FROM lineup_stint_players sp JOIN lineup_stints s ON s.id=sp.stint_id"
                 " WHERE s.game_id='acb-99001'")
        ).scalar_one() == 5

        # Fase 1: boxscore ampliado, equipo y jugador.
        assert conn.execute(
            text("SELECT stl, tov, blk, blk_against, pf, pf_drawn, oreb, dreb, plus_minus, pir"
                 " FROM game_advanced_stats WHERE game_id='acb-99001'")
        ).first() == (7, 11, 3, 2, 18, 20, 9, 27, 2, 108)
        assert conn.execute(
            text("SELECT stl, tov, blk, pf, plus_minus, pir, dunks"
                 " FROM player_game_stats WHERE game_id='acb-99001'")
        ).first() == (2, 1, 0, 2, 6, 24, 1)

        # Fase 2: play-by-play tipado (4 eventos) + faltas por cuarto
        # DERIVADAS por el loader (2 de "bas", 1 de "rm" en Q1 — el robo no cuenta).
        assert conn.execute(text("SELECT COUNT(*) FROM play_events WHERE game_id='acb-99001'")).scalar_one() == 4
        assert conn.execute(
            text("SELECT fouls_for, fouls_against FROM game_team_quarter_stats"
                 " WHERE game_id='acb-99001' AND team_id='bas' AND quarter=1")
        ).first() == (2, 1)
        assert conn.execute(
            text("SELECT fouls_for, fouls_against FROM game_team_quarter_stats"
                 " WHERE game_id='acb-99001' AND team_id='rm' AND quarter=1")
        ).first() == (1, 2)

        # Fase 3: metadata de partido + boxscore de jugador por cuarto.
        assert conn.execute(
            text("SELECT arena, attendance, home_coach, away_coach FROM games WHERE id='acb-99001'")
        ).first() == ("Fernando Buesa Arena", 8200, "Pedro Martínez", "Xavi Pascual")
        assert conn.execute(
            text("SELECT pts, stl FROM player_game_quarter_stats"
                 " WHERE game_id='acb-99001' AND player_id='howard' AND quarter=1")
        ).first() == (6, 1)

        # Fase 4: avanzadas oficiales por jugador.
        assert conn.execute(
            text("SELECT ts_pct, ppt, pace FROM player_advanced_stats WHERE game_id='acb-99001' AND player_id='howard'")
        ).first() == (61.2, 1.3, 345.0)


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
        assert conn.execute(text("SELECT COUNT(*) FROM lineup_stints WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(
            text("SELECT COUNT(*) FROM lineup_stint_players sp JOIN lineup_stints s ON s.id=sp.stint_id"
                 " WHERE s.game_id='acb-99001'")
        ).scalar_one() == 5
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-99001'")).scalar_one() == 3
        assert conn.execute(text("SELECT COUNT(*) FROM key_events WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM play_events WHERE game_id='acb-99001'")).scalar_one() == 4
        assert conn.execute(text("SELECT COUNT(*) FROM player_game_quarter_stats WHERE game_id='acb-99001'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM player_advanced_stats WHERE game_id='acb-99001'")).scalar_one() == 1


def test_list_existing_external_ids_filtra_por_fuente_y_temporada(engine):
    """Devuelve solo los ids de la fuente/temporada pedidas, sin el prefijo."""
    with engine.begin() as conn:
        load_game(conn, _sample_game())  # acb-99001, season_id=1 ('2025-2026')

    assert list_existing_external_ids(engine, "acb", 2025) == {"99001"}
    # El seed de `schema.sql` carga partidos 'g1'..'g5' (sin prefijo de fuente):
    # no deben colarse en el resultado de ninguna fuente real.
    assert list_existing_external_ids(engine, "euroleague", 2025) == set()
    # Otra temporada: mismo partido, pero el label no coincide.
    assert list_existing_external_ids(engine, "acb", 2024) == set()
