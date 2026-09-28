"""Consultas de la probabilidad de victoria y los momentos clave (propuesta 18, hoja de ruta A4).

`doc/features/propuestas/18_momentos_clave.md`. Módulo aparte de `queries.py`
(≈2.200 líneas, punto de fricción señalado en la hoja de ruta §B), pero sin
reescribir nada: la escalera del marcador es la MISMA que pinta la pestaña
"Rotaciones" (`queries._score_steps`/`game_score_steps`, con el 0-0 de salida
y el cierre en el marcador oficial), y el quinteto de una ventana es el mismo
`queries.window_lineup`. Dos escaleras distintas para el mismo partido —una
en el gráfico de rotaciones y otra en el de probabilidad— es la forma más
rápida de que dos pestañas se contradigan.

La lógica (modelo, curva, momentos, WPA, clips) vive en
`app/analytics/win_probability.py`, sin Streamlit ni SQL.
"""
from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import win_probability as wp
    from app.data import queries
except ImportError:  # pragma: no cover
    from analytics import win_probability as wp
    from data import queries

_TTL = 3600


def _league_score_states(engine: Engine, season_id: int, interval_s: float) -> pd.DataFrame:
    """Implementación sin cachear de `league_score_states` (ver su docstring).

    Un bucle de `_score_steps` por partido y no una consulta masiva a
    propósito: la escalera tiene dos detalles no triviales (el 0-0 de salida y
    el cierre con el marcador oficial, que recupera la última canasta cuando
    cae después del último evento tipado) y reescribirlos en otra consulta
    sería tener dos definiciones del marcador. Son ~740 lecturas indexadas por
    `game_id`, una vez por hora (caché).
    """
    games = pd.read_sql(
        text("""
            SELECT g.id, g.home_score, g.away_score
            FROM games g
            WHERE g.season_id = :season_id
              AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
              AND g.home_score <> g.away_score
              AND EXISTS (SELECT 1 FROM play_events pe WHERE pe.game_id = g.id)
            ORDER BY g.id
        """),
        engine,
        params={"season_id": season_id},
    )
    frames = []
    for game in games.to_dict("records"):
        steps = queries._score_steps(engine, game["id"], None)
        frames.append(
            wp.sample_game_states(
                steps, game["home_score"] > game["away_score"], interval_s=interval_s, game_id=game["id"]
            )
        )
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["game_id", "seconds", "seconds_remaining", "margin", "home_win"])
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def league_score_states(
    _engine: Engine, season_id: int, interval_s: float = wp.SAMPLE_INTERVAL_S
) -> pd.DataFrame:
    """Estados del marcador de TODA la liga en una temporada, cada `interval_s` segundos.

    Todas las competiciones y todos los equipos (no solo el Baskonia): el
    modelo necesita cientos de partidos, y "cómo de decisivo es un +5 a falta
    de dos minutos" no depende de quién juegue.

    Returns:
        `game_id, seconds, seconds_remaining, margin, home_win` (margen del
        local). Solo partidos acabados (sin empate) y con play-by-play tipado.
    """
    return _league_score_states(_engine, season_id, interval_s)


@st.cache_data(ttl=_TTL, show_spinner=False)
def league_wp_model(_engine: Engine, season_id: int) -> wp.WinProbabilityModel:
    """Modelo de probabilidad de victoria de la temporada (logística o de reserva)."""
    return wp.fit_win_probability(_league_score_states(_engine, season_id, wp.SAMPLE_INTERVAL_S))


def _game_row(engine: Engine, game_id: str) -> Optional[dict]:
    df = pd.read_sql(
        text("SELECT id, season_id, home_team_id, away_team_id, home_score, away_score FROM games WHERE id = :g"),
        engine,
        params={"g": game_id},
    )
    return None if df.empty else df.iloc[0].to_dict()


def _five(stints: pd.DataFrame, start: float, end: float) -> Optional[str]:
    lineup = queries.window_lineup(stints, start, end)
    return None if lineup.empty else " · ".join(lineup["player_name"])


def game_key_moments(
    engine: Engine,
    game_id: str,
    team_id: str,
    *,
    n: int = wp.N_KEY_MOMENTS,
    window_s: float = wp.KEY_MOMENT_WINDOW_S,
    season_id: Optional[int] = None,
    team_label: str = "",
) -> Optional[dict]:
    """Todo lo de la pestaña "Momentos clave" de un partido, visto desde `team_id`.

    Sin caché propia a propósito: se apoya en piezas ya cacheadas (modelo de
    la liga, escalera, tramos) y lo que añade es cálculo en memoria de
    milisegundos; cachear el conjunto obligaría a serializar el modelo como
    argumento.

    Args:
        team_id: desde qué equipo se mira (normalmente el propio). Tiene que
            jugar el partido.
        season_id: temporada con la que ajustar el modelo; por defecto la
            del propio partido.
        team_label: nombre corto del equipo para la descripción de los clips.

    Returns:
        `None` si el partido no existe o `team_id` no lo juega. Si no, un dict
        con `model`, `curve` (`game_wp_curve`), `moments` (`key_moments` + las
        columnas `lineup_own`/`lineup_rival`), `lineups_own`/`lineups_rival`
        (`lineup_wpa`), `clips` (`clip_list`), `is_home`, `rival_team_id`.
        Con la escalera vacía (sin play-by-play), `curve`/`moments`/`clips`
        vienen vacíos.
    """
    game = _game_row(engine, game_id)
    if game is None or team_id not in (game["home_team_id"], game["away_team_id"]):
        return None
    is_home = team_id == game["home_team_id"]
    rival_team_id = game["away_team_id"] if is_home else game["home_team_id"]

    model = league_wp_model(engine, int(season_id if season_id is not None else game["season_id"]))
    steps = queries.game_score_steps(engine, game_id, team_id)
    curve = wp.game_wp_curve(steps, model, is_home=is_home)
    moments = wp.key_moments(curve, n=n, window_s=window_s)

    own_stints = queries.game_stints(engine, game_id, team_id)
    rival_stints = queries.game_stints(engine, game_id, rival_team_id)
    moments = moments.assign(
        lineup_own=[_five(own_stints, r["start_seconds"], r["end_seconds"]) for r in moments.to_dict("records")],
        lineup_rival=[_five(rival_stints, r["start_seconds"], r["end_seconds"]) for r in moments.to_dict("records")],
    )
    game_end = float(curve["seconds"].max()) if not curve.empty else None
    return {
        "model": model,
        "curve": curve,
        "moments": moments,
        "lineups_own": wp.lineup_wpa(curve, own_stints, model, is_home=is_home),
        "lineups_rival": wp.lineup_wpa(curve, rival_stints, model, is_home=is_home, team_is_curve_team=False),
        "clips": wp.clip_list(moments, game_end=game_end, team_label=team_label),
        "is_home": is_home,
        "rival_team_id": rival_team_id,
    }
