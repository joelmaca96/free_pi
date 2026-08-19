"""Tests para `db/storage.py`.

Cubre los upserts idempotentes de todas las entidades: equipos, jugadores,
partidos, box scores y estadísticas avanzadas por equipo/partido. Verifica
que repetir la misma operación no duplica filas y que los campos se
actualizan correctamente.
"""
import pytest

from packages.baskonia_core.db import models
from packages.baskonia_core.db.storage import (
    upsert_boxscore,
    upsert_game,
    upsert_player,
    upsert_team,
    upsert_team_game_stats,
)


# --- upsert_team -----------------------------------------------------------

def test_upsert_team_creates(session):
    """Un equipo nuevo se inserta."""
    team = upsert_team(session, "vitoria", "Baskonia", "acb")
    assert team.id is not None
    assert session.query(models.Team).count() == 1


def test_upsert_team_idempotent(session):
    """Repetir el upsert del mismo slug no duplica la fila."""
    upsert_team(session, "vitoria", "Baskonia", "acb")
    upsert_team(session, "vitoria", "Baskonia", "acb")
    assert session.query(models.Team).count() == 1


def test_upsert_team_updates_name(session):
    """El nombre se actualiza si cambia."""
    team = upsert_team(session, "vitoria", "Baskonia", "acb")
    team2 = upsert_team(session, "vitoria", "Baskonia SAD", "acb")
    assert team.id == team2.id
    assert team2.name == "Baskonia SAD"


# --- upsert_player ---------------------------------------------------------

def test_upsert_player_creates(session, teams):
    """Un jugador nuevo se inserta."""
    player = upsert_player(session, "Markus Howard", teams["vitoria"], position="G", number="0")
    assert player.id is not None
    assert session.query(models.Player).count() == 1


def test_upsert_player_idempotent(session, teams):
    """Repetir el upsert del mismo jugador no duplica la fila."""
    upsert_player(session, "Markus Howard", teams["vitoria"])
    upsert_player(session, "Markus Howard", teams["vitoria"])
    assert session.query(models.Player).count() == 1


def test_upsert_player_scoped_by_team(session, teams):
    """El mismo nombre en equipos distintos son jugadores distintos."""
    upsert_player(session, "Markus Howard", teams["vitoria"])
    upsert_player(session, "Markus Howard", teams["bilbao"])
    assert session.query(models.Player).count() == 2


# --- upsert_game -----------------------------------------------------------

def test_upsert_game_creates(session, teams):
    """Un partido nuevo se inserta."""
    game = upsert_game(
        session,
        date="Sun, Nov 23, 2025",
        league="acb",
        home_team=teams["vitoria"],
        away_team=teams["bilbao"],
        home_score=88,
        away_score=80,
    )
    assert game.id is not None
    assert session.query(models.Game).count() == 1


def test_upsert_game_idempotent(session, teams):
    """Repetir el upsert del mismo partido no duplica la fila."""
    upsert_game(session, "Sun, Nov 23, 2025", "acb", teams["vitoria"], teams["bilbao"])
    upsert_game(session, "Sun, Nov 23, 2025", "acb", teams["vitoria"], teams["bilbao"])
    assert session.query(models.Game).count() == 1


def test_upsert_game_updates_league(session, teams):
    """La liga se actualiza en partidos ya existentes (fix de competición real)."""
    game = upsert_game(session, "Sun, Nov 23, 2025", "acb", teams["vitoria"], teams["bilbao"])
    game2 = upsert_game(session, "Sun, Nov 23, 2025", "euroleague", teams["vitoria"], teams["bilbao"])
    assert game.id == game2.id
    assert game2.league == "euroleague"


def test_upsert_game_updates_score(session, teams):
    """El resultado se actualiza cuando el partido se juega."""
    game = upsert_game(session, "Sun, Nov 23, 2025", "acb", teams["vitoria"], teams["bilbao"])
    assert game.home_score is None
    game2 = upsert_game(
        session, "Sun, Nov 23, 2025", "acb", teams["vitoria"], teams["bilbao"], home_score=88, away_score=80
    )
    assert game2.home_score == 88
    assert game2.away_score == 80


# --- upsert_boxscore -------------------------------------------------------

def test_upsert_boxscore_creates_and_calculates_efg_ts(session, teams, played_game):
    """El box score se inserta y calcula eFG%/TS% automáticamente."""
    box = upsert_boxscore(
        session,
        played_game,
        teams["vitoria"],
        "Markus Howard",
        {"MP": "30:00", "PTS": 22, "FG": 8, "FGA": 15, "3P": 4, "3PA": 9, "FT": 2, "FTA": 2},
    )
    assert box.id is not None
    # eFG% = (8 + 0.5*4) / 15
    assert box.efg_pct == pytest.approx((8 + 0.5 * 4) / 15)
    # TS% = 22 / (2 * (15 + 0.44*2))
    assert box.ts_pct == pytest.approx(22 / (2 * (15 + 0.44 * 2)))


def test_upsert_boxscore_idempotent(session, teams, played_game):
    """Repetir el upsert del mismo jugador no duplica la fila.

    El fixture `played_game` ya crea un box score de "Markus Howard" para el
    vitoria, así que el upsert lo actualiza en vez de crear una fila nueva:
    el conteo total se mantiene en 2 (las 2 filas del fixture).
    """
    stats = {"MP": "30:00", "PTS": 22, "FG": 8, "FGA": 15}
    upsert_boxscore(session, played_game, teams["vitoria"], "Markus Howard", stats)
    upsert_boxscore(session, played_game, teams["vitoria"], "Markus Howard", stats)
    assert session.query(models.BoxScore).count() == 2  # 2 del fixture, sin duplicados


def test_upsert_boxscore_updates_stats(session, teams, played_game):
    """Las estadísticas se actualizan si cambian."""
    stats = {"MP": "30:00", "PTS": 22, "FG": 8, "FGA": 15}
    box = upsert_boxscore(session, played_game, teams["vitoria"], "Nuevo Jugador", stats)
    box2 = upsert_boxscore(
        session, played_game, teams["vitoria"], "Nuevo Jugador", {"MP": "30:00", "PTS": 30, "FG": 10, "FGA": 18}
    )
    assert box.id == box2.id
    assert box2.points == 30


# --- upsert_team_game_stats ------------------------------------------------

def test_upsert_team_game_stats_creates(session, teams, played_game):
    """Las stats avanzadas se insertan."""
    ratings = {"possessions": 70.0, "pace": 69.5, "off_rating": 125.7, "def_rating": 115.1, "net_rating": 10.6}
    row = upsert_team_game_stats(session, played_game, teams["vitoria"], ratings)
    assert row.id is not None
    assert row.off_rating == pytest.approx(125.7)


def test_upsert_team_game_stats_idempotent(session, teams, played_game):
    """Repetir el upsert del mismo equipo/partido no duplica la fila.

    El fixture `played_game` ya crea stats avanzadas para ambos equipos, así
    que el upsert del vitoria actualiza la fila existente en vez de crear una
    nueva: el conteo total se mantiene en 2.
    """
    ratings = {"possessions": 70.0, "pace": 69.5, "off_rating": 125.7, "def_rating": 115.1, "net_rating": 10.6}
    upsert_team_game_stats(session, played_game, teams["vitoria"], ratings)
    upsert_team_game_stats(session, played_game, teams["vitoria"], ratings)
    assert session.query(models.TeamGameStats).count() == 2  # 2 del fixture, sin duplicados
