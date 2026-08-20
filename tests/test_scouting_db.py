"""Tests de la base de datos de scouting: carga del esquema y repositorio.

Cada test usa una BD SQLite en memoria propia, inicializada ejecutando
`schema.sql` (DDL + datos semilla reales), para no depender de ni tocar
`data/baskonia.db`.
"""
from datetime import date

import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import (
    ScoutingRepository,
    create_scouting_engine,
    init_scouting_db,
    is_initialized,
)


@pytest.fixture()
def engine():
    """Engine SQLite en memoria con el esquema de scouting ya cargado."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def repo(engine):
    return ScoutingRepository(engine)


# ---------- Carga de esquema ----------


def test_schema_loads_without_errors(engine):
    assert is_initialized(engine)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        views = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='view'")
            )
        }
    expected_tables = {
        "seasons", "competitions", "teams", "court_zones", "players",
        "player_external_ids", "games", "game_advanced_stats",
        "player_game_stats", "lineups", "lineup_players", "game_zone_stats",
        "shots", "key_events", "score_progression", "upcoming_matchups",
    }
    expected_views = {
        "player_stats_by_competition", "player_stats_combined",
        "team_stats_by_competition", "team_stats_combined",
    }
    assert expected_tables <= tables
    assert expected_views <= views


def test_init_is_noop_without_force(engine):
    """Llamar dos veces sin --force no falla (no reinserta el seed)."""
    init_scouting_db(engine)  # ya inicializado por el fixture; no debe lanzar
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM players")).scalar_one()
    assert count == 8


def test_init_force_recreates_schema(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM key_events WHERE game_id = 'g1'"))
    init_scouting_db(engine, force=True)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM key_events")).scalar_one()
    assert count == 20  # el seed se recarga entero


# ---------- Plantilla con medias de temporada ----------


def test_get_roster_returns_all_players_with_season_stats(repo):
    roster = repo.get_roster("2025-2026")
    assert len(roster) == 8
    numbers = [p["number"] for p in roster]
    assert numbers == sorted(numbers)  # ordenado por dorsal

    howard = next(p for p in roster if p["id"] == "howard")
    assert howard["name"] == "Marcus Howard"
    assert howard["gp"] == 5
    # Medias calculadas por la vista player_stats_combined sobre el seed de g1..g5.
    assert howard["pts_avg"] == pytest.approx((20 + 17 + 12 + 17 + 19) / 5)
    assert howard["min_avg"] == pytest.approx((32.4 + 30.6 + 25.8 + 30.0 + 31.2) / 5)


def test_get_roster_unknown_season_returns_empty(repo):
    assert repo.get_roster("2099-2100") == []


# ---------- Detalle de un partido ----------


def test_get_game_detail_g1_matches_seed(repo):
    detail = repo.get_game_detail("g1")
    assert detail is not None
    assert detail["home_team_id"] == "bas"
    assert detail["away_team_id"] == "rm"
    assert detail["away_team_name"] == "Real Madrid"
    assert detail["competition_name"] == "Euroliga"
    assert detail["home_score"] == 88
    assert detail["away_score"] == 82

    # Bloque de conveniencia con la perspectiva del Baskonia.
    assert detail["baskonia"]["is_home"] is True
    assert detail["baskonia"]["opponent_name"] == "Real Madrid"
    assert detail["baskonia"]["score_for"] == 88
    assert detail["baskonia"]["score_against"] == 82

    assert len(detail["advanced"]) == 1
    assert detail["advanced"][0]["team_id"] == "bas"
    assert detail["advanced"][0]["efg_pct"] == pytest.approx(53.8)
    assert detail["advanced"][0]["net_rating"] == pytest.approx(7.8)

    assert len(detail["lineups"]) == 3
    top_lineup = detail["lineups"][0]
    assert top_lineup["minutes"] == pytest.approx(15.1)
    assert {p["id"] for p in top_lineup["players"]} == {
        "howard", "moneke", "codi", "sedekerskis", "kotsar",
    }

    assert len(detail["zone_stats"]) == 6
    pintura = next(z for z in detail["zone_stats"] if z["label"] == "Pintura")
    assert pintura["team_id"] == "bas"
    assert pintura["fg_pct"] == pytest.approx(58)

    assert len(detail["key_events"]) == 4
    assert detail["key_events"][0]["label"] == "Buen arranque defensivo"
    assert detail["key_events"][0]["team_id"] == "bas"
    rival_event = next(e for e in detail["key_events"] if e["team_id"] == "rm")
    assert rival_event["label"] == "Triple de Campazzo"


def test_get_game_detail_unknown_game_returns_none(repo):
    assert repo.get_game_detail("does-not-exist") is None


def test_get_game_detail_baskonia_is_none_for_game_without_baskonia(repo, engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO games"
                " (id, season_id, competition_id, home_team_id, away_team_id,"
                "  game_date, home_score, away_score, pace)"
                " VALUES ('g-rival', 1, 2, 'rm', 'fcb', '2026-02-01', 90, 88, 72.0)"
            )
        )

    detail = repo.get_game_detail("g-rival")
    assert detail["home_team_id"] == "rm"
    assert detail["away_team_id"] == "fcb"
    assert detail["baskonia"] is None
    assert detail["advanced"] == []


# ---------- Boxscore de un partido ----------


def test_get_game_boxscore_matches_seed(repo):
    boxscore = repo.get_game_boxscore("g1")
    assert len(boxscore) == 8
    # Orden por puntos desc (empates por nombre, ver ORDER BY en el repositorio).
    assert [row["player_id"] for row in boxscore][:2] == ["howard", "moneke"]
    assert boxscore[0]["name"] == "Marcus Howard"
    assert boxscore[0]["pts"] == 20
    assert boxscore[-1]["player_id"] == "lutse"
    assert boxscore[-1]["pts"] == 0


def test_get_game_boxscore_unknown_game_returns_empty(repo):
    assert repo.get_game_boxscore("does-not-exist") == []


# ---------- Forma reciente de un jugador ----------


def test_get_player_recent_form_orders_by_date_desc_and_limits(repo):
    form = repo.get_player_recent_form("howard", last_n=2)
    assert [row["game_id"] for row in form] == ["g5", "g4"]  # 2026-01-18 y 2026-01-15
    assert form[0]["pts"] == 19
    assert form[1]["pts"] == 17


# ---------- Carga de minutos en una ventana de días ----------


def test_get_player_minutes_load_sums_within_window(repo):
    # kotsar en el seed: g1 2025-12-29 (fuera de la ventana de 14 días desde
    # 2026-01-18), g2 2026-01-05, g3 2026-01-10, g4 2026-01-15, g5 2026-01-18.
    total = repo.get_player_minutes_load("kotsar", days=14, as_of=date(2026, 1, 18))
    assert total == pytest.approx(25.9 + 25.9 + 21.1 + 23.3)


def test_get_player_minutes_load_zero_before_any_game(repo):
    total = repo.get_player_minutes_load("kotsar", days=14, as_of=date(2025, 12, 1))
    assert total == 0.0


# ---------- Tendencia ORtg/DRtg ----------


def test_get_rating_trend_orders_by_date_desc_and_matches_seed(repo):
    trend = repo.get_rating_trend(last_n=8)
    assert [row["game_id"] for row in trend] == ["g5", "g4", "g3", "g2", "g1"]
    # El seed no registra ortg/drtg por partido (ver comentario en schema.sql).
    assert all(row["ortg"] is None and row["drtg"] is None for row in trend)


def test_get_rating_trend_respects_limit(repo):
    trend = repo.get_rating_trend(last_n=2)
    assert [row["game_id"] for row in trend] == ["g5", "g4"]


# ---------- Calendario de un equipo cualquiera (propio o rival) ----------


def test_get_games_for_team_returns_all_baskonia_games(repo):
    games = repo.get_games_for_team("bas")
    assert [g["game_id"] for g in games] == ["g1", "g2", "g3", "g4", "g5"]
    g2 = next(g for g in games if g["game_id"] == "g2")
    assert g2["home_team_id"] == "uni"
    assert g2["away_team_id"] == "bas"
    assert g2["home_score"] == 85
    assert g2["away_score"] == 91


def test_get_games_for_team_filters_by_opponent_and_season(repo):
    games = repo.get_games_for_team("rm", season_label="2025-2026")
    assert [g["game_id"] for g in games] == ["g1"]


def test_get_games_for_team_unknown_team_returns_empty(repo):
    assert repo.get_games_for_team("does-not-exist") == []


# ---------- Próximos rivales ----------


def test_get_upcoming_matchups_matches_seed(repo):
    matchups = repo.get_upcoming_matchups()
    assert len(matchups) == 5
    assert [m["opponent_name"] for m in matchups] == [
        "Real Madrid", "FC Barcelona", "Bayern Múnich", "BAXI Manresa", "Joventut Badalona",
    ]

    real_madrid = matchups[0]
    assert real_madrid["competition_name"] == "Euroliga"
    assert real_madrid["h2h_wins"] == 2
    assert real_madrid["h2h_losses"] == 3
    assert real_madrid["key_player_note"] == "Facundo Campazzo — en racha (TS% +2.1z)"

    baxi = next(m for m in matchups if m["opponent_name"] == "BAXI Manresa")
    assert baxi["has_scouting_data"] == 0
    assert baxi["predicted_net_rating"] is None
