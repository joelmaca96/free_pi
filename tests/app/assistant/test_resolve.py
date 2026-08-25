"""Tests de `app/assistant/resolve.py` con casos de la vida real (§12.2).

Los casos no son inventados: salen del propio encargo ("Fenerbache", con la
errata incluida), de la confusión Markus/Marcus Howard, y del hecho de que en
la base de datos real conviven varios Howard distintos. El que más importa es
el último: **con empate real no se elige**. Devolver el primero en silencio
produce una respuesta segura sobre la persona equivocada, que es el fallo más
caro de todo el sistema.
"""
import datetime as dt

import pytest
from sqlalchemy import text

from app.assistant.resolve import (
    is_ambiguous,
    previous_season_id,
    resolve_entity,
    resolve_player_game,
    resolve_team_game,
)


@pytest.fixture()
def engine_with_howards(engine):
    """Añade los otros dos Howard reales y el Fenerbahçe, que el seed no trae."""
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO teams (id, name, is_own_team) VALUES ('fenerbahce-b', 'Fenerbahce Beko Istanbul', 0)")
        )
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('howard-sant', 'fcb', 'Howard Sant-Roos', 7, 'Alero')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('william-howa', 'rm', 'William Howard', 3, 'Alero')"
            )
        )
    return engine


def test_exact_name_resolves_to_one_player(engine):
    candidates = resolve_entity(engine, "Marcus Howard")
    assert [c.id for c in candidates] == ["howard"]
    assert candidates[0].matched_by == "nombre"


def test_misspelled_first_name_still_resolves(engine):
    """'Markus' por 'Marcus': el error de tecleo más probable con este jugador."""
    candidates = resolve_entity(engine, "Markus Howard")
    assert candidates[0].id == "howard"
    assert candidates[0].matched_by == "difuso"


def test_misspelled_team_resolves_fuzzily(engine_with_howards):
    """'Fenerbache' es la errata del propio encargo."""
    candidates = resolve_entity(engine_with_howards, "Fenerbache", kind="team")
    assert candidates[0].id == "fenerbahce-b"


def test_team_alias_resolves(engine_with_howards):
    assert resolve_entity(engine_with_howards, "el Fener", kind="team")[0].id == "fenerbahce-b"
    assert resolve_entity(engine_with_howards, "el Barça", kind="team")[0].id == "fcb"


def test_own_team_alias(engine):
    candidates = resolve_entity(engine, "nosotros")
    assert candidates[0].id == "bas"
    assert candidates[0].matched_by == "equipo propio"


def test_surname_alone_returns_every_howard_and_does_not_choose(engine_with_howards):
    """El caso que §5.1 prohíbe resolver en silencio."""
    candidates = resolve_entity(engine_with_howards, "Howard", kind="player")

    assert {c.id for c in candidates} == {"howard", "howard-sant", "william-howa"}
    assert is_ambiguous(candidates)
    # Y todos empatados: ninguno destaca lo suficiente como para elegirlo.
    assert len({c.score for c in candidates}) == 1


def test_unknown_name_returns_nothing_rather_than_the_least_bad_match(engine):
    assert resolve_entity(engine, "Michael Jordan") == []


def test_external_id_resolves(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO player_external_ids (player_id, source, external_id)"
                " VALUES ('kotsar', 'acb', 'ACB-12345')"
            )
        )
    candidates = resolve_entity(engine, "acb-12345", kind="player")
    assert candidates[0].id == "kotsar"
    assert candidates[0].matched_by == "id externo"


def test_previous_season(engine):
    from tests.app.assistant.conftest import LEAGUE_SEASON_ID

    assert previous_season_id(engine, LEAGUE_SEASON_ID) == 1
    assert previous_season_id(engine, 1) is None


def test_player_last_game_is_the_last_one_HE_played(engine):
    """No el último del equipo: un jugador no siempre juega (§5.2)."""
    with engine.begin() as conn:
        # Un partido posterior a todos los del seed en el que Kotsar no juega.
        conn.execute(
            text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                " game_date, home_score, away_score, pace)"
                " VALUES ('g6', 1, 1, 'bas', 'rm', '2026-02-01', 80, 70, 70.0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                " VALUES ('g6', 'howard', 25.0, 15, 2, 3, 50.0)"
            )
        )

    assert resolve_player_game(engine, "howard", season_id=1) == "g6"
    assert resolve_player_game(engine, "kotsar", season_id=1) == "g5"


def test_team_game_by_opponent_and_date(engine):
    today = dt.date(2026, 3, 1)
    assert resolve_team_game(engine, "bas", when="last", today=today) == "g5"
    assert resolve_team_game(engine, "bas", when="first", today=today) == "g1"
    assert resolve_team_game(engine, "bas", opponent_id="fcb", today=today) == "g3"
    assert resolve_team_game(engine, "bas", when="2026-01-05", today=today) == "g2"


def test_team_game_ignores_games_after_today(engine):
    """`today` se inyecta: nada de `date.today()` escondido dentro de la consulta."""
    assert resolve_team_game(engine, "bas", when="last", today=dt.date(2026, 1, 6)) == "g2"
