"""Consultas de la predicción del partido (propuesta 16, A2 de la hoja de ruta).

Mismo estilo y mismas reglas que `app/data/queries.py` (`_engine` sin hashear,
`@st.cache_data`, SQL parametrizado, solo `SELECT`). Módulo aparte y no dentro
de `queries.py` porque ese fichero ya pasa de 2.000 líneas (§B de la hoja de
ruta pide justo dividirlo por dominio).

**Solo lectura, también aquí**: `upcoming_matchups.predicted_net_rating`
existe en el esquema pero la aplicación nunca lo escribe — la predicción se
calcula al vuelo (`matchup_prediction`) y se cachea como cualquier consulta.
El cálculo es `app/analytics/prediction.py`; aquí solo se le dan los datos y
se cachea el resultado, que comparten la pantalla "Próximo rival", el
dossier `.pptx` y la herramienta `game_prediction` del asistente.
"""
import datetime as dt
from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

try:  # pragma: no cover - ver nota en assistant/tools/context.py
    from app.analytics import prediction
except ImportError:  # pragma: no cover
    from analytics import prediction

_TTL = 3600  # mismo criterio que `queries.py`: la ingesta corre por su cuenta.


@st.cache_data(ttl=_TTL, show_spinner=False)
def season_game_results(
    _engine: Engine, season_id: int, before: Optional[dt.date] = None, competition_id: Optional[int] = None
) -> pd.DataFrame:
    """Resultados de TODOS los partidos de la temporada (no solo del Baskonia), cronológicos.

    Args:
        before: solo partidos estrictamente anteriores a esa fecha — el
            ajuste de un partido nunca debe ver ese mismo partido (ni nada
            posterior) si ya estuviera cargado.
        competition_id: acota a una competición; `None` (por defecto) = todas
            juntas, que es lo que ancla ACB y Euroliga en una misma escala.

    Returns:
        `game_id, game_date, competition_id, competition, home_team_id,
        home_team, away_team_id, away_team, home_score, away_score`. Vacío si
        la temporada no tiene partidos.
    """
    sql = text("""
        SELECT g.id AS game_id, g.game_date, g.competition_id, c.name AS competition,
               g.home_team_id, th.name AS home_team, g.away_team_id, ta.name AS away_team,
               g.home_score, g.away_score
        FROM games g
        JOIN competitions c ON c.id = g.competition_id
        JOIN teams th ON th.id = g.home_team_id
        JOIN teams ta ON ta.id = g.away_team_id
        WHERE g.season_id = :season_id
          AND (:before IS NULL OR g.game_date < :before)
          AND (:competition_id IS NULL OR g.competition_id = :competition_id)
        ORDER BY g.game_date, g.id
    """)
    params = {
        "season_id": season_id,
        "before": None if before is None else before.isoformat(),
        "competition_id": competition_id,
    }
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_last_game_date(_engine: Engine, team_id: str, before: dt.date) -> Optional[dt.date]:
    """Fecha del último partido del equipo ANTES de `before`, en cualquier competición y temporada.

    Es el "anterior" de `queries.rest_days` (propuesta 04) mirado desde un
    partido que todavía no está en `games`. `None` si no hay ninguno.
    """
    sql = text("""
        SELECT MAX(game_date) FROM games
        WHERE (home_team_id = :team_id OR away_team_id = :team_id) AND game_date < :before
    """)
    with _engine.connect() as conn:
        value = conn.execute(sql, {"team_id": team_id, "before": before.isoformat()}).scalar()
    return None if value is None else dt.date.fromisoformat(str(value)[:10])


