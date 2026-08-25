"""Herramientas de quintetos (§4.4). Aquí vive el límite real del modelo de datos.

`team_lineups` ordena quintetos por rendimiento en el global de la temporada:
eso se ha podido contestar siempre. `clutch_lineups` —"¿cuál es el mejor
quinteto para los últimos minutos?", la tercera pregunta del encargo— no, y
por un motivo concreto: `lineups` agrega el partido entero por combinación de
cinco, así que los tramos se funden al persistir y no queda ni reloj ni
marcador con el que recortar (§2.3).

La fase 3 arregló eso aguas arriba (`lineup_stints`, ver `schema.sql`), pero
una base de datos que no se haya reingerido todavía sigue sin tramos. De ahí
`requires="lineup_stints"`: la herramienta **no se registra** si no hay dato.
Una herramienta que existe y siempre falla es peor que su ausencia — sin
ella, el modelo ve que no puede, lo dice y ofrece la pregunta adyacente que
sí sabe responder; con ella, insiste y acaba inventando.
"""
from .base import ToolContext, artifact, fail, ok, records, register, schema

try:  # pragma: no cover - ver nota en tools/context.py
    from app.data import queries_assistant
except ImportError:  # pragma: no cover
    from data import queries_assistant

_INFERRED_TEAM_WARNING = (
    "El equipo de estos quintetos se ha deducido del equipo ACTUAL de sus jugadores "
    "(la ingesta todavía no guarda el equipo del quinteto): un traspaso ensucia el histórico."
)


def _season(ctx: ToolContext, season_id) -> int:
    return ctx.season_id if season_id is None else int(season_id)


@register(
    "team_lineups",
    family="lineup",
    description=(
        "Mejores quintetos de un equipo en una temporada, por plus/minus por 40 minutos o por "
        "minutos jugados. Es el GLOBAL del partido: no puede acotarse a los últimos minutos ni "
        "al marcador apretado (para eso, clutch_lineups, si está disponible)."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
            "min_minutes": {
                "type": "number",
                "description": "Minutos mínimos juntos (10 por defecto). Sin mínimo, el 'mejor' es ruido.",
            },
            "order_by": {"type": "string", "enum": ["plus_minus", "minutes"]},
            "limit": {"type": "integer"},
        },
        required=["team_id"],
    ),
    artifact="table",
)
def team_lineups(
    ctx: ToolContext,
    team_id: str,
    season_id: int = None,
    competition_id: int = None,
    min_minutes: float = 10.0,
    order_by: str = "plus_minus",
    limit: int = 10,
) -> dict:
    """Quintetos agregados por combinación real de cinco, con mínimo de minutos."""
    season = _season(ctx, season_id)
    lineups = queries_assistant.team_lineups(
        ctx.engine, team_id, season, competition_id, min_minutes, limit, order_by
    )
    if lineups.empty:
        return fail(
            "sin quintetos",
            detail=(
                f"No hay quintetos de {team_id} con al menos {min_minutes:g} minutos juntos "
                f"en la temporada {season}."
            ),
            suggestion="Baja min_minutes, o quita el filtro de competición.",
        )
    rows = records(lineups)
    warnings = [_INFERRED_TEAM_WARNING] if lineups["is_inferred"].max() else None
    if not ctx.capabilities.lineup_stints:
        warnings = (warnings or []) + [
            "Estos son totales de partido completo: no se pueden aislar los últimos minutos "
            "ni el juego con el partido apretado."
        ]
    return ok(
        rows,
        source="lineups + lineup_players (vista lineup_team)",
        scope=f"temporada {season} · mínimo {min_minutes:g} min juntos",
        warnings=warnings,
        artifact=artifact("table", rows, title="Quintetos"),
    )


