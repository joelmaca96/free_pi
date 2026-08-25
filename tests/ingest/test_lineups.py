"""Tests de reconstrucción de quintetos y tramos a partir de play-by-play.

`reconstruct_lineups` devuelve desde 2026-08-24 un `LineupReconstruction`
(`by_team` + `stints`) en vez de solo el diccionario por equipo: los tramos
sin agregar son lo que hace contestable "el mejor quinteto en los últimos
minutos" (ver `local/features/005-chatbot/01_design.md` §2.3).
"""
import pytest

from ingest.common.game_clock import game_clock_to_seconds
from ingest.common.lineups import PlayByPlayEvent, reconstruct_lineups


def test_game_clock_to_seconds_within_and_across_quarters():
    assert game_clock_to_seconds("Q1", "10:00") == 0
    assert game_clock_to_seconds("Q1", "08:24") == 96  # 10:00 - 8:24 = 1:36 = 96s
    assert game_clock_to_seconds("Q2", "10:00") == 600
    assert game_clock_to_seconds("Q4", "00:00") == 4 * 600
    assert game_clock_to_seconds("OT1", "05:00") == 4 * 600
    assert game_clock_to_seconds("OT1", "00:00") == 4 * 600 + 300


def test_game_clock_rejects_unknown_period_or_clock():
    with pytest.raises(ValueError):
        game_clock_to_seconds("Q5", "10:00")
    with pytest.raises(ValueError):
        game_clock_to_seconds("Q1", "not-a-clock")


HOME = "bas"
AWAY = "rm"
HOME_STARTERS = ["h1", "h2", "h3", "h4", "h5"]
AWAY_STARTERS = ["a1", "a2", "a3", "a4", "a5"]


def test_reconstruct_lineups_single_stint_full_game():
    events = [PlayByPlayEvent(team_id=HOME, type="score", seconds=100, points=2)]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=2400)

    assert len(result.by_team[HOME]) == 1
    lineup = result.by_team[HOME][0]
    assert lineup.player_ids == sorted(HOME_STARTERS)
    assert lineup.minutes == 40.0  # 2400s = 40 min, un solo quinteto todo el partido
    assert lineup.plus_minus == 2

    assert len(result.by_team[AWAY]) == 1
    assert result.by_team[AWAY][0].plus_minus == -2


def test_reconstruct_lineups_substitution_splits_stints_and_attributes_plus_minus():
    events = [
        PlayByPlayEvent(team_id=HOME, type="score", seconds=60, points=3),  # quinteto titular: +3
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=300, player_id="h5"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=300, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="score", seconds=400, points=2),  # segundo quinteto: +2
        PlayByPlayEvent(team_id=AWAY, type="score", seconds=450, points=2),  # segundo quinteto: -2
    ]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=2400)

    by_players = {tuple(l.player_ids): l for l in result.by_team[HOME]}
    starters_key = tuple(sorted(HOME_STARTERS))
    second_key = tuple(sorted(["h1", "h2", "h3", "h4", "h6"]))

    assert by_players[starters_key].plus_minus == 3
    assert by_players[starters_key].minutes == round(300 / 60, 1)  # 0..300s en pista

    assert by_players[second_key].plus_minus == 0  # +2 propio -2 rival
    assert by_players[second_key].minutes == round((2400 - 300) / 60, 1)


def test_reconstruct_lineups_ignores_incomplete_lineups():
    # Falta un jugador titular local (solo 4): no debe generar un "quinteto" de 4.
    events = [PlayByPlayEvent(team_id=HOME, type="score", seconds=10, points=2)]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS[:4], AWAY_STARTERS, events, game_end_seconds=100)

    assert result.by_team[HOME] == []
    assert len(result.by_team[AWAY]) == 1  # el rival sí tiene quinteto completo


def test_reconstruct_lineups_reuses_same_combo_across_non_contiguous_stints():
    events = [
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=100, player_id="h5"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=100, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=200, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=200, player_id="h5"),  # vuelve el quinteto titular
    ]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=300)

    starters_lineup = next(l for l in result.by_team[HOME] if l.player_ids == sorted(HOME_STARTERS))
    # 0-100s + 200-300s = 200s en total para el quinteto titular, en dos tramos distintos.
    assert starters_lineup.minutes == round(200 / 60, 1)


# ---------------------------------------------------------------- tramos --
# Lo que hace contestable "el mejor quinteto en los últimos minutos": el mismo
# recorrido de eventos, sin agregar. Ver `ingest/common/lineups.py`.


def test_stints_keep_the_clock_and_the_score_of_each_spell():
    events = [
        PlayByPlayEvent(team_id=HOME, type="score", seconds=60, points=3),
        PlayByPlayEvent(team_id=AWAY, type="score", seconds=120, points=2),
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=300, player_id="h5"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=300, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="score", seconds=400, points=2),
    ]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=2400)

    home_stints = [s for s in result.stints if s.team_id == HOME]
    assert len(home_stints) == 2

    first, second = sorted(home_stints, key=lambda s: s.start_seconds)
    assert (first.start_seconds, first.end_seconds) == (0.0, 300.0)
    assert (first.points_for, first.points_against) == (3, 2)
    assert first.margin_start == 0  # el partido empieza 0-0

    assert (second.start_seconds, second.end_seconds) == (300.0, 2400.0)
    assert second.points_for == 2
    # Entró con 3-2 a favor: es lo que separa "los últimos minutos" de
    # "los últimos minutos de un partido ya decidido".
    assert second.margin_start == 1


def test_stints_and_aggregated_lineups_add_up_to_the_same_minutes():
    """Invariante: los tramos de un equipo suman los minutos de sus quintetos.

    Si esto se rompe, es que la agregación y el detalle han divergido — y
    entonces el "global de la temporada" y el "tramo final" dejan de ser dos
    lecturas del mismo partido.
    """
    events = [
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=500, player_id="h5"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=500, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=1200, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=1200, player_id="h5"),
    ]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=2400)

    stint_seconds = sum(s.end_seconds - s.start_seconds for s in result.stints if s.team_id == HOME)
    lineup_minutes = sum(l.minutes for l in result.by_team[HOME])
    assert stint_seconds == 2400
    assert round(stint_seconds / 60.0, 1) == round(lineup_minutes, 1)


def test_incomplete_lineups_produce_no_stint_either():
    events = [PlayByPlayEvent(team_id=HOME, type="score", seconds=10, points=2)]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS[:4], AWAY_STARTERS, events, game_end_seconds=100)

    assert [s.team_id for s in result.stints] == [AWAY]


def test_lineups_now_carry_their_own_team():
    """Antes había que inferirlo por el equipo ACTUAL de los cinco jugadores,
    que un traspaso estropea hacia atrás (ver `lineups.team_id`)."""
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, [], game_end_seconds=2400)
    assert {l.team_id for l in result.by_team[HOME]} == {HOME}
    assert {l.team_id for l in result.by_team[AWAY]} == {AWAY}
