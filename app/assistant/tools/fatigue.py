"""Herramienta de fatiga y calendario cruzado ACB + Euroliga (propuesta 04).

`fatigue_profile` es la respuesta de una pieza a "¿cómo llega el rival?"
(§6 de `doc/features/propuestas/04_fatiga_y_calendario.md`): descanso real
desde el último partido —en CUALQUIER competición, el matiz que motiva toda
la propuesta—, su carga de minutos reciente jugador a jugador y cómo rinde el
equipo con poco descanso frente a con más. Mismas tres consultas que pintan
los bloques de "Carga de minutos" (`app/screens/estado_equipo.py`) y "Fatiga y
descanso" (`app/screens/proximo_rival.py`) — `queries.rest_days`/
`rolling_load`/`performance_by_rest` —, aquí en una sola llamada en vez de en
varios bloques de pantalla.
"""
import datetime as dt

import pandas as pd

from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from data import queries, queries_assistant


def _team_season(ctx: ToolContext, team_id: str, season_id):
    """Como `team.py::_team_season` (mismo criterio de caída a temporada anterior).

    Duplicada en vez de importada de `.team` a propósito: ese módulo no
    expone la función públicamente (empieza por `_`) y este archivo no debe
    depender de un detalle interno de otro módulo de herramientas.
    """
    preferred = ctx.season_id if season_id is None else int(season_id)
    return season_with_fallback(queries.team_scouting_season, ctx.engine, team_id, preferred)


#: Agrupación de los cuatro tramos de `queries.performance_by_rest` en el
#: contraste de dos que pide el documento (§2b): "con dos días de descanso
#: concede X puntos más por 100 posesiones que con más".
_SHORT_REST_BUCKETS = ("≤1", "2")
_LONG_REST_BUCKETS = ("3-4", "≥5")


def _weighted_net_rating(perf: pd.DataFrame, buckets) -> dict:
    subset = perf[perf["rest_bucket"].isin(buckets)]
    gp = int(subset["gp"].sum())
    net_rating = round(float((subset["net_rating"] * subset["gp"]).sum() / gp), 1) if gp else None
    return {"gp": gp, "net_rating": net_rating}


@register(
    "fatigue_profile",
    family="team",
    description=(
        "Cómo llega un equipo AHORA MISMO: días de descanso desde su último partido (en cualquier "
        "competición, no solo la que juega hoy), sus partidos de los últimos 7/14 días, carga de "
        "minutos reciente de sus jugadores principales y su net rating con poco descanso frente a "
        "con más. La respuesta a '¿cómo llega X?' de una sola pieza — para el rival del próximo "
        "partido, resuelve antes su team_id con next_opponent o resolve_entity."
    ),
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="table",
)
def fatigue_profile(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    """Descanso + carga móvil + rendimiento por descanso, cruzados en una respuesta.

    Aproximación desde calendario y minutos de partido (§5 del documento):
    sin datos médicos ni de entrenamiento, esto NO es predicción de lesión, y
    el aviso de esa limitación viaja siempre en `meta.warnings`.
    """
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    warnings = [
        "Aproximación desde calendario y minutos de partido, no datos médicos ni de entrenamiento — "
        "no lo presentes como predicción de lesión."
    ]
    if fallback_warning:
        warnings.append(fallback_warning)

    rest = queries.rest_days(ctx.engine, team_id, season)
    if rest.empty:
        return fail(
            "sin datos",
            detail=f"El equipo {team_id} no tiene partidos en la temporada {season}.",
            suggestion="Prueba con la temporada anterior: un rival de Euroliga puede no haber jugado todavía.",
        )

    today_iso = ctx.today.isoformat()
    past = rest[rest["game_date"] <= today_iso]
    if past.empty:
        return fail(
            "sin partidos jugados",
            detail=f"{team_id} tiene calendario cargado en la temporada {season} pero ningún partido jugado todavía.",
        )
    last_game = past.iloc[-1]
    days_since_last = (ctx.today - dt.date.fromisoformat(str(last_game["game_date"]))).days

    cutoff_14 = (ctx.today - dt.timedelta(days=14)).isoformat()
    cutoff_7 = (ctx.today - dt.timedelta(days=7)).isoformat()
    recent_14 = past[past["game_date"] >= cutoff_14]
    recent_7 = recent_14[recent_14["game_date"] >= cutoff_7]

    # Minutos de los últimos 7 días, y quién está por encima de su media de
    # temporada (§2b del documento) — mismo cruce que pinta la ficha de
    # fatiga del rival en "Próximo rival".
    top_load = []
    rolling = queries.rolling_load(ctx.engine, team_id, season, 7)
    if not rolling.empty:
        latest_date = rolling["game_date"].max()
        latest = rolling[rolling["game_date"] == latest_date].copy()
        averages = queries_assistant.team_roster_production(ctx.engine, team_id, season)
        latest = latest.merge(averages[["id", "min_avg"]], left_on="player_id", right_on="id", how="left")
        latest["minutes_per_game_recent"] = latest["rolling_minutes"] / latest["games_in_window"].replace(0, pd.NA)
        latest["above_season_average"] = latest["minutes_per_game_recent"] > latest["min_avg"]
        top_load = records(latest.drop(columns="id").sort_values("rolling_minutes", ascending=False).head(6))

    # Rendimiento con poco descanso frente a con más (§2b/§4): el dato que
    # convierte la ficha en decisión, agrupado y con el detalle de 4 tramos.
    perf = queries.performance_by_rest(ctx.engine, team_id, season)
    short_vs_long = None
    if not perf.empty:
        short_vs_long = {
            "short_rest": _weighted_net_rating(perf, _SHORT_REST_BUCKETS),
            "long_rest": _weighted_net_rating(perf, _LONG_REST_BUCKETS),
        }

    data = {
        "days_since_last_game": days_since_last,
        "last_game": clean_dict(last_game.to_dict()),
        "games_last_7_days": int(len(recent_7)),
        "games_last_14_days": int(len(recent_14)),
        "recent_games": records(recent_14.iloc[::-1]),
        "top_recent_load": top_load,
        "performance_by_rest": records(perf),
        "short_vs_long_rest": short_vs_long,
    }
    return ok(
        data,
        source="games + player_game_stats + game_advanced_stats",
        scope=f"a fecha de {today_iso} · temporada {season}",
        gp=int(len(past)),
        warnings=warnings,
        artifact=artifact("table", records(perf), title="Rendimiento por descanso"),
    )
