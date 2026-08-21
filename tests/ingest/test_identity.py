"""Tests de resolución de identidad (equipos/jugadores entre fuentes)."""
from sqlalchemy import text

from ingest.common.identity import (
    get_or_create_season,
    normalize_name,
    resolve_or_create_player,
    resolve_or_create_team,
)


def test_normalize_name_strips_accents_and_club_noise():
    assert normalize_name("Valencia Basket Club") == "valencia"
    assert normalize_name("Valencia") == "valencia"
    assert normalize_name("Baskonia") == normalize_name("Saski Baskonia")


def test_resolve_or_create_team_reuses_seed_team_by_name(engine):
    with engine.begin() as conn:
        team_id = resolve_or_create_team(conn, "acb", "ACB-VAL", "Valencia Basket")
    assert team_id == "val"  # ya existe en el seed con ese nombre


def test_resolve_or_create_team_is_idempotent_and_normalizes_across_sources(engine):
    with engine.begin() as conn:
        acb_id = resolve_or_create_team(conn, "acb", "ACB-VAL", "Valencia Basket")
        euro_id = resolve_or_create_team(conn, "euroleague", "EL-VAL", "Valencia")
        acb_id_again = resolve_or_create_team(conn, "acb", "ACB-VAL", "Valencia Basket")

    assert acb_id == euro_id == acb_id_again == "val"


def test_resolve_or_create_team_creates_new_team_when_no_match(engine):
    with engine.begin() as conn:
        team_id = resolve_or_create_team(conn, "acb", "ACB-XYZ", "Club Baloncesto Nuevo")
        rows = conn.execute(text("SELECT name FROM teams WHERE id = :id"), {"id": team_id}).first()
    assert rows is not None
    assert rows[0] == "Club Baloncesto Nuevo"


def test_resolve_or_create_player_reuses_seed_player_by_external_id(engine):
    with engine.begin() as conn:
        player_id = resolve_or_create_player(conn, "acb", "ACB-HOWARD", "Marcus Howard", "bas", number=0)
        player_id_again = resolve_or_create_player(conn, "acb", "ACB-HOWARD", "Marcus Howard", "bas", number=0)
    assert player_id == "howard"
    assert player_id_again == "howard"


def test_resolve_or_create_player_matches_by_team_and_number_without_external_id(engine):
    with engine.begin() as conn:
        player_id = resolve_or_create_player(conn, "euroleague", "EL-9001", "M. Howard", "bas", number=0)
    assert player_id == "howard"


def test_resolve_or_create_player_creates_new_player_when_no_match(engine):
    with engine.begin() as conn:
        player_id = resolve_or_create_player(
            conn, "acb", "ACB-NEW1", "Jugador Nuevo", "bas", number=99, position="Base"
        )
        row = conn.execute(text("SELECT name, number FROM players WHERE id = :id"), {"id": player_id}).first()
    assert row is not None
    assert row[0] == "Jugador Nuevo"
    assert row[1] == 99


def test_get_or_create_season_is_idempotent(engine):
    with engine.begin() as conn:
        first = get_or_create_season(conn, 2025)
        second = get_or_create_season(conn, 2025)
        new = get_or_create_season(conn, 2026)
    assert first == second == 1  # temporada 2025-2026 ya está en el seed
    assert new != first
