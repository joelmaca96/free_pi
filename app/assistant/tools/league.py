"""Herramientas de liga y comparación (§4.5).

El contexto de liga es lo que convierte un número en una lectura: 73.6
posesiones no significan nada sueltas, y 21 puntos por partido tampoco. Aquí
viven las dos formas de dárselo al modelo — el ranking (`league_leaders`,
`standings`) y el percentil (`league_percentiles`, que es el motor de §6).
"""
from .base import ToolContext, artifact, fail, ok, records, register, schema

try:  # pragma: no cover - ver nota en tools/context.py
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from data import queries, queries_assistant

_METRIC_KEYS = sorted(queries_assistant.LEADER_METRICS)


def _season(ctx: ToolContext, season_id) -> int:
    return ctx.season_id if season_id is None else int(season_id)


def _competition_name(ctx: ToolContext, competition_id: int) -> str:
    competitions = queries.list_competitions(ctx.engine)
    match = competitions[competitions["id"] == competition_id]
    return match["name"].iloc[0] if not match.empty else str(competition_id)


@register(
    "league_leaders",
    family="league",
    description=(
        "Top N de una competición por métrica ('¿quién anota más en la ACB?'). Métricas de "
        "jugador: pts, reb, ast, min, efg, stl, blk, tov, pf, oreb, dreb, pir (tov/pf: menos es "
        "mejor, ya vienen ordenadas así). De equipo: pace, ortg, drtg, net_rating, team_efg, "
        "team_ts. Siempre dentro de UNA competición: mezclar ACB y Euroliga no da un ranking."
    ),
    parameters=schema(
        {
            "metric": {"type": "string", "enum": _METRIC_KEYS},
            "competition_id": {"type": "integer"},
            "season_id": {"type": "integer"},
            "min_gp": {
                "type": "integer",
                "description": "Partidos mínimos (5 por defecto). Sin mínimo lidera quien jugó un partido.",
            },
            "limit": {"type": "integer"},
        },
        required=["metric", "competition_id"],
    ),
    artifact="table",
)
def league_leaders(
    ctx: ToolContext,
    metric: str,
    competition_id: int,
    season_id: int = None,
    min_gp: int = 5,
    limit: int = 10,
) -> dict:
    season = _season(ctx, season_id)
    try:
        leaders = queries_assistant.league_leaders(ctx.engine, metric, season, competition_id, min_gp, limit)
    except ValueError as exc:
        return fail("métrica no soportada", detail=str(exc), suggestion=f"Usa una de: {', '.join(_METRIC_KEYS)}")

    if leaders.empty:
        return fail(
            "sin datos",
            detail=f"Nadie llega a {min_gp} partidos en esa competición y temporada.",
            suggestion="Baja min_gp o prueba con otra competición (get_context las lista).",
        )
    rows = records(leaders)
    _, _, metric_label = queries_assistant.LEADER_METRICS[metric]
    return ok(
        {"metric": metric_label, "leaders": rows},
        source="player_stats_by_competition / team_stats_by_competition",
        scope=f"{_competition_name(ctx, competition_id)} · temporada {season} · mínimo {min_gp} partidos",
        artifact=artifact("table", rows, title=f"Líderes · {metric_label}"),
    )


@register(
    "league_percentiles",
    family="league",
    description=(
        "En qué percentil de su liga está un equipo o un jugador en cada métrica. Es lo que "
        "permite decir 'juega rápido' con un número detrás. Para el estilo completo de un equipo, "
        "team_style ya lo incluye."
    ),
    parameters=schema(
        {
            "kind": {"type": "string", "enum": ["player", "team"]},
            "entity_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
        },
        required=["kind", "entity_id"],
    ),
    artifact="table",
    requires="league_percentiles",
)
def league_percentiles(
    ctx: ToolContext, kind: str, entity_id: str, season_id: int = None, competition_id: int = None
) -> dict:
    season = _season(ctx, season_id)
    if kind == "team":
        rows_df = queries_assistant.team_style_row(ctx.engine, entity_id, season, competition_id)
        source = "team_style_percentiles"
    else:
        rows_df = queries_assistant.player_percentile_row(ctx.engine, entity_id, season, competition_id)
        source = "player_percentiles"

    if rows_df.empty:
        return fail(
            "sin percentil",
            detail=(
                f"{entity_id} no llega al mínimo de 5 partidos en esa competición y temporada, "
                "que es el filtro de la vista de percentiles."
            ),
            suggestion="Usa team_profile o player_averages para los números crudos.",
        )
    rows = records(rows_df)
    return ok(
        rows,
        source=source,
        scope=f"temporada {season} · percentil dentro de su propia competición",
        artifact=artifact("table", rows, title="Percentiles de liga"),
    )


@register(
    "standings",
    family="league",
    description=(
        "Clasificación de una competición (victorias, derrotas, diferencia de puntos), calculada "
        "desde los partidos ya jugados. No hay tabla oficial en la base de datos: esto es un "
        "recuento propio sobre lo que hay cargado."
    ),
    parameters=schema(
        {"competition_id": {"type": "integer"}, "season_id": {"type": "integer"}},
        required=["competition_id"],
    ),
    artifact="table",
)
def standings(ctx: ToolContext, competition_id: int, season_id: int = None) -> dict:
    season = _season(ctx, season_id)
    table = queries_assistant.standings(ctx.engine, season, competition_id, ctx.today)
    if table.empty:
        return fail("sin partidos", detail="No hay partidos jugados en esa competición y temporada.")
    rows = records(table)
    return ok(
        rows,
        source="games",
        scope=f"{_competition_name(ctx, competition_id)} · temporada {season} · hasta {ctx.today.isoformat()}",
        warnings=[
            "Se cuenta solo lo que hay cargado en esta base de datos, que puede no ser la "
            "competición entera: no es la clasificación oficial."
        ],
        artifact=artifact("table", rows, title="Clasificación"),
    )


@register(
    "compare",
    family="league",
    description=(
        "Compara dos o más jugadores (o dos o más equipos) sobre las mismas métricas, en una "
        "tabla. Mismo tipo de entidad en la misma llamada."
    ),
    parameters=schema(
        {
            "kind": {"type": "string", "enum": ["player", "team"]},
            "entity_ids": {"type": "array", "items": {"type": "string"}},
            "season_id": {"type": "integer"},
        },
        required=["kind", "entity_ids"],
    ),
    artifact="table",
)
def compare(ctx: ToolContext, kind: str, entity_ids: list, season_id: int = None) -> dict:
    season = _season(ctx, season_id)
    if len(entity_ids) < 2:
        return fail(
            "hacen falta al menos dos",
            detail="compare necesita dos o más ids del mismo tipo.",
            suggestion="Para uno solo usa player_averages o team_profile.",
        )
    table = queries_assistant.compare_entities(ctx.engine, kind, entity_ids, season)
    if table.empty:
        return fail("sin datos", detail="Ninguno de esos ids existe en la base de datos.",
                    suggestion="Resuélvelos antes con resolve_entity.")
    rows = records(table)
    missing = [row["id"] for row in rows if not row.get("gp")]
    warnings = (
        [f"Sin partidos en la temporada {season}: {', '.join(missing)}. Dilo en la respuesta."]
        if missing
        else None
    )
    return ok(
        rows,
        source="player_stats_combined / team_stats_combined",
        scope=f"temporada {season}",
        warnings=warnings,
        artifact=artifact("table", rows, title="Comparativa"),
    )
