"""Herramienta de señales semanales (propuesta 10): "¿qué ha cambiado esta semana?"

Reutiliza EXACTAMENTE el mismo motor que pinta el bloque de
`app/screens/estado_equipo.py` (`analytics.signals`) — la respuesta del
asistente y la de la pantalla nunca pueden divergir, porque las dos llaman a
las mismas funciones de detección (jugador, equipo, equipo por zona,
rotación, carga) y al mismo `select_top_signals` (§6 del documento: "Encaja
con el asistente como herramienta `weekly_signals`").

Sin capa de LLM aquí: `polish_headlines` es cosmética (pule la prosa de la
PANTALLA) y el modelo del asistente ya redacta su propia respuesta a partir
de los titulares de reglas — pulirlos dos veces sería trabajo doble para el
mismo texto.
"""
import datetime as dt
from dataclasses import asdict

import pandas as pd

from .base import ToolContext, ok, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import signals as signals_engine
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import signals as signals_engine
    from data import queries, queries_assistant

#: K por defecto de la ventana reciente (§4 del documento: "K = 5 por
#: defecto, configurable").
DEFAULT_LAST_N = 5


def _team_season(ctx: ToolContext, team_id: str, season_id):
    """Como `team.py::_team_season` (mismo criterio de caída a temporada anterior); duplicada
    a propósito, ver la nota de `fatigue.py::_team_season`."""
    preferred = ctx.season_id if season_id is None else int(season_id)
    return season_with_fallback(queries.team_scouting_season, ctx.engine, team_id, preferred)


def compute_weekly_signals(
    ctx: ToolContext, team_id: str, season: int, *, last_n: int = DEFAULT_LAST_N
) -> "tuple[list, dict]":
    """El cálculo compartido entre la herramienta y la pantalla: candidatos -> top 5 + procedencia.

    Returns:
        `(top_signals, meta)` — `top_signals` es la salida de `select_top_
        signals` (posiblemente vacía: "sin cambios significativos" es una
        respuesta correcta, §2), `meta` trae cuántos partidos alimentaron
        cada familia, para la procedencia de la herramienta y para que la
        pantalla pueda explicar por qué una familia falta (p.ej. sin
        `lineup_stints` cargados, `rotation_games` sale a 0).
    """
    team_name = queries.team_name(ctx.engine, team_id) or team_id

    player_log = queries.team_player_game_log(ctx.engine, team_id, season)
    team_log = queries.team_game_advanced_log(ctx.engine, team_id, season)
    zone_log = queries.team_game_zone_counts(ctx.engine, team_id, season)
    pair_window = queries_assistant.pair_minutes_by_window(ctx.engine, team_id, season, last_n)

    rolling = queries.rolling_load(ctx.engine, team_id, season, 7)
    rolling_latest = pd.DataFrame()
    if not rolling.empty:
        latest_date = rolling["game_date"].max()
        rolling_latest = rolling[rolling["game_date"] == latest_date]

    team_signals = signals_engine.detect_team_signals(
        team_log, team_id=team_id, team_name=team_name, last_n=last_n
    ) + signals_engine.detect_team_zone_signals(zone_log, team_id=team_id, team_name=team_name, last_n=last_n)
    candidates = signals_engine.all_candidates(
        player=signals_engine.detect_player_signals(player_log, last_n=last_n),
        team=team_signals,
        rotation=signals_engine.detect_rotation_signals(pair_window, last_n=last_n),
        load=signals_engine.detect_load_signals(rolling_latest),
    )
    top_signals = signals_engine.select_top_signals(candidates, max_signals=5)

    meta = {
        "player_games": int(player_log["game_id"].nunique()) if not player_log.empty else 0,
        "team_games": int(len(team_log)),
        "team_zone_games": int(zone_log["game_id"].nunique()) if not zone_log.empty else 0,
        "rotation_pairs_tracked": int(len(pair_window)),
        "candidates_evaluated": len(candidates),
    }
    return top_signals, meta


def _signal_to_dict(signal: "signals_engine.Signal") -> dict:
    """`Signal` -> dict compacto para el JSON de la herramienta (sin `context`/`headline_template`,
    que son detalle interno de formateo, no algo que el modelo deba citar)."""
    data = asdict(signal)
    data.pop("context", None)
    data.pop("headline_template", None)
    for key in ("recent_value", "baseline_value", "effect", "weight"):
        if data.get(key) is not None:
            data[key] = round(float(data[key]), 2)
    if data.get("p_value") is not None:
        data["p_value"] = round(float(data["p_value"]), 4)
    return data


@register(
    "weekly_signals",
    family="team",
    description=(
        "Qué ha cambiado DE VERDAD en un equipo en los últimos partidos: jugador (minutos, tiro, "
        "pérdidas, faltas, rebote ofensivo), equipo (cuatro factores + ritmo + reparto de tiro por "
        "zona), rotación (parejas que ganan o pierden peso) y carga (jugadores por encima del aviso "
        "de minutos). Máximo 5 señales, ya filtradas por significación estadística Y tamaño de "
        "efecto mínimo — si no hay nada, la "
        "respuesta correcta es decir que no hay cambios significativos esta semana, no forzar una."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "last_n": {
                "type": "integer",
                "description": "Partidos recientes de la ventana (5 por defecto, §4 de la propuesta).",
            },
        },
        required=["team_id"],
    ),
    artifact="table",
)
def weekly_signals(ctx: ToolContext, team_id: str, season_id: int = None, last_n: int = None) -> dict:
    """Señales semanales de `team_id`: la misma lista que pinta la pantalla, en JSON."""
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    k = int(last_n) if last_n else DEFAULT_LAST_N

    top_signals, meta = compute_weekly_signals(ctx, team_id, season, last_n=k)

    warnings = [
        "Aproximación desde calendario y boxscore, sin ajustar por calidad de rival — un cambio puede "
        "ser el nivel de los últimos rivales, no una tendencia real (mira 'extra_note' en cada señal).",
    ]
    if fallback_warning:
        warnings.append(fallback_warning)

    data = {
        "signals": [_signal_to_dict(s) for s in top_signals],
        "window_games": k,
    }
    return ok(
        data,
        source="player_game_stats + game_advanced_stats + lineup_stints",
        scope=f"últimos {k} partidos vs. resto de la temporada {season}",
        gp=meta["team_games"],
        warnings=warnings,
    )