@st.cache_data(ttl=_TTL, show_spinner=False)
def upcoming_matchup_vs(_engine: Engine, opponent_team_id: str, today: dt.date) -> Optional[dict]:
    """Próximo partido del calendario (`upcoming_matchups`) contra ESE rival, o `None`.

    Como `queries.next_matchup` pero acotado a un rival: el asistente puede
    preguntar por el partido contra X aunque no sea el siguiente.

    Returns:
        `{"match_date", "is_home", "competition"}`.
    """
    sql = text("""
        SELECT um.match_date, um.is_home, c.name AS competition
        FROM upcoming_matchups um
        JOIN competitions c ON c.id = um.competition_id
        WHERE um.opponent_team_id = :team_id AND um.match_date >= :today
        ORDER BY um.match_date ASC
        LIMIT 1
    """)
    df = pd.read_sql(sql, _engine, params={"team_id": opponent_team_id, "today": today.isoformat()})
    return None if df.empty else df.iloc[0].to_dict()


def rest_until(engine: Engine, team_id: str, match_date: dt.date) -> Optional[int]:
    """Días de descanso con los que el equipo llega a `match_date` (`None` sin partido previo)."""
    last = team_last_game_date(engine, team_id, match_date)
    return None if last is None else (match_date - last).days


@st.cache_data(ttl=_TTL, show_spinner=False)
def season_backtest(_engine: Engine, season_id: int, before: dt.date) -> Optional[dict]:
    """`prediction.backtest` de la temporada con los partidos anteriores a `before`, cacheado aparte.

    Es lo caro (un reajuste por fecha: ~1-2 s con una temporada de ~700
    partidos) y solo depende de la temporada y la fecha, no del rival ni de
    la pista: con su propia caché, predecir contra otro rival el mismo día
    (el asistente lo hace) no lo repite.
    """
    games = season_game_results(_engine, season_id, before=before)
    if games.empty:
        return None
    return prediction.backtest(prediction.prepare_games(games))


@st.cache_data(ttl=_TTL, show_spinner=False)
def matchup_prediction(
    _engine: Engine,
    own_team_id: str,
    rival_team_id: str,
    season_id: int,
    match_date: dt.date,
    is_home: Optional[bool],
    competition: Optional[str] = None,
) -> Optional[dict]:
    """Predicción completa de un partido: ajuste de la liga, predicción, frases y backtest.

    Args:
        season_id: temporada cuyos partidos se usan para el ajuste (en
            "Próximo rival", la de scouting del rival).
        match_date: fecha del partido; el ajuste solo ve partidos anteriores
            y el descanso de cada equipo se cuenta hasta ella.
        is_home/competition: condición del equipo propio y nombre de la
            competición del partido — una sede neutral (Copa, Supercopa)
            anula la ventaja de campo (`prediction.NEUTRAL_COMPETITIONS`), y
            `is_home=None` también (sede desconocida o neutral).

    Returns:
        `{"prediction", "fit", "backtest", "ratings", "own_rest",
        "rival_rest", "venue"}` (ver `analytics/prediction.py`), o `None` si
        la temporada no tiene partidos anteriores a `match_date`.
    """
    games = season_game_results(_engine, season_id, before=match_date)
    if games.empty:
        return None
    prepared = prediction.prepare_games(games)
    fit = prediction.fit_ratings(prepared)
    if is_home is None or prediction.is_neutral_competition(competition):
        venue = "neutral"
    else:
        venue = "home" if is_home else "away"
    own_rest = rest_until(_engine, own_team_id, match_date)
    rival_rest = rest_until(_engine, rival_team_id, match_date)
    pred = prediction.predict_matchup(
        fit, own_team_id, rival_team_id, venue=venue, own_rest=own_rest, rival_rest=rival_rest
    )
    names = dict(zip(games["home_team_id"], games["home_team"]))
    names.update(zip(games["away_team_id"], games["away_team"]))
    return {
        "prediction": pred,
        "fit": fit,
        "backtest": season_backtest(_engine, season_id, match_date),
        "ratings": prediction.ratings_table(fit, names),
        "own_rest": own_rest,
        "rival_rest": rival_rest,
        "venue": venue,
    }
