"""Test de integración: `parse_and_resolve` reconstruye lineups desde play-by-play
cuando el contrato no trae ya una lista `lineups` explícita.
"""
from sqlalchemy import text

from ingest.common.raw_game import parse_and_resolve

# Dorsales fuera del rango usado por el seed (bas: 0,2,8,10,12,21,33,95) para
# que no haya coincidencias accidentales al resolver identidad por equipo+dorsal.
RAW_GAME_WITH_PBP = {
    "game_id": "PBP1",
    "date": "2026-03-01",
    "season": 2025,
    "competition": "ACB",
    "home_team": {"id": "src-bas", "name": "Baskonia"},
    "away_team": {"id": "src-rm", "name": "Real Madrid"},
    "home_score": 10,
    "away_score": 6,
    "pace": 70.0,
    "players": [
        {"player_id": "src-h1", "team_id": "src-bas", "name": "Home Uno", "number": 41, "minutes": 40.0,
         "pts": 4, "reb": 1, "ast": 1, "efg_pct": 50.0, "starter": True},
        {"player_id": "src-h2", "team_id": "src-bas", "name": "Home Dos", "number": 42, "minutes": 20.0,
         "pts": 2, "reb": 1, "ast": 0, "efg_pct": 50.0, "starter": True},
        {"player_id": "src-h3", "team_id": "src-bas", "name": "Home Tres", "number": 43, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
        {"player_id": "src-h4", "team_id": "src-bas", "name": "Home Cuatro", "number": 44, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
        {"player_id": "src-h5", "team_id": "src-bas", "name": "Home Cinco", "number": 45, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
        {"player_id": "src-h6", "team_id": "src-bas", "name": "Home Seis (banquillo)", "number": 46,
         "minutes": 20.0, "pts": 4, "reb": 1, "ast": 0, "efg_pct": 50.0, "starter": False},
        {"player_id": "src-a1", "team_id": "src-rm", "name": "Away Uno", "number": 51, "minutes": 40.0,
         "pts": 6, "reb": 1, "ast": 0, "efg_pct": 50.0, "starter": True},
        {"player_id": "src-a2", "team_id": "src-rm", "name": "Away Dos", "number": 52, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
        {"player_id": "src-a3", "team_id": "src-rm", "name": "Away Tres", "number": 53, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
        {"player_id": "src-a4", "team_id": "src-rm", "name": "Away Cuatro", "number": 54, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
        {"player_id": "src-a5", "team_id": "src-rm", "name": "Away Cinco", "number": 55, "minutes": 40.0,
         "pts": 0, "reb": 1, "ast": 0, "efg_pct": 0.0, "starter": True},
    ],
    "play_by_play": [
        {"team_id": "src-bas", "type": "score", "quarter": "Q1", "clock": "09:00", "points": 2},
        {"team_id": "src-bas", "type": "sub_out", "quarter": "Q1", "clock": "05:00", "player_id": "src-h2"},
        {"team_id": "src-bas", "type": "sub_in", "quarter": "Q1", "clock": "05:00", "player_id": "src-h6"},
        {"team_id": "src-bas", "type": "score", "quarter": "Q1", "clock": "02:00", "points": 2},
        {"team_id": "src-rm", "type": "score", "quarter": "Q1", "clock": "01:00", "points": 6},
    ],
}


def _internal_id(conn, external_id: str) -> str:
    return conn.execute(
        text("SELECT player_id FROM player_external_ids WHERE source='acb' AND external_id=:eid"),
        {"eid": external_id},
    ).scalar_one()


def test_parse_and_resolve_reconstructs_lineups_from_play_by_play(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME_WITH_PBP, source="acb")
        ids = {f"h{i}": _internal_id(conn, f"src-h{i}") for i in range(1, 7)}
        ids.update({f"a{i}": _internal_id(conn, f"src-a{i}") for i in range(1, 6)})

    # "Baskonia"/"Real Madrid" normalizan a los equipos reales del seed (bas/rm).
    assert game.home_team_id == "bas"
    assert game.away_team_id == "rm"

    starters_key = frozenset(ids[f"h{i}"] for i in range(1, 6))
    second_key = frozenset([ids["h1"], ids["h3"], ids["h4"], ids["h5"], ids["h6"]])
    away_key = frozenset(ids[f"a{i}"] for i in range(1, 6))

    by_combo = {frozenset(l.player_ids): l for l in game.lineups}
    assert set(by_combo) == {starters_key, second_key, away_key}

    assert by_combo[starters_key].minutes == 5.0  # Q1 10:00 -> 05:00
    assert by_combo[starters_key].plus_minus == 2

    assert by_combo[second_key].minutes == 4.0  # Q1 05:00 -> 01:00 (último evento registrado)
    assert by_combo[second_key].plus_minus == -4  # +2 propio, -6 del rival

    assert by_combo[away_key].minutes == 9.0  # Q1 10:00 -> 01:00, un solo quinteto todo el tramo
    assert by_combo[away_key].plus_minus == 2  # -2, -2 (canastas locales) + 6 (propia)