@register(
    "lineup_detail",
    family="lineup",
    description="Rendimiento de UNA combinación concreta de cinco jugadores en la temporada.",
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "player_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Los cinco ids de jugador (resuélvelos antes con resolve_entity).",
            },
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
        },
        required=["team_id", "player_ids"],
    ),
)
def lineup_detail(
    ctx: ToolContext,
    team_id: str,
    player_ids: list,
    season_id: int = None,
    competition_id: int = None,
) -> dict:
    """Busca esa combinación exacta entre los quintetos del equipo."""
    season = _season(ctx, season_id)
    wanted = ",".join(sorted(player_ids))
    # `min_minutes=0` y `limit` alto: aquí no se está buscando "el mejor", se
    # está buscando UNO concreto, y filtrarlo por minutos lo escondería.
    lineups = queries_assistant.team_lineups(
        ctx.engine, team_id, season, competition_id, 0.0, 500, "minutes"
    )
    match = lineups[lineups["player_ids"] == wanted] if not lineups.empty else lineups
    if match.empty:
        return fail(
            "quinteto no encontrado",
            detail=f"Esos cinco jugadores no han coincidido en pista en la temporada {season}.",
            suggestion="Usa team_lineups para ver qué combinaciones sí han jugado juntas.",
        )
    return ok(
        records(match)[0],
        source="lineups + lineup_players",
        scope=f"temporada {season}",
        warnings=[_INFERRED_TEAM_WARNING] if match["is_inferred"].max() else None,
    )


@register(
    "player_pairs",
    family="lineup",
    description=(
        "Cómo rinde el equipo con dos jugadores JUNTOS en pista frente a cada uno sin el otro. "
        "Para un quinteto entero usa lineup_detail."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "player_a": {"type": "string"},
            "player_b": {"type": "string"},
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
        },
        required=["team_id", "player_a", "player_b"],
    ),
    artifact="table",
)
def player_pairs(
    ctx: ToolContext,
    team_id: str,
    player_a: str,
    player_b: str,
    season_id: int = None,
    competition_id: int = None,
) -> dict:
    season = _season(ctx, season_id)
    impact = queries_assistant.player_pair_impact(
        ctx.engine, team_id, player_a, player_b, season, competition_id
    )
    if impact.empty:
        return fail("sin quintetos", detail=f"No hay quintetos de {team_id} en la temporada {season}.")
    rows = records(impact)
    return ok(
        rows,
        source="lineups + lineup_players",
        scope=f"temporada {season} · A={player_a}, B={player_b}",
        artifact=artifact("table", rows, title="Impacto de la pareja"),
    )


@register(
    "clutch_lineups",
    family="lineup",
    description=(
        "Mejores quintetos EN LOS MINUTOS FINALES con el partido apretado: filtra por ventana de "
        "tiempo y por diferencia en el marcador al entrar en pista. Para el global de la "
        "temporada usa team_lineups."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "last_minutes": {"type": "number", "description": "Cuánto dura 'el final' (5 por defecto)."},
            "max_margin": {
                "type": "integer",
                "description": "Diferencia máxima en el marcador al abrir el tramo (5 por defecto).",
            },
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        required=["team_id"],
    ),
    artifact="table",
    requires="lineup_stints",
)
def clutch_lineups(
    ctx: ToolContext,
    team_id: str,
    last_minutes: float = 5.0,
    max_margin: int = 5,
    season_id: int = None,
    competition_id: int = None,
    limit: int = 10,
) -> dict:
    """La pregunta 2.3 del encargo, contestada de verdad — cuando hay tramos."""
    season = _season(ctx, season_id)
    lineups = queries_assistant.clutch_lineups(
        ctx.engine,
        team_id,
        season,
        last_seconds=int(last_minutes * 60),
        max_margin=max_margin,
        limit=limit,
        competition_id=competition_id,
    )
    if lineups.empty:
        return fail(
            "sin tramos que cumplan el filtro",
            detail=(
                f"No hay quintetos de {team_id} con minutos suficientes en los últimos "
                f"{last_minutes:g} minutos y con menos de {max_margin} puntos de diferencia."
            ),
            suggestion="Amplía la ventana (last_minutes) o el margen (max_margin), o usa team_lineups.",
        )
    rows = records(lineups)
    return ok(
        rows,
        source="lineup_stints + lineup_stint_players",
        scope=f"temporada {season} · últimos {last_minutes:g} min · margen ±{max_margin}",
        artifact=artifact("table", rows, title="Quintetos en el tramo final"),
    )
