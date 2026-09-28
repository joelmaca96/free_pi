"""Tests de `ingest/common/shot_context.py`: contexto de tiro derivado del play-by-play.

Los eventos imitan el contrato común de `play_events` (ids externos, reloj
`MM:SS` hacia atrás), con los tipos de tiro que los dos adapters emiten desde
2026-09-28. Las secuencias son las del acta real: el robo y la pérdida llegan
como DOS filas en el mismo segundo, y un 2+1 trae el tiro libre en el mismo
segundo que la canasta.
"""
from ingest.common.shot_context import (
    FASTBREAK_WINDOW_SECONDS,
    derive_shot_context,
    fill_missing_shot_context,
)

TEAMS = ("H", "A")


def _ev(team, event_type, clock, quarter="Q1", player="p1"):
    return {"team_id": team, "player_id": player, "quarter": quarter, "clock": clock, "event_type": event_type}


def _flags(events):
    return [c for c in derive_shot_context(events, TEAMS) if c is not None]


def test_fastbreak_after_defensive_rebound_inside_the_window():
    events = [
        _ev("A", "fg2_missed", "09:50", player="a1"),
        _ev("H", "dreb", "09:48"),
        _ev("H", "fg2_made", "09:44"),  # 4 s después del rebote
    ]
    home_shot = _flags(events)[1]
    assert home_shot == {"is_fastbreak": True, "is_second_chance": False, "is_off_turnover": False}


def test_slow_possession_after_defensive_rebound_is_not_fastbreak():
    events = [
        _ev("H", "dreb", "09:48"),
        _ev("H", "fg2_made", "09:30"),  # 18 s: ataque estático
    ]
    assert _flags(events)[0]["is_fastbreak"] is False
    assert FASTBREAK_WINDOW_SECONDS < 18


def test_steal_and_turnover_in_the_same_second_open_one_off_turnover_possession():
    events = [
        _ev("A", "turnover", "09:30", player="a1"),
        _ev("H", "steal", "09:30"),
        _ev("H", "fg3_missed", "09:20"),
        _ev("H", "oreb", "09:18", player="p2"),
        _ev("H", "fg3_made", "09:15"),
    ]
    first, second = _flags(events)
    # 10 s: ya no es contraataque, pero sí tras pérdida.
    assert first == {"is_fastbreak": False, "is_second_chance": False, "is_off_turnover": True}
    # Tras el rebote ofensivo: segunda oportunidad Y sigue siendo la posesión de la pérdida.
    assert second == {"is_fastbreak": False, "is_second_chance": True, "is_off_turnover": True}


def test_turnover_without_a_steal_also_counts_as_off_turnover():
    events = [_ev("A", "turnover", "05:00", player="a1"), _ev("H", "fg2_made", "04:58")]
    assert _flags(events)[0] == {"is_fastbreak": True, "is_second_chance": False, "is_off_turnover": True}


def test_possession_after_opponent_score_has_no_context():
    events = [
        _ev("A", "fg2_made", "08:00", player="a1"),
        _ev("H", "fg2_made", "07:57"),  # 3 s, pero tras canasta rival: no es contraataque
    ]
    assert _flags(events)[1] == {"is_fastbreak": False, "is_second_chance": False, "is_off_turnover": False}


def test_and_one_free_throw_gives_the_possession_back_to_the_shooter():
    """2+1 fallado y cogido en ataque: el palmeo sigue en la posesión de la pérdida original."""
    events = [
        _ev("A", "turnover", "06:00", player="a1"),
        _ev("H", "fg2_made", "05:55"),
        _ev("H", "ft_missed", "05:55"),
        _ev("H", "oreb", "05:54", player="p2"),
        _ev("H", "fg2_made", "05:52", player="p2"),
    ]
    putback = _flags(events)[1]
    assert putback["is_second_chance"] is True
    assert putback["is_off_turnover"] is True


def test_made_free_throws_hand_the_ball_to_the_rival():
    events = [
        _ev("H", "dreb", "04:00"),
        _ev("H", "ft_made", "03:55"),
        _ev("H", "ft_made", "03:55"),
        _ev("A", "fg2_made", "03:50", player="a1"),
    ]
    assert _flags(events)[0] == {"is_fastbreak": False, "is_second_chance": False, "is_off_turnover": False}


def test_offensive_rebound_of_a_team_without_the_ball_opens_an_unknown_possession():
    events = [
        _ev("H", "dreb", "02:00"),
        _ev("A", "oreb", "01:50", player="a1"),  # el acta se saltó algo (rebote de equipo...)
        _ev("A", "fg2_made", "01:48", player="a1"),
    ]
    assert _flags(events)[0] == {"is_fastbreak": False, "is_second_chance": True, "is_off_turnover": False}


def test_a_new_period_resets_the_possession():
    events = [
        _ev("A", "turnover", "00:05", quarter="Q1", player="a1"),
        _ev("H", "fg2_made", "09:58", quarter="Q2"),
    ]
    assert _flags(events)[0] == {"is_fastbreak": False, "is_second_chance": False, "is_off_turnover": False}


def test_events_are_read_in_clock_order_not_list_order():
    events = [
        _ev("H", "fg2_made", "09:44"),
        _ev("H", "dreb", "09:48"),  # llega detrás en la lista, pero antes en el reloj
    ]
    assert derive_shot_context(events, TEAMS)[0]["is_fastbreak"] is True


def test_fill_only_touches_missing_flags_and_keeps_source_ones():
    events = [_ev("A", "turnover", "05:00", player="a1"), _ev("H", "fg2_made", "04:58")]
    shots = [
        {"player_id": "p1", "team_id": "H", "made": True, "quarter": "Q1", "clock": "04:58",
         "is_fastbreak": False, "is_second_chance": None},  # la fuente dice "no contraataque"
    ]
    filled = fill_missing_shot_context(shots, events, TEAMS)[0]
    assert filled["is_fastbreak"] is False          # la bandera de la fuente gana
    assert filled["is_second_chance"] is False      # rellenada
    assert filled["is_off_turnover"] is True        # rellenada (clave ausente)
    assert shots[0]["is_second_chance"] is None     # no muta la entrada


def test_fill_matches_identical_shots_one_to_one_in_order():
    """Tapón + palmeo fallado del mismo jugador en el mismo segundo: dos tiros, dos eventos."""
    events = [
        _ev("H", "dreb", "03:00"),
        _ev("H", "fg2_missed", "02:57"),
        _ev("H", "oreb", "02:57"),
        _ev("H", "fg2_missed", "02:57"),
    ]
    shot = {"player_id": "p1", "team_id": "H", "made": False, "quarter": "Q1", "clock": "02:57"}
    first, second = fill_missing_shot_context([shot, dict(shot)], events, TEAMS)
    assert first["is_second_chance"] is False
    assert second["is_second_chance"] is True


def test_shot_without_clock_or_without_event_is_left_alone():
    events = [_ev("H", "fg2_made", "04:58")]
    no_clock = {"player_id": "p1", "team_id": "H", "made": True}
    other_player = {"player_id": "zz", "team_id": "H", "made": True, "quarter": "Q1", "clock": "04:58"}
    filled = fill_missing_shot_context([no_clock, other_player], events, TEAMS)
    assert all(shot.get("is_fastbreak") is None for shot in filled)
