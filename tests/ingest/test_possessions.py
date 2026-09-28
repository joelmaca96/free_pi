"""Tests de las posesiones por tramo (`ingest/common/possessions.py`).

Lo que se fija aquí, porque cada cosa falla en silencio:

- **La fórmula** (FGA + 0,44·FTA − OREB + TOV) y su variante de viajes a la
  línea contados, con el 2+1 que NO abre posesión nueva.
- **Las fronteras**: semiabierto `[start, end)` (el evento del segundo de un
  cambio va al quinteto que entra), salvo el reloj `00:00`, que va al quinteto
  que acabó el periodo.
- **NULL y no 0** cuando el partido no tiene tiros tipados en `play_events`
  (ingestas anteriores): es lo que deja a la interfaz distinguir "sin dato".
- **El reescalado** al total de referencia de `game_advanced_stats`, y que no
  se aplique con un factor absurdo.

Los tipos de tiro (`fg2_made`...`ft_missed`) todavía no los escribe ningún
adaptador en este árbol (los trae la ingesta de tiros en paralelo): aquí se
insertan a mano con los nombres del contrato.
"""
import pytest
from sqlalchemy import text

from ingest.common import possessions
from ingest.common.possessions import (
    TeamCounts,
    assign_stint,
    compute_game_possessions,
    reference_possessions,
    update_stint_possessions,
)


def _ev(team, seconds, event_type, clock="05:00"):
    return {"team_id": team, "seconds": seconds, "game_clock": clock, "event_type": event_type}


_STINTS = [
    {"id": 1, "team_id": "A", "start_seconds": 0.0, "end_seconds": 300.0},
    {"id": 2, "team_id": "A", "start_seconds": 300.0, "end_seconds": 600.0},
    {"id": 4, "team_id": "A", "start_seconds": 600.0, "end_seconds": 900.0},
    {"id": 3, "team_id": "B", "start_seconds": 0.0, "end_seconds": 600.0},
    {"id": 5, "team_id": "B", "start_seconds": 600.0, "end_seconds": 900.0},
]

_EVENTS = [
    _ev("A", 100, "fg2_made"),
    _ev("A", 200, "fg3_missed"),
    _ev("A", 205, "oreb"),
    _ev("A", 210, "fg2_made"),
    _ev("B", 150, "fg2_missed"),
    _ev("B", 160, "dreb"),       # no entra en la fórmula
    _ev("B", 160, "steal"),      # tampoco: la pérdida es la del otro equipo
    _ev("A", 300, "turnover"),   # segundo exacto del cambio 1 -> 2: va al que ENTRA (2)
    _ev("B", 400, "ft_made"),
    _ev("B", 400, "ft_missed"),
    _ev("A", 600, "fg3_made", clock="00:00"),  # bocina del periodo: va al que ACABA (2)
    _ev("A", 600, "fg2_made", clock="10:00"),  # primera jugada del periodo siguiente (4)
]


def test_formula_is_the_standard_boxscore_estimate():
    counts = TeamCounts(fga=60, fta=20, oreb=10, tov=12)
    assert counts.possessions() == pytest.approx(60 + 0.44 * 20 - 10 + 12)


def test_trips_mode_counts_trips_instead_of_the_044_factor():
    counts = TeamCounts(fga=60, fta=20, ft_trips=9, oreb=10, tov=12)
    assert counts.possessions("trips") == pytest.approx(60 + 9 - 10 + 12)


def test_half_open_boundaries_and_end_of_period_rule():
    team_a = [(1, 0.0, 300.0), (2, 300.0, 600.0), (4, 600.0, 900.0)]
    assert assign_stint(team_a, 300.0, "05:00") == 2       # [start, end): al que entra
    assert assign_stint(team_a, 600.0, "00:00") == 2       # bocina: al que acaba el periodo
    assert assign_stint(team_a, 600.0, "10:00") == 4
    assert assign_stint(team_a, 900.0, "00:00") == 4       # final del partido
    # Último segundo sin reloj 00:00 y sin tramo que empiece ahí: se prueba la otra regla.
    assert assign_stint(team_a, 900.0, "00:01") == 4
    # Hueco sin quinteto de cinco: sin tramo.
    assert assign_stint([(1, 0.0, 100.0), (2, 200.0, 300.0)], 150.0, "05:00") is None


