"""Tests de reconstrucción de quintetos a partir de play-by-play."""
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

    assert len(result[HOME]) == 1
    lineup = result[HOME][0]
    assert lineup.player_ids == sorted(HOME_STARTERS)
    assert lineup.minutes == 40.0  # 2400s = 40 min, un solo quinteto todo el partido
    assert lineup.plus_minus == 2

    assert len(result[AWAY]) == 1
    assert result[AWAY][0].plus_minus == -2


def test_reconstruct_lineups_substitution_splits_stints_and_attributes_plus_minus():
    events = [
        PlayByPlayEvent(team_id=HOME, type="score", seconds=60, points=3),  # quinteto titular: +3
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=300, player_id="h5"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=300, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="score", seconds=400, points=2),  # segundo quinteto: +2
        PlayByPlayEvent(team_id=AWAY, type="score", seconds=450, points=2),  # segundo quinteto: -2
    ]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=2400)

    by_players = {tuple(l.player_ids): l for l in result[HOME]}
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

    assert result[HOME] == []
    assert len(result[AWAY]) == 1  # el rival sí tiene quinteto completo


def test_reconstruct_lineups_reuses_same_combo_across_non_contiguous_stints():
    events = [
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=100, player_id="h5"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=100, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="sub_out", seconds=200, player_id="h6"),
        PlayByPlayEvent(team_id=HOME, type="sub_in", seconds=200, player_id="h5"),  # vuelve el quinteto titular
    ]
    result = reconstruct_lineups(HOME, AWAY, HOME_STARTERS, AWAY_STARTERS, events, game_end_seconds=300)

    starters_lineup = next(l for l in result[HOME] if l.player_ids == sorted(HOME_STARTERS))
    # 0-100s + 200-300s = 200s en total para el quinteto titular, en dos tramos distintos.
    assert starters_lineup.minutes == round(200 / 60, 1)
