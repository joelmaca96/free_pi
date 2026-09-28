"""Herramienta del plan de rotación contra un rival (propuesta 15).

`rotation_plan_vs_rival` cruza las ventanas en que el rival flojea
(descansos de sus principales, peores tramos de reloj) con nuestros mejores
quintetos disponibles, proyectados contra los cinco que el rival suele tener
en pista en esos minutos. Mismo cálculo que la sección "Plan de rotación
contra el rival" de "Próximo rival" (`app/analytics/rotation_plan.py`).

Necesita tramos con reloj: `requires="lineup_stints"`, igual que las demás
herramientas de quintetos (ver `tools/rotations.py`).
"""
from typing import Optional

from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import rotation_plan as rpl
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import rotation_plan as rpl
    from data import queries, queries_assistant


def _is_home_from_calendar(ctx: ToolContext, rival_team_id: str) -> Optional[bool]:
    """Si el rival es el del próximo partido del calendario, si jugamos en casa; si no, `None`."""
    matchup = queries.next_matchup(ctx.engine, ctx.today)
    if matchup and matchup.get("opponent_team_id") == rival_team_id:
        return bool(matchup["is_home"])
    return None


@register(
    "rotation_plan_vs_rival",
    family="lineup",
    description=(
        "Plan de rotación propio contra un rival: en qué minutos flojea (cuando descansan sus "
        "principales, sus peores tramos del partido), qué cinco suele tener en pista entonces y cuáles "
        "son NUESTROS mejores quintetos disponibles para esos minutos, con la diferencia proyectada "
        "contra esos cinco (RAPM) y cuánto mejora lo que solemos tener en pista ahí. Para '¿qué "
        "sacamos cuando sientan a su base?' o 'plan de rotación contra X'. team_id = el RIVAL."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string", "description": "Equipo RIVAL."},
            "season_id": {"type": "integer"},
            "unavailable": {
                "type": "array", "items": {"type": "string"},
                "description": "player_id propios que no juegan (lesionados, no convocados).",
            },
            "is_home": {
                "type": "boolean",
                "description": "Si jugamos en casa. Por defecto, lo que diga el calendario si es el próximo rival.",
            },
            "limit": {"type": "integer", "description": "Quintetos por ventana (3 por defecto, máx. 5)."},
        },
        required=["team_id"],
    ),
    artifact="table",
    requires="lineup_stints",
)
def rotation_plan_vs_rival(
    ctx: ToolContext,
    team_id: str,
    season_id: int = None,
    unavailable: list = None,
    is_home: bool = None,
    limit: int = 3,
) -> dict:
    if season_id is None:
        season, season_warning = season_with_fallback(queries.team_scouting_season, ctx.engine, team_id, ctx.season_id)
    else:
        season, season_warning = int(season_id), None
    own = ctx.own_team_id
    if team_id == own:
        return fail("mismo equipo", detail="team_id tiene que ser el rival, no el equipo propio.")

    rival_rows = queries.team_stint_rows(ctx.engine, team_id, season)
    if rival_rows.empty:
        return fail(
            "sin tramos",
            detail=f"No hay tramos de quinteto de {team_id} en la temporada {season}.",
            suggestion="Prueba con la temporada anterior o usa team_rotation_pattern.",
        )
    data = queries_assistant.season_impact(ctx.engine, season, None)
    fit, segments, names = data["fit"], data["segments"], data["names"]
    own_candidates = rpl.default_candidates(segments, own)
    if not own_candidates or fit["players"].empty:
        # Sin tramos propios en esa temporada no hay quintetos que proponer: el
        # "plan" serían solo las ventanas del rival (eso ya es team_rotation_pattern).
        return fail(
            "sin tramos propios",
            detail=f"No hay tramos con los diez jugadores en pista de {own} en la temporada {season}: "
            "no se pueden proyectar nuestros quintetos contra los del rival.",
            suggestion="Usa team_rotation_pattern para ver solo la rotación del rival.",
        )
    unavailable = set(unavailable or [])
    candidates = [p for p in own_candidates if p not in unavailable]
    if is_home is None:
        is_home = _is_home_from_calendar(ctx, team_id)

    plan = rpl.build_plan(
        rival_rows,
        queries.team_stint_rows(ctx.engine, own, season),
        fit,
        segments,
        own,
        candidates,
        rival_on_off=queries_assistant.player_on_off(ctx.engine, team_id, season),
        is_home=is_home,
        top=max(1, min(int(limit), 5)),
    )
    if not plan:
        return fail(
            "sin ventanas",
            detail=f"{team_id} no tiene descansos fijos ni tramos del partido claramente malos en la temporada {season}.",
            suggestion="Usa team_rotation_pattern para ver su rotación completa.",
        )
    rival_name = queries.team_name(ctx.engine, team_id) or team_id
    to_names = lambda ps: [names.get(p, p) for p in ps]  # noqa: E731

    windows, table = [], []
    for window in plan:
        lineups = window["lineups"].assign(players=window["lineups"]["players"].map(to_names))
        windows.append({
            **clean_dict({
                key: window[key]
                for key in (
                    "kind", "label", "player_name", "rival_minutes", "rival_per_40", "off_per_40",
                    "rival_five_share", "rival_five_rapm", "own_usual_margin",
                )
            }),
            "rival_five": to_names(window["rival_five"]),
            "own_usual": to_names(window["own_usual"]),
            "lineups": records(lineups),
        })
        table += [{"ventana": f"Min {window['label']}", **row} for row in records(lineups)]

    warnings = [
        "Modelo aditivo (RAPM): el orden de nuestros quintetos es el mismo en todas las ventanas; lo que "
        "cambia es el margen esperado y la mejora sobre lo habitual (gain_vs_usual). Di siempre los "
        "minutos reales (observed_minutes) junto a la proyección.",
        "Proyección por 40 minutos contra los cinco más habituales del rival en esa ventana, no contra "
        "un quinteto seguro: rival_five_share dice cuánto de fijo es ese quinteto.",
    ]
    if is_home is None:
        warnings.append("Sin saber si jugamos en casa: la proyección no incluye ventaja de campo.")
    if plan[0]["lineups"].empty:
        warnings.append(
            f"Menos de cinco disponibles ({len(candidates)}): no hay quintetos que proponer, solo las "
            "ventanas del rival. Dilo en vez de inventar un quinteto."
        )
    prior_season = data.get("prior_season") if fit.get("prior_used") else None
    if prior_season:
        warnings.append(
            f"RAPM con la temporada {prior_season['label']} como punto de partida (prior), igual que el "
            "constructor de quintetos por defecto."
        )
    if season_warning:
        warnings.insert(0, season_warning)
    return ok(
        {
            "summary": rpl.plan_insights(rival_name, plan, names),
            "is_home": is_home,
            "home_advantage_per_40": round(rpl.home_term(fit["home_advantage"], is_home), 2),
            "windows": windows,
        },
        source="lineup_stints (toda la liga, regresión ridge) + patrón de rotación del rival",
        scope=f"temporada {season}",
        gp=int(rival_rows["game_id"].nunique()),
        warnings=warnings,
        artifact=artifact("table", table, title=f"Plan de rotación contra {rival_name}"),
    )