def test_stint_possessions_split_own_and_opponent_events_by_window():
    result = compute_game_possessions("g", ["A", "B"], _EVENTS, _STINTS, rescale=False)

    assert result.has_shot_events is True
    # Tramo 1 (A, 0-300): 3 FGA − 1 OREB de A; 1 FGA de B.
    assert result.stints[1] == (2.0, 1.0)
    # Tramo 2 (A, 300-600): pérdida del segundo 300 + triple sobre la bocina; 2 libres de B.
    assert result.stints[2] == (2.0, 0.88)
    # Tramo 4 (A, 600-900): la canasta del arranque del periodo siguiente.
    assert result.stints[4] == (1.0, 0.0)
    # Tramos de B: los mismos eventos vistos desde el otro lado.
    assert result.stints[3] == (1.88, 4.0)
    assert result.stints[5] == (0.0, 1.0)
    # Total del partido por equipo, con todos sus eventos.
    assert result.estimated == {"A": pytest.approx(5.0), "B": pytest.approx(1.88)}
    # Sin referencia: no se reescala.
    assert result.scale == {"A": 1.0, "B": 1.0}


def test_and_one_free_throw_is_not_a_possession_in_trips_mode():
    events = [
        _ev("A", 100, "fg2_made"), _ev("A", 100, "ft_made"),   # 2+1: una posesión, no dos
        _ev("A", 200, "ft_made"), _ev("A", 200, "ft_made"),    # viaje de 2 libres: una posesión
        _ev("A", 250, "ft_made"), _ev("A", 250, "ft_missed"), _ev("A", 250, "ft_made"),  # de 3: una
    ]
    stints = [{"id": 1, "team_id": "A", "start_seconds": 0.0, "end_seconds": 600.0}]

    trips = compute_game_possessions("g", ["A", "B"], events, stints, rescale=False, ft_mode="trips")
    factor = compute_game_possessions("g", ["A", "B"], events, stints, rescale=False, ft_mode="factor")

    assert trips.stints[1][0] == pytest.approx(3.0)
    assert factor.stints[1][0] == pytest.approx(1 + 0.44 * 6)


def test_unknown_ft_mode_fails_loudly():
    with pytest.raises(ValueError):
        compute_game_possessions("g", ["A", "B"], _EVENTS, _STINTS, ft_mode="exacto")


def test_no_shot_events_means_no_estimate():
    events = [_ev("A", 100, "turnover"), _ev("A", 200, "oreb")]
    result = compute_game_possessions("g", ["A", "B"], events, _STINTS)
    assert result.has_shot_events is False
    assert result.stints == {}


def test_rescale_matches_the_reference_total():
    result = compute_game_possessions("g", ["A", "B"], _EVENTS, _STINTS, reference={"A": 5.5, "B": None})

    assert result.scale["A"] == pytest.approx(1.1)
    assert result.scale["B"] == 1.0  # sin referencia, sin reescalar
    assert result.stints[1] == (2.2, 1.0)
    assert result.stints[3] == (1.88, 4.4)
    # Con los tramos cubriendo el partido entero, suman exactamente la referencia.
    own_a = sum(result.stints[i][0] for i in (1, 2, 4))
    assert own_a == pytest.approx(5.5)
    assert result.discrepancy_pct("A") == pytest.approx(100 * (5.0 - 5.5) / 5.5)


def test_rescale_is_skipped_when_the_factor_is_absurd():
    result = compute_game_possessions("g", ["A", "B"], _EVENTS, _STINTS, reference={"A": 20.0})
    assert result.scale["A"] == 1.0
    assert result.stints[1] == (2.0, 1.0)


def test_rescale_can_be_turned_off():
    result = compute_game_possessions("g", ["A", "B"], _EVENTS, _STINTS, reference={"A": 5.5}, rescale=False)
    assert result.scale["A"] == 1.0
    assert result.stints[1] == (2.0, 1.0)


# ------------------------------------------------------------- contra la BD --


def _insert_stint(conn, game_id, team_id, start, end):
    result = conn.execute(
        text(
            "INSERT INTO lineup_stints"
            " (game_id, team_id, start_seconds, end_seconds, points_for, points_against, margin_start)"
            " VALUES (:g, :t, :s, :e, 0, 0, 0)"
        ),
        {"g": game_id, "t": team_id, "s": start, "e": end},
    )
    return result.lastrowid


