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
    ghost_candidates,
    is_ambiguous,
    previous_season_id,
    resolve_entity,
    resolve_player_game,
    resolve_team_game,
)


@pytest.fixture()
def engine_with_howards(engine):
    """Añade los otros dos Howard reales y el Fenerbahçe, que el seed no trae.

    Los dos Howard llegan **con boxscore**, igual que en la base de datos
    real. No es decoración del fixture: desde que `resolve_entity` desempata
    por partidos cargados (ver `Candidate.games`), un jugador sin una sola
    línea de boxscore ya no cuenta como empate — así que unos Howard sin
    partidos probarían el camino contrario al que este fichero quiere probar,
    y `test_surname_alone_...` pasaría a verde por el motivo equivocado.
    """
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
        for player_id in ("howard-sant", "william-howa"):
            conn.execute(
                text(
                    "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                    " VALUES ('syn-2-1', :p, 24.0, 11, 3, 2, 52.0)"
                ),
                {"p": player_id},
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


# ---------------------------------------------------------------------------
# Desempate por datos. El caso es literal: el único pulgar abajo del registro
# de feedback es una pregunta de pretemporada contra el Bilbao en la que el
# asistente gastó el turno entero preguntando a cuál de los dos Bilbao se
# refería el entrenador — siendo que uno de los dos no tiene ni un partido.
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_with_ghost_team(engine):
    """Un club duplicado por cambio de patrocinador: mismo nombre, cero partidos.

    Reproduce lo que hay en `data/baskonia.db`: `surne-bilbao` con partidos y
    `bilbao` sin ninguno, los dos como "Bilbao" para quien escribe.
    """
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO teams (id, name, is_own_team) VALUES ('surne-bilbao', 'Surne Bilbao', 0)")
        )
        conn.execute(
            text("INSERT INTO teams (id, name, is_own_team) VALUES ('bilbao', 'Bilbao Basket', 0)")
        )
        conn.execute(
            text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                " game_date, home_score, away_score, pace)"
                " VALUES ('bil-1', 2, 1, 'surne-bilbao', 'rm', '2026-11-08', 88, 80, 71.0)"
            )
        )
    return engine


def test_ghost_duplicate_does_not_create_an_ambiguity(engine_with_ghost_team):
    """El club sin partidos no empata con el club real: no hay nada que preguntar."""
    candidates = resolve_entity(engine_with_ghost_team, "Bilbao", kind="team")

    assert [c.id for c in candidates] == ["surne-bilbao", "bilbao"]
    assert [c.games for c in candidates] == [1, 0]
    # Empatan en parecido de nombre; lo que los separa es tener datos.
    assert candidates[0].score == candidates[1].score
    assert not is_ambiguous(candidates)


def test_the_discarded_ghost_is_reported_not_hidden(engine_with_ghost_team):
    """Apartar una fila en silencio sería el mismo pecado que elegir en silencio."""
    candidates = resolve_entity(engine_with_ghost_team, "Bilbao", kind="team")

    assert [c.id for c in ghost_candidates(candidates)] == ["bilbao"]


def test_all_tied_without_data_is_still_ambiguous(engine):
    """Sin datos de ninguno no hay razón para preferir uno: se sigue preguntando."""
    with engine.begin() as conn:
        for team_id, name in (("bilbao-a", "Bilbao Basket"), ("bilbao-b", "Bilbao Basket")):
            conn.execute(
                text("INSERT INTO teams (id, name, is_own_team) VALUES (:id, :name, 0)"),
                {"id": team_id, "name": name},
            )

    candidates = resolve_entity(engine, "Bilbao Basket", kind="team")

    assert len(candidates) == 2
    assert all(c.games == 0 for c in candidates)
    assert is_ambiguous(candidates)
    assert ghost_candidates(candidates) == []


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
