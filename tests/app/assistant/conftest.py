"""Fixtures del asistente: base de datos temporal con datos suficientes para probar de verdad.

El seed de `schema.sql` (5 partidos del Baskonia, 8 jugadores) llega para
resolver entidades y para una línea de boxscore, pero **no** para nada que
necesite contexto de liga: las vistas de percentiles exigen 5 partidos por
equipo y competición, y con el seed ningún equipo llega. Por eso este fixture
añade una liga entera y sintética —pero determinista, sin aleatoriedad— en
una temporada aparte.

Todo lo de aquí es offline y desechable (un fichero por test en `tmp_path`,
ver el fixture `engine`): la suite del repo no toca `data/baskonia.db` y el
asistente no rompe esa propiedad (§12).
"""
import datetime as dt

import pytest
import streamlit as st
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.assistant.capabilities import probe
from app.assistant.tools.base import ToolContext

#: Temporada sintética con liga completa. La 1 es la del seed y se deja
#: intacta, para que un test que dependa del seed no dependa además de esto.
LEAGUE_SEASON_ID = 2
LEAGUE_SEASON_LABEL = "2026-2027"
LEAGUE_TEAMS = ["bas", "rm", "fcb", "val", "gc", "uni"]
TODAY = dt.date(2027, 3, 1)


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """Vacía la caché de `st.cache_data` entre tests.

    Las consultas de `app/data/` están cacheadas por sus argumentos, y el
    engine va como `_engine` (sin hashear, por convención de Streamlit). Sin
    esto, dos tests con bases de datos DISTINTAS pero el mismo `team_id`/
    `season_id` se leerían el resultado cacheado el uno del otro — un falso
    verde silencioso y dificilísimo de ver.
    """
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _add_league(conn, season_id: int, label: str) -> None:
    """Liga sintética: dos vueltas entre seis equipos, resultados deterministas.

    Los marcadores se derivan del índice de cada equipo (los primeros de la
    lista son mejores), así que el orden de la clasificación y los percentiles
    son predecibles y se pueden afirmar en un test sin copiar números mágicos.
    """
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (:id, :label)"), {"id": season_id, "label": label})

    game_number = 0
    for home_index, home in enumerate(LEAGUE_TEAMS):
        for away_index, away in enumerate(LEAGUE_TEAMS):
            if home == away:
                continue
            game_number += 1
            game_id = f"syn-{season_id}-{game_number}"
            # Cuanto menor el índice, mejor el equipo: el local suma 80 y
            # resta su índice, el visitante 78 y resta el suyo.
            home_score = 90 - home_index * 3
            away_score = 85 - away_index * 3
            pace = 68.0 + home_index + away_index
            conn.execute(
                text(
                    "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                    " game_date, home_score, away_score, pace)"
                    " VALUES (:id, :season_id, 1, :home, :away, :date, :hs, :as_, :pace)"
                ),
                {
                    "id": game_id,
                    "season_id": season_id,
                    "home": home,
                    "away": away,
                    # Fechas escalonadas dentro de la temporada, siempre en el
                    # pasado respecto de `TODAY`.
                    "date": (dt.date(2026, 10, 1) + dt.timedelta(days=game_number)).isoformat(),
                    "hs": home_score,
                    "as_": away_score,
                    "pace": pace,
                },
            )
            for team, index, points_for, points_against in (
                (home, home_index, home_score, away_score),
                (away, away_index, away_score, home_score),
            ):
                conn.execute(
                    text(
                        "INSERT INTO game_advanced_stats"
                        " (game_id, team_id, ortg, drtg, net_rating, efg_pct, ts_pct, tov_pct, orb_pct)"
                        " VALUES (:g, :t, :ortg, :drtg, :net, :efg, :ts, 12.0, 25.0)"
                    ),
                    {
                        "g": game_id,
                        "t": team,
                        "ortg": 115.0 - index * 2,
                        "drtg": 105.0 + index * 2,
                        "net": (115.0 - index * 2) - (105.0 + index * 2),
                        "efg": 56.0 - index,
                        "ts": 60.0 - index,
                    },
                )


def _add_shots(conn) -> None:
    """Unos tiros con coordenadas para Howard en `g5`.

    El seed de `schema.sql` no carga ninguno (`shots` está vacía), así que sin
    esto no se puede probar ni el perfil de tiro por zona ni el artefacto de
    mapa de tiros — que es el que la página pinta en la respuesta a la primera
    pregunta del encargo.
    """
    # (x, y, encestado, zona) — dos en la pintura, dos triples exteriores.
    shots = [
        (250, 400, 1, 1),
        (260, 410, 0, 1),
        (250, 100, 1, 6),
        (200, 120, 0, 6),
    ]
    for pos_x, pos_y, made, zone_id in shots:
        conn.execute(
            text(
                "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located)"
                " VALUES ('g5', 'howard', :zone, :x, :y, :made, 1)"
            ),
            {"zone": zone_id, "x": pos_x, "y": pos_y, "made": made},
        )


@pytest.fixture()
def engine(tmp_path):
    """BD de scouting en un fichero temporal: esquema + seed reales + liga sintética.

    En fichero y no en `:memory:`, a diferencia del resto de fixtures del
    repo, por dos motivos que solo aplican al asistente:

    1. **El agente ejecuta herramientas en paralelo** (§3.3). Con
       `sqlite:///:memory:`, SQLAlchemy usa un pool por hilo y cada hilo abre
       una base de datos NUEVA Y VACÍA: las llamadas paralelas fallarían con
       "no such table" por un artefacto del fixture, no por un fallo real.
    2. **`sql_guard` abre su propia conexión de solo lectura** (`mode=ro`),
       que es la guarda de verdad y solo existe con un fichero. Contra
       `:memory:` se probaría el camino secundario y nunca el real.

    Sigue siendo 100% offline y aislado de `data/baskonia.db`.
    """
    eng = create_scouting_engine(f"sqlite:///{(tmp_path / 'scouting.db').as_posix()}")
    init_scouting_db(eng)
    with eng.begin() as conn:
        _add_league(conn, LEAGUE_SEASON_ID, LEAGUE_SEASON_LABEL)
        _add_shots(conn)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def ctx(engine):
    """Contexto de herramienta sobre la temporada del SEED (la del boxscore real)."""
    return ToolContext(
        engine=engine,
        season_id=1,
        own_team_id="bas",
        today=TODAY,
        capabilities=probe(engine),
    )


@pytest.fixture()
def league_ctx(engine):
    """Contexto sobre la temporada sintética (la que sí tiene contexto de liga)."""
    return ToolContext(
        engine=engine,
        season_id=LEAGUE_SEASON_ID,
        own_team_id="bas",
        today=TODAY,
        capabilities=probe(engine),
    )


def add_stints(engine, rows) -> None:
    """Inserta tramos (`lineup_stints` + `lineup_stint_players`) para probar el clutch.

    Args:
        rows: iterables `(game_id, team_id, start, end, pf, pa, margin, [players])`.
    """
    with engine.begin() as conn:
        for game_id, team_id, start, end, points_for, points_against, margin, players in rows:
            result = conn.execute(
                text(
                    "INSERT INTO lineup_stints"
                    " (game_id, team_id, start_seconds, end_seconds, points_for, points_against, margin_start)"
                    " VALUES (:g, :t, :s, :e, :pf, :pa, :m)"
                ),
                {"g": game_id, "t": team_id, "s": start, "e": end, "pf": points_for, "pa": points_against, "m": margin},
            )
            for player_id in players:
                conn.execute(
                    text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                    {"s": result.lastrowid, "p": player_id},
                )