def _insert_event(conn, game_id, team_id, seconds, event_type, clock="05:00"):
    conn.execute(
        text(
            "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
            " event_type, home_score, away_score)"
            " VALUES (:g, :t, NULL, 'Q1', :c, :s, :e, 0, 0)"
        ),
        {"g": game_id, "t": team_id, "c": clock, "s": seconds, "e": event_type},
    )


def _stint_possessions(conn, game_id):
    return conn.execute(
        text("SELECT possessions_for, possessions_against FROM lineup_stints WHERE game_id = :g ORDER BY id"),
        {"g": game_id},
    ).all()


def test_update_writes_null_not_zero_without_shot_events(engine):
    """g5 del seed (bas-val) con tramos y solo pérdidas/robos: NULL, no 0."""
    with engine.begin() as conn:
        _insert_stint(conn, "g5", "bas", 0.0, 600.0)
        _insert_stint(conn, "g5", "val", 0.0, 600.0)
        _insert_event(conn, "g5", "bas", 100, "turnover")
        _insert_event(conn, "g5", "val", 100, "steal")
        result = update_stint_possessions(conn, "g5")
        assert result.has_shot_events is False
        assert _stint_possessions(conn, "g5") == [(None, None), (None, None)]


def test_update_writes_possessions_and_is_idempotent(engine):
    with engine.begin() as conn:
        _insert_stint(conn, "g5", "bas", 0.0, 600.0)
        _insert_stint(conn, "g5", "val", 0.0, 600.0)
        for seconds in (10, 40, 70):
            _insert_event(conn, "g5", "bas", seconds, "fg2_made")
        _insert_event(conn, "g5", "val", 20, "fg3_missed")
        _insert_event(conn, "g5", "val", 25, "turnover")
        update_stint_possessions(conn, "g5")
        first = _stint_possessions(conn, "g5")
        update_stint_possessions(conn, "g5")
        assert _stint_possessions(conn, "g5") == first == [(3.0, 2.0), (2.0, 3.0)]


def test_reference_comes_from_points_over_ortg(engine):
    """g5 del seed acaba 84-79; con ortg 112 de bas, sus posesiones de referencia son 75."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE game_advanced_stats SET ortg = 112.0 WHERE game_id = 'g5' AND team_id = 'bas'"))
        reference = reference_possessions(conn, "g5")
    assert reference["bas"] == pytest.approx(75.0)
    assert reference["val"] is None  # sin fila de avanzadas para val en el seed


def test_update_rescales_to_the_reference_in_the_db(engine):
    with engine.begin() as conn:
        # 84 puntos / 2 posesiones estimadas * ... -> ortg tal que la referencia sea 2.2 (factor 1,1).
        conn.execute(
            text("UPDATE game_advanced_stats SET ortg = :o WHERE game_id = 'g5' AND team_id = 'bas'"),
            {"o": 100.0 * 84 / 2.2},
        )
        _insert_stint(conn, "g5", "bas", 0.0, 600.0)
        _insert_event(conn, "g5", "bas", 10, "fg2_made")
        _insert_event(conn, "g5", "bas", 20, "fg2_missed")
        update_stint_possessions(conn, "g5")
        assert _stint_possessions(conn, "g5")[0][0] == pytest.approx(2.2)
        update_stint_possessions(conn, "g5", rescale=False)
        assert _stint_possessions(conn, "g5")[0][0] == pytest.approx(2.0)


def test_dry_run_computes_without_writing(engine):
    with engine.begin() as conn:
        _insert_stint(conn, "g5", "bas", 0.0, 600.0)
        _insert_event(conn, "g5", "bas", 10, "fg2_made")
        result = update_stint_possessions(conn, "g5", dry_run=True)
        assert list(result.stints.values()) == [(1.0, 0.0)]
        assert _stint_possessions(conn, "g5") == [(None, None)]


def test_shot_event_types_are_the_contract_names():
    """Contrato con la ingesta de tiros: si alguien renombra un tipo, esto salta."""
    assert possessions.SHOT_EVENT_TYPES == {
        "fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed",
    }
