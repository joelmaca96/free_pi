"""Herramienta del planificador de minutos con carga (propuesta 17).

`minutes_plan` responde "¿cómo reparto los minutos el domingo sin Kotsar y con
Howard cargado?" con el mismo cálculo que la sección "Planificador de
minutos" de la pantalla de quintetos (`app/analytics/minutes_plan.py`): RAPM
del ajuste de la temporada, topes sugeridos por la carga de los días previos
y reparto óptimo de los 200 minutos.

Necesita tramos con reloj (el RAPM sale de ellos): `requires="lineup_stints"`,
igual que `lineup_builder` (`tools/rotations.py`).
"""
import datetime as dt

from .base import ToolContext, artifact, fail, ok, records, register, schema

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import impact
    from app.analytics import minutes_plan as mp
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import impact
    from analytics import minutes_plan as mp
    from data import queries, queries_assistant


def _parse_date(value):
    if value in (None, ""):
        return None
    return dt.date.fromisoformat(str(value)[:10])


def _minutes_map(value) -> dict:
    """`{player_id: minutos}` saneado (el modelo a veces manda cadenas)."""
    out = {}
    for key, minutes in (value or {}).items():
        try:
            out[str(key)] = float(minutes)
        except (TypeError, ValueError):
            continue
    return out


