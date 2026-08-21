"""Tests de la fuente Euroliga: adapter (DataFrame -> contrato común) y carga.

Columnas verificadas en vivo (ver `adapter.py`); no requieren `euroleague_api`
instalado: los DataFrames de entrada se construyen a mano con pandas (ya es
dependencia del proyecto), con las mismas columnas que devuelve la librería
real.
"""
from unittest import mock

import pandas as pd
import pytest
from sqlalchemy import text

from ingest.common.loader import load_game
from ingest.euroleague.adapter import build_raw_game
from ingest.euroleague.parser import parse_and_resolve
import ingest.euroleague.pipeline as pipeline_module
from ingest.euroleague.pipeline import run


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    """`pipeline.run` duerme entre partidos para no tumbar el rate-limit real; en tests, no."""
    monkeypatch.setattr(pipeline_module, "REQUEST_DELAY", 0.0)

METADATA = {
    "Gamecode": 305, "Season": 2025, "Date": "20/01/2026",
    "TeamA": "KOSNER BASKONIA", "CodeTeamA": "BAS",
    "TeamB": "REAL MADRID", "CodeTeamB": "MAD",
    "ScoreA": 91, "ScoreB": 88,
}

BOXSCORE_DF = pd.DataFrame(
    [
        {"Player_ID": "EL-HOWARD", "Player": "HOWARD, Marcus", "Team": "BAS", "Dorsal": 0, "IsStarter": 1.0,
         "Minutes": "31:00", "Points": 24, "TotalRebounds": 4, "Assistances": 5, "Turnovers": 2,
         "OffensiveRebounds": 1, "DefensiveRebounds": 3, "FieldGoalsMade2": 5, "FieldGoalsAttempted2": 8,
         "FieldGoalsMade3": 3, "FieldGoalsAttempted3": 7, "FreeThrowsMade": 3, "FreeThrowsAttempted": 3},
        {"Player_ID": "EL-RIVAL1", "Player": "RIVAL, Jugador", "Team": "MAD", "Dorsal": 9, "IsStarter": 1.0,
         "Minutes": "29:00", "Points": 18, "TotalRebounds": 6, "Assistances": 3, "Turnovers": 1,
         "OffensiveRebounds": 2, "DefensiveRebounds": 4, "FieldGoalsMade2": 6, "FieldGoalsAttempted2": 10,
         "FieldGoalsMade3": 2, "FieldGoalsAttempted3": 5, "FreeThrowsMade": 2, "FreeThrowsAttempted": 2},
    ]
)

SHOTS_DF = pd.DataFrame(
    [
        {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 0.0, "COORD_Y": 100.0, "ID_ACTION": "2FGM"},
        {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 50.0, "COORD_Y": 150.0, "ID_ACTION": "2FGA"},
        {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": -1, "COORD_Y": -1, "ID_ACTION": "FTM"},  # sin ubicación
    ]
)


def test_build_raw_game_matches_common_contract():
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), SHOTS_DF.to_dict("records"))

    assert raw["game_id"] == "305"
    assert raw["date"] == "2026-01-20"
    assert raw["home_team"]["id"] == "BAS"
    assert raw["home_team"]["name"] == "Kosner Baskonia"
    assert raw["home_score"] == 91
    assert raw["away_score"] == 88
    assert len(raw["players"]) == 2
    assert raw["players"][0]["minutes"] == 31.0  # "31:00" -> 31.0
    assert len(raw["team_stats"]) == 2
    assert len(raw["shots"]) == 2  # el tiro con COORD (-1,-1) se descarta
    assert raw["shots"][0]["made"] is True
    assert raw["shots"][1]["made"] is False


def test_euroleague_transform_and_load_reuses_baskonia_and_is_idempotent(engine):
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), SHOTS_DF.to_dict("records"))

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw)
        assert game.home_team_id == "bas"  # "Kosner Baskonia" normaliza al equipo del seed
        load_game(conn, game)
    with engine.begin() as conn:  # segunda ejecución: no debe duplicar
        game = parse_and_resolve(conn, raw)
        load_game(conn, game)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM games WHERE id='euroleague-305'")).scalar_one() == 1
        pts = conn.execute(
            text("SELECT pts FROM player_game_stats WHERE game_id='euroleague-305' AND player_id='howard'")
        ).scalar_one()
        assert pts == 24
        assert conn.execute(text("SELECT COUNT(*) FROM teams WHERE name='Real Madrid'")).scalar_one() == 1


def test_euroleague_pipeline_run_reports_summary(engine):
    fake_client = mock.Mock()
    fake_client.fetch_season_game_codes.return_value = pd.DataFrame([{"game": 305, "gamecode": "E2025_305", "played": "true"}])
    fake_client.fetch_game_metadata.return_value = METADATA
    fake_client.fetch_game_boxscore.return_value = BOXSCORE_DF
    fake_client.fetch_game_shot_data.return_value = SHOTS_DF
    fake_client.fetch_game_play_by_play.return_value = pd.DataFrame([])

    summary = run(engine, season=2025, client=fake_client)

    assert summary == {"loaded": ["305"], "failed": []}


def test_euroleague_pipeline_run_skips_unplayed_games(engine):
    fake_client = mock.Mock()
    fake_client.fetch_season_game_codes.return_value = pd.DataFrame(
        [{"game": 305, "gamecode": "E2025_305", "played": "true"}, {"game": 306, "gamecode": "E2025_306", "played": "false"}]
    )
    fake_client.fetch_game_metadata.return_value = METADATA
    fake_client.fetch_game_boxscore.return_value = BOXSCORE_DF
    fake_client.fetch_game_shot_data.return_value = SHOTS_DF
    fake_client.fetch_game_play_by_play.return_value = pd.DataFrame([])

    summary = run(engine, season=2025, client=fake_client)

    assert summary == {"loaded": ["305"], "failed": []}
    fake_client.fetch_game_metadata.assert_called_once_with(2025, 305)


PBP_DF = pd.DataFrame(
    [
        {"CODETEAM": "BAS", "PLAYER_ID": "EL-HOWARD", "PLAYTYPE": "2FGM", "PERIOD": 1, "MARKERTIME": "08:00"},
        {"CODETEAM": "MAD", "PLAYER_ID": "EL-RIVAL1", "PLAYTYPE": "3FGM", "PERIOD": 1, "MARKERTIME": "05:00"},
        {"CODETEAM": None, "PLAYER_ID": None, "PLAYTYPE": "BP", "PERIOD": 1, "MARKERTIME": "10:00"},  # begin period
    ]
)
BOXSCORE_WITH_STARTERS_DF = BOXSCORE_DF.copy()


def test_build_raw_game_with_play_by_play_feeds_lineup_reconstruction(engine):
    raw = build_raw_game(
        METADATA, BOXSCORE_WITH_STARTERS_DF.to_dict("records"), SHOTS_DF.to_dict("records"),
        PBP_DF.to_dict("records"),
    )
    assert raw["lineups"] == []  # el adapter no calcula lineups, solo pasa play_by_play
    assert len(raw["play_by_play"]) == 2
    assert raw["play_by_play"][0]["type"] == "score"

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw)

    # Solo 2 jugadores en el boxscore de prueba -> un único quinteto de 2 no
    # llega a 5, así que no se reconstruye ningún lineup completo (dato
    # insuficiente, no se inventa uno).
    assert game.lineups == []
