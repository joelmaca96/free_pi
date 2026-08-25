"""Tests de `app/data/queries.py`: por ahora, solo `team_scouting_season`.

Es la pieza que decide de qué temporada sale el scouting de un rival en
"Próximo rival" (`app/pages/proximo_rival.py`) cuando la temporada
seleccionada todavía no tiene partidos suyos — cae a la última temporada con
datos, sin mirar nunca hacia delante, y lo señala (`is_fallback`). Caso real
documentado en la propia función: el Baskonia arranca 2026-2027 contra el
Olympiacos, que no ha jugado nada en esa temporada todavía pero sí 43
partidos en 2025-2026.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import team_scouting_season


@pytest.fixture()
def engine():
    """BD de scouting en memoria con el esquema + seed reales (temporada 2025-2026,
    `season_id=1`, con partidos de 'rm' pero ninguno de 'baxi'/'jb' — ver seed de
    `schema.sql`)."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _add_season(conn, season_id: int, label: str) -> None:
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (:id, :label)"), {"id": season_id, "label": label})


def _add_game(conn, game_id: str, season_id: int, home: str, away: str) -> None:
    conn.execute(
        text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace)"
            " VALUES (:id, :season_id, 1, :home, :away, '2026-01-01', 80, 75, 70.0)"
        ),
        {"id": game_id, "season_id": season_id, "home": home, "away": away},
    )


def test_returns_preferred_season_directly_when_it_already_has_games(engine):
    """'rm' tiene partidos en la temporada 1 (seed, `g1`) — la propia temporada
    pedida ya sirve, sin caer a ninguna otra."""
    result = team_scouting_season.__wrapped__(engine, "rm", 1)
    assert result == {"season_id": 1, "label": "2025-2026", "is_fallback": False}


def test_falls_back_to_the_last_season_with_data_and_flags_it(engine):
    """Caso real que motivó la función: el rival no ha jugado nada en la temporada
    seleccionada (2, nueva) pero sí en la anterior (1, con `g1` del seed)."""
    with engine.begin() as conn:
        _add_season(conn, 2, "2026-2027")

    result = team_scouting_season.__wrapped__(engine, "rm", 2)

    assert result == {"season_id": 1, "label": "2025-2026", "is_fallback": True}


def test_never_looks_forward_to_a_later_season(engine):
    """'jb' no tiene partidos en la temporada 1 (seed) pero sí en la 2 (futura,
    añadida aquí) — pedir la temporada 1 NO debe devolver la 2: no es una
    aproximación razonable, sería otra cosa (ver docstring de la función)."""
    with engine.begin() as conn:
        _add_season(conn, 2, "2026-2027")
        _add_game(conn, "g-future", 2, "jb", "bas")

    result = team_scouting_season.__wrapped__(engine, "jb", 1)

    assert result is None


def test_returns_none_when_the_team_has_no_games_in_any_season_up_to_preferred(engine):
    """'baxi' no tiene ningún partido en el seed — ni la propia temporada pedida
    ni ninguna anterior sirven; no hay nada a lo que caer."""
    result = team_scouting_season.__wrapped__(engine, "baxi", 1)

    assert result is None


def test_falls_back_across_more_than_one_season_gap(engine):
    """El hueco puede ser de más de una temporada (rival con calendario futuro
    cargado pero sin jugar nada en dos temporadas seguidas) — sigue cayendo a la
    última con datos, no a la inmediatamente anterior porque sí."""
    with engine.begin() as conn:
        _add_season(conn, 2, "2026-2027")
        _add_season(conn, 3, "2027-2028")

    result = team_scouting_season.__wrapped__(engine, "rm", 3)

    assert result == {"season_id": 1, "label": "2025-2026", "is_fallback": True}
