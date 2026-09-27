"""Tests del patrón de rotación (`app/analytics/rotation_patterns.py`).

Todo se prueba sobre dos partidos sintéticos de un equipo en los que se sabe
de antemano quién sale, cuándo descansa la estrella y qué tramo pierde:

- `star` juega 0-420 y 720-2400 en los dos partidos: descansa los minutos 8-12.
- `bench` entra justo cuando se sienta `star`.
- En el partido `g1` el equipo pierde 0-10 mientras `star` está sentado y ya
  no lo recupera (llega al final con -10); `g2` va empatado todo el rato.
"""
import pandas as pd
import pytest

from app.analytics import rotation_patterns as rp

_CORE = ["c1", "c2", "c3", "c4"]


def _stints(game_id, first_id):
    """Tres tramos por partido: titulares, suplente por la estrella y titulares otra vez."""
    margin_mid = -10 if game_id == "g1" else 0
    stints = [
        (first_id, 0.0, 420.0, 10, 10, 0, ["star"] + _CORE),
        (first_id + 1, 420.0, 720.0, 0, 10 if game_id == "g1" else 0, 0, ["bench"] + _CORE),
        (first_id + 2, 720.0, 2400.0, 40, 40, margin_mid, ["star"] + _CORE),
    ]
    rows = []
    for stint_id, start, end, pf, pa, margin, players in stints:
        for p in players:
            rows.append({
                "stint_id": stint_id, "game_id": game_id, "start_seconds": start, "end_seconds": end,
                "points_for": pf, "points_against": pa, "margin_start": margin,
                "player_id": p, "player_name": p.upper(),
            })
    return rows


@pytest.fixture()
def rows():
    return pd.DataFrame(_stints("g1", 1) + _stints("g2", 10))


def test_minute_shares_are_proportions_of_all_games(rows):
    shares = rp.minute_shares(rows).set_index(["player_id", "minute"])["share"]

    assert shares[("star", 1)] == pytest.approx(1.0)
    assert shares[("star", 9)] == pytest.approx(0.0)
    assert shares[("bench", 9)] == pytest.approx(1.0)
    assert shares[("star", 8)] == pytest.approx(0.0)       # 420-480 s -> minuto 8, sentado
    assert shares[("star", 7)] == pytest.approx(1.0)
    assert len(rp.minute_shares(rows)) == 6 * 40


def test_a_game_the_player_missed_counts_as_zero():
    rows = pd.DataFrame(_stints("g1", 1) + [
        {**r, "player_id": "c1" if r["player_id"] == "star" else r["player_id"]}
        for r in _stints("g2", 10)
        if r["player_id"] != "c1"
    ])

    shares = rp.minute_shares(rows).set_index(["player_id", "minute"])["share"]

    assert shares[("star", 1)] == pytest.approx(0.5)


def test_rest_windows_only_inside_the_playing_span():
    series = [0.0] * 5 + [0.9] * 5 + [0.1] * 3 + [0.9] * 5 + [0.2] * 1 + [0.9] * 3 + [0.0] * 18

    assert rp.rest_windows(series) == [(11, 13)]  # el minuto 19 suelto no llega a ventana


def test_rotation_table_finds_the_stars_rest(rows):
    table = rp.player_rotation_table(rp.minute_shares(rows)).set_index("player_id")

    assert table.loc["star", "rest_windows"] == [(8, 12)]
    assert table.loc["star", "minutes_per_game"] == pytest.approx(35.0)
    assert table.index[0] in _CORE  # 40 min: los que nunca salen van primero


def test_starting_lineups(rows):
    starters = rp.starting_lineups(rows)

    assert len(starters) == 1
    assert starters.iloc[0]["players"] == ("C1", "C2", "C3", "C4", "STAR")
    assert starters.iloc[0]["games"] == 2
    assert starters.iloc[0]["share"] == pytest.approx(1.0)


def test_closing_players_only_in_close_games(rows):
    closers, close_games = rp.closing_players(rows)

    assert close_games == 1  # g1 llega al minuto 35 con -10; solo g2 (empate) es apretado
    assert set(closers["player_id"]) == {"star", *_CORE}
    assert closers["share"].max() == pytest.approx(1.0)


def test_block_performance_puts_the_run_in_its_minutes(rows):
    blocks = rp.block_performance(rows).set_index("block")

    # El 0-10 de g1 cae entre los segundos 420 y 720: bloques 5-8 (240-480) y 9-12 (480-720).
    assert blocks.loc["9-12", "plus_minus"] == pytest.approx(-10 * 240 / 300)
    assert blocks.loc["5-8", "plus_minus"] == pytest.approx(-10 * 60 / 300)  # el tramo titular va 10-10
    assert blocks["minutes"].sum() == pytest.approx(80.0)
    assert list(blocks.index)[:3] == ["1-4", "5-8", "9-12"]


def test_insights_mention_starters_rest_and_worst_block(rows):
    rotation = rp.player_rotation_table(rp.minute_shares(rows))
    on_off = pd.DataFrame([
        {"player_id": "star", "on_per_40": 3.0, "off_per_40": -8.0, "reliable": True},
    ])
    closers, close_games = rp.closing_players(rows)
    blocks = rp.block_performance(rows).assign(minutes=100.0)

    lines = rp.rotation_insights(
        "Rival", rotation, rp.starting_lineups(rows), closers, close_games, blocks, on_off,
        n_games=2, key_players=6,
    )
    text = "\n".join(lines)

    assert "Quinteto inicial más probable" in text
    assert "STAR (35 min/partido) suele descansar en los minutos 8-12" in text
    assert "Sin él, Rival hace -8.0 por 40" in text
    assert "peor tramo" in text and "9-12" in text


def test_empty_input_is_handled_everywhere():
    empty = pd.DataFrame()

    assert rp.minute_shares(empty).empty
    assert rp.starting_lineups(empty).empty
    assert rp.closing_players(empty)[1] == 0
    assert rp.block_performance(empty).empty
    assert rp.player_rotation_table(rp.minute_shares(empty)).empty
