"""Test de una colisión de identidad real (hallada reingiriendo producción, 2026-08-27).

`resolve_or_create_player` (`ingest/common/identity.py`) resolvía un jugador
nuevo, sin `player_external_ids` todavía, por "mismo `team_id` + mismo
dorsal" — sin comprobar que fuera la misma persona. En un partido real de ACB
(104622, MoraBanc Andorra), dos jugadores DISTINTOS con dorsal `0` en el
mismo equipo (dado de baja uno, alta el otro, más habitual de lo que parece a
mitad de temporada) acababan resolviendo al MISMO `player_id` interno.
`player_game_stats` absorbe eso en silencio vía `ON CONFLICT DO UPDATE` (no
rompe, pero mezcla estadísticas de dos personas reales bajo un jugador); las
tablas de Fase 3/4 (`player_game_quarter_stats`/`player_advanced_stats`,
`PRIMARY KEY` estricta sin upsert) sí rompían la carga del partido ENTERO por
esto — mitigado con `ingest/common/raw_game.py::
_dedupe_after_identity_collision`.

LA CAUSA RAÍZ YA ESTÁ CERRADA: el emparejamiento por dorsal exige además un
apellido en común (`identity._shares_a_surname`), así que Shannon Evans y
Morris Udeze ya no se funden. Este fichero comprueba las dos mitades: que la
colisión no ocurre, y que la red de seguridad de las tablas de PK estricta
sigue puesta para las colisiones que SÍ quedan (dos jugadores con el mismo
nombre normalizado, que el dorsal ya no puede desempatar).
"""
from ingest.common.raw_game import parse_and_resolve

# Dorsal 0 repetido a propósito en el mismo equipo — dos ids externos
# distintos, sin `player_external_ids` previo, que colisionan por el
# fallback "team_id + dorsal" de `resolve_or_create_player`.
RAW_GAME_WITH_A_SHARED_DORSAL = {
    "game_id": "COLLISION1",
    "date": "2026-03-01",
    "season": 2025,
    "competition": "ACB",
    "home_team": {"id": "src-bas", "name": "Baskonia"},
    "away_team": {"id": "src-rm", "name": "Real Madrid"},
    "home_score": 80,
    "away_score": 78,
    "pace": 70.0,
    "players": [
        {"player_id": "src-h1", "team_id": "src-bas", "name": "Home Uno", "number": 61, "minutes": 40.0,
         "pts": 4, "reb": 1, "ast": 1, "efg_pct": 50.0, "starter": True},
        {"player_id": "src-shannon", "team_id": "src-bas", "name": "Shannon Evans", "number": 0, "minutes": 10.0,
         "pts": 2, "reb": 0, "ast": 0, "efg_pct": 50.0, "starter": False},
        # Mismo equipo, mismo dorsal (0), id externo DISTINTO -> colisiona
        # con el jugador de arriba vía el fallback de identidad.
        {"player_id": "src-udeze", "team_id": "src-bas", "name": "Morris Udeze", "number": 0, "minutes": 8.0,
         "pts": 3, "reb": 2, "ast": 0, "efg_pct": 60.0, "starter": False},
        {"player_id": "src-a1", "team_id": "src-rm", "name": "Away Uno", "number": 71, "minutes": 40.0,
         "pts": 6, "reb": 1, "ast": 0, "efg_pct": 50.0, "starter": True},
    ],
    "quarter_boxscore": [
        {"player_id": "src-shannon", "quarter": 1, "pts": 2, "stl": 1},
        {"player_id": "src-udeze", "quarter": 1, "pts": 3, "stl": 0},
    ],
    "player_advanced": [
        {"player_id": "src-shannon", "ts_pct": 55.0},
        {"player_id": "src-udeze", "ts_pct": 62.0},
    ],
}


def test_two_different_players_sharing_a_dorsal_stay_apart(engine, caplog):
    """Shannon Evans y Morris Udeze son dos personas: mismo equipo y mismo dorsal 0, pero
    ningún apellido en común. Cada uno tiene que quedarse con su propio `player_id`, y sus
    estadísticas con él."""
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME_WITH_A_SHARED_DORSAL, source="acb")

    assert len(game.boxscore) == 4
    resolved_ids = {stat.player_id for stat in game.boxscore}
    assert len(resolved_ids) == 4, "dos jugadores distintos fundidos bajo un mismo player_id"

    # Y cada línea de boxscore sigue siendo la suya, no la del otro.
    by_id = {stat.player_id: stat for stat in game.boxscore}
    assert sorted(s.pts for s in by_id.values()) == [2, 3, 4, 6]

    assert not any("colisión de identidad" in record.message for record in caplog.records)


#: Colisión que el dorsal ya NO puede desempatar: dos ids externos distintos
#: para el mismo nombre normalizado. Es el caso que le queda a la red de
#: seguridad de abajo — poco frecuente, pero real (homónimos, o la misma
#: persona con dos fichas en la fuente).
RAW_GAME_WITH_A_SHARED_NAME = {
    **RAW_GAME_WITH_A_SHARED_DORSAL,
    "game_id": "COLLISION2",
    "players": [
        {"player_id": "src-h1", "team_id": "src-bas", "name": "Home Uno", "number": 61, "minutes": 40.0,
         "pts": 4, "reb": 1, "ast": 1, "efg_pct": 50.0, "starter": True},
        {"player_id": "src-a", "team_id": "src-bas", "name": "Morris Udeze", "number": 0, "minutes": 10.0,
         "pts": 2, "reb": 0, "ast": 0, "efg_pct": 50.0, "starter": False},
        {"player_id": "src-b", "team_id": "src-bas", "name": "Morris Udeze", "number": 4, "minutes": 8.0,
         "pts": 3, "reb": 2, "ast": 0, "efg_pct": 60.0, "starter": False},
        {"player_id": "src-a1", "team_id": "src-rm", "name": "Away Uno", "number": 71, "minutes": 40.0,
         "pts": 6, "reb": 1, "ast": 0, "efg_pct": 50.0, "starter": True},
    ],
    "quarter_boxscore": [
        {"player_id": "src-a", "quarter": 1, "pts": 2, "stl": 1},
        {"player_id": "src-b", "quarter": 1, "pts": 3, "stl": 0},
    ],
    "player_advanced": [
        {"player_id": "src-a", "ts_pct": 55.0},
        {"player_id": "src-b", "ts_pct": 62.0},
    ],
}


def test_a_remaining_collision_still_does_not_crash_the_game_load(engine, caplog):
    """`player_game_quarter_stats`/`player_advanced_stats` (PK estricta, sin upsert) se quedan
    con la PRIMERA fila de la colisión, no con las dos — sin esto, `load_game` reventaba con
    `IntegrityError` y se perdía el partido ENTERO (boxscore, tiros, quintetos) por una tabla
    que ni siquiera es la fuente de verdad de esas estadísticas."""
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME_WITH_A_SHARED_NAME, source="acb")

    quarter_keys = [(row.player_id, row.quarter) for row in game.quarter_boxscore]
    assert len(quarter_keys) == len(set(quarter_keys))  # sin duplicados de clave

    advanced_player_ids = [row.player_id for row in game.player_advanced]
    assert len(advanced_player_ids) == len(set(advanced_player_ids))

    # La colisión que queda sigue registrándose, no se traga en silencio.
    assert any("colisión de identidad" in record.message for record in caplog.records)

    # Y lo más importante: `load_game` puede cargar el partido sin reventar.
    from ingest.common.loader import load_game

    with engine.begin() as conn:
        load_game(conn, game)