@register(
    "minutes_plan",
    family="lineup",
    description=(
        "Plan de minutos para el PRÓXIMO partido: reparte los 200 minutos (5×40) maximizando el margen "
        "proyectado por RAPM, con topes por jugador sugeridos por su carga (minutos en los 7 días previos, "
        "descanso corto), no disponibles a 0, mínimos opcionales y cobertura de base y pívot. Dice qué "
        "limita a cada uno y lo compara con su media reciente ('Howard: 31 → 26 min, tope por carga'). "
        "Para '¿cuántos minutos le doy a X?', '¿cómo reparto sin Y?'. Para qué quinteto sacar, "
        "lineup_builder. Ids de jugador de resolve_entity."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string", "description": "Por defecto, el equipo propio."},
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
            "game_date": {"type": "string", "description": "YYYY-MM-DD del partido; por defecto el próximo."},
            "unavailable": {"type": "array", "items": {"type": "string"}, "description": "player_id que no juegan."},
            "max_minutes": {
                "type": "object", "additionalProperties": {"type": "number"},
                "description": "{player_id: tope} fijado por el entrenador.",
            },
            "min_minutes": {
                "type": "object", "additionalProperties": {"type": "number"}, "description": "{player_id: mínimo}.",
            },
            "general_cap": {"type": "number", "description": "Tope de quien no tiene carga (32)."},
            "prudent": {"type": "boolean", "description": "Penalizar RAPM con poca muestra."},
        },
        required=[],
    ),
    artifact="table",
    requires="lineup_stints",
)
def minutes_plan(
    ctx: ToolContext,
    team_id: str = None,
    season_id: int = None,
    competition_id: int = None,
    game_date: str = None,
    unavailable: list = None,
    max_minutes: dict = None,
    min_minutes: dict = None,
    general_cap: float = None,
    prudent: bool = False,
) -> dict:
    team = team_id or ctx.own_team_id
    season = ctx.season_id if season_id is None else int(season_id)
    data = queries_assistant.season_impact(ctx.engine, season, competition_id)
    fit, segments, names = data["fit"], data["segments"], data["names"]
    team_minutes = impact.team_player_minutes(segments, team)
    roster = queries.roster_cards(ctx.engine, team, season)
    if team_minutes.empty:
        return fail(
            "sin tramos",
            detail=f"No hay tramos con los diez jugadores en pista de {team} en la temporada {season}.",
        )

    try:
        requested_date = _parse_date(game_date)
    except ValueError:
        return fail("fecha inválida", detail=f"'{game_date}' no es una fecha YYYY-MM-DD.")
    game_log = queries.rolling_load(ctx.engine, team, season, 7)
    last_played = game_log["game_date"].max().date() if not game_log.empty else None
    next_scheduled = None
    if team == ctx.own_team_id:
        upcoming = queries.next_matchup(ctx.engine, ctx.today)
        next_scheduled = _parse_date(upcoming["match_date"]) if upcoming else None
    reference_date = requested_date or mp.default_reference_date(last_played, next_scheduled)

    rules = mp.LoadRules(default_max=float(general_cap)) if general_cap else mp.LoadRules()
    summary = mp.recent_minutes_summary(game_log, reference_date, days=rules.window_days)
    rest = mp.rest_days_before(game_log, reference_date)
    caps = mp.load_caps(summary, rest_days=rest, rules=rules)
    players = mp.build_plan_input(fit["players"], team_minutes, roster, summary, caps, names, default_max=rules.default_max)

    out = set(unavailable or [])
    players.loc[players["player_id"].isin(out), "available"] = False
    for pid, cap in _minutes_map(max_minutes).items():
        mask = players["player_id"] == pid
        players.loc[mask, ["max_minutes", "cap_source", "cap_detail"]] = [cap, "entrenador", ""]
        players.loc[mask & ~players["player_id"].isin(out), "available"] = True
    for pid, floor in _minutes_map(min_minutes).items():
        mask = players["player_id"] == pid
        players.loc[mask, "min_minutes"] = floor
        players.loc[mask & ~players["player_id"].isin(out), "available"] = True

    warnings = [
        "Modelo aditivo (RAPM × minutos / 40, sin química): reparte TOTALES del partido, no la rotación.",
        "Los topes por carga son una regla de calendario sobre minutos de partido, no datos médicos.",
    ]
    available = players[players["available"]]
    floors = None
    if not available.empty and all(isinstance(p, str) and p.strip() for p in available["position"]):
        floors = {
            label: minutes for label, minutes in mp.DEFAULT_POSITION_FLOORS.items()
            if (available["position"].str.strip().str.lower() == label.lower()).any()
        }
        missing = sorted(set(mp.DEFAULT_POSITION_FLOORS) - set(floors))
        if missing:
            warnings.append(f"Sin ningún disponible de posición {', '.join(missing)}: esa cobertura no se exige.")
    else:
        warnings.append("Posiciones desconocidas para algún disponible: sin cobertura de base y pívot.")

    players, relax_notes = mp.relax_caps(players, position_floors=floors)
    if relax_notes:
        warnings.append("Con los topes por carga no había reparto posible: se han subido " + "; ".join(relax_notes) + ".")

    value_column = "prudent" if prudent else "rapm"
    result = mp.plan_minutes(players, value_column=value_column, position_floors=floors)
    if not result["feasible"]:
        return fail("sin reparto posible", detail=result["message"], suggestion="Sube topes, quita mínimos o añade disponibles.")

    table = result["table"]
    shown = table[table["available"] | (table["recent_avg_minutes"].fillna(0) > 0)]
    plan_rows = shown[[
        "player_id", "player_name", "position", "rapm", "planned_minutes", "recent_avg_minutes", "delta",
        "max_minutes", "binding", "detail", "explanation",
    ]]
    payload = {
        "game_date": reference_date.isoformat(),
        "rest_days": rest,
        "criterion": "prudente" if prudent else "esperanza",
        "projected_margin": round(result["projected_margin"], 2),
        "recent_distribution_margin": round(mp.recent_distribution_margin(players), 2)
        if players["recent_avg_minutes"].notna().any() else None,
        "position_floors": floors or {},
        "plan": records(plan_rows),
    }
    if not requested_date and reference_date != next_scheduled:
        warnings.append(
            f"Sin próximo partido cercano en el calendario: se planifica uno hipotético el {reference_date.isoformat()}."
        )
    return ok(
        payload,
        source="lineup_stints (RAPM de la liga) + player_game_stats (carga)",
        scope=f"partido del {reference_date.isoformat()} · temporada {season}"
        + (f" · competición {competition_id}" if competition_id else ""),
        warnings=warnings,
        artifact=artifact(
            "table",
            records(plan_rows[["player_name", "planned_minutes", "recent_avg_minutes", "binding", "detail"]]),
            title="Plan de minutos",
        ),
    )
