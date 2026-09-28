"""Herramientas de patrón de rotación e impacto ajustado (propuestas 12 y 13).

- `team_rotation_pattern`: la costumbre de un equipo (quinteto de salida,
  cuándo descansan sus principales, quién cierra, en qué minutos pierde).
  Mismo cálculo que la sección "Patrón de rotación" de "Próximo rival".
- `lineup_builder`: RAPM de la plantilla y los mejores quintetos posibles con
  los disponibles. Mismo cálculo que "Constructor de quintetos" en la
  pantalla de quintetos.

Las dos necesitan tramos con reloj: `requires="lineup_stints"`, igual que
`clutch_lineups` (ver la docstring de `tools/lineups.py`).
"""
from .base import ToolContext, artifact, fail, ok, records, register, schema

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import impact
    from app.analytics import rotation_patterns as rp
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import impact
    from analytics import rotation_patterns as rp
    from data import queries, queries_assistant


def _season(ctx: ToolContext, season_id) -> int:
    return ctx.season_id if season_id is None else int(season_id)


@register(
    "team_rotation_pattern",
    family="lineup",
    description=(
        "Patrón de rotación de un equipo en la temporada: quinteto inicial más probable, minutos en "
        "que descansan habitualmente sus principales (y cómo le va al equipo sin ellos), quién cierra "
        "los finales apretados y en qué tramos del partido gana o pierde. Para preparar un partido "
        "('¿cuándo sienta a su base?', '¿en qué minutos sufre?'). No es el detalle de UN partido."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "last_n_games": {"type": "integer", "description": "Solo los últimos N partidos (todos por defecto)."},
        },
        required=["team_id"],
    ),
    artifact="table",
    requires="lineup_stints",
)
def team_rotation_pattern(ctx: ToolContext, team_id: str, season_id: int = None, last_n_games: int = None) -> dict:
    season = _season(ctx, season_id)
    rows = queries.team_stint_rows(ctx.engine, team_id, season, last_n_games=last_n_games)
    if rows.empty:
        return fail(
            "sin tramos",
            detail=f"No hay tramos de quinteto de {team_id} en la temporada {season}.",
            suggestion="Prueba con la temporada anterior o usa team_lineups.",
        )
    n_games = int(rows["game_id"].nunique())
    rotation = rp.player_rotation_table(rp.minute_shares(rows))
    starters = rp.starting_lineups(rows)
    closers, close_games = rp.closing_players(rows)
    blocks = rp.block_performance(rows)
    on_off = queries_assistant.player_on_off(ctx.engine, team_id, season)
    team_name = queries.team_name(ctx.engine, team_id) or team_id
    summary = rp.rotation_insights(
        team_name, rotation, starters, closers, close_games, blocks, on_off, n_games=n_games
    )

    rotation_rows = rotation.head(10).assign(
        rest_windows=rotation.head(10)["rest_windows"].map(lambda ws: [f"{a}-{b}" for a, b in ws])
    )
    data = {
        "summary": summary,
        "games": n_games,
        "players": records(rotation_rows),
        "starting_lineups": records(starters.head(3).assign(players=starters.head(3)["players"].map(list))),
        "closers": records(closers.head(8)),
        "close_games": close_games,
        "blocks": records(blocks),
    }
    return ok(
        data,
        source="lineup_stints + lineup_stint_players",
        scope=f"temporada {season}" + (f" · últimos {last_n_games} partidos" if last_n_games else ""),
        gp=n_games,
        warnings=[
            "Los puntos por tramo del partido se reparten por tiempo dentro de cada tramo de quinteto "
            "(no hay canasta a canasta): aproximación.",
        ],
        artifact=artifact("table", records(blocks), title="Diferencia por tramo del partido"),
    )


@register(
    "lineup_builder",
    family="lineup",
    description=(
        "Impacto ajustado (RAPM: +/- por 40 descontando compañeros y rivales) de la plantilla de un "
        "equipo y los mejores quintetos posibles con los jugadores disponibles. Úsala para '¿qué "
        "quinteto saco sin X?', '¿con quién rodeo a Y?' o '¿quién aporta de verdad, más allá del "
        "On/Off?'. Los ids de jugador salen de resolve_entity."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "competition_id": {"type": "integer"},
            "unavailable": {"type": "array", "items": {"type": "string"}, "description": "player_id que no juegan."},
            "must_include": {"type": "array", "items": {"type": "string"}, "description": "player_id fijos."},
            "limit": {"type": "integer"},
            "use_prior": {
                "type": "boolean",
                "description": (
                    "Usar el RAPM de la temporada anterior como punto de partida (por defecto sí; "
                    "false = solo esta temporada, encogida hacia 0)."
                ),
            },
        },
        required=["team_id"],
    ),
    artifact="table",
    requires="lineup_stints",
)
def lineup_builder(
    ctx: ToolContext,
    team_id: str,
    season_id: int = None,
    competition_id: int = None,
    unavailable: list = None,
    must_include: list = None,
    limit: int = 5,
    use_prior: bool = True,
) -> dict:
    season = _season(ctx, season_id)
    data = queries_assistant.season_impact(ctx.engine, season, competition_id, use_prior=bool(use_prior))
    fit, segments, names = data["fit"], data["segments"], data["names"]
    team_minutes = impact.team_player_minutes(segments, team_id)
    if team_minutes.empty:
        return fail(
            "sin tramos",
            detail=f"No hay tramos con los diez jugadores en pista de {team_id} en la temporada {season}.",
        )

    unavailable = set(unavailable or [])
    candidates = [
        p for p in team_minutes.index
        if team_minutes[p] >= 100.0 and p not in unavailable
    ]
    best = impact.best_lineups(
        fit["players"], candidates, observed=impact.observed_lineups(segments, team_id),
        must_include=[p for p in (must_include or []) if p not in unavailable], top=max(1, min(int(limit), 10)),
    )
    team_rapm = fit["players"][fit["players"]["player_id"].isin(team_minutes.index)].assign(
        player_name=lambda df: df["player_id"].map(names),
        team_minutes=lambda df: df["player_id"].map(team_minutes),
    )
    lineups = best.assign(players=best["players"].map(lambda ps: [names.get(p, p) for p in ps]))
    prior_season = data.get("prior_season") if fit.get("prior_used") else None
    impact_columns = ["player_id", "player_name", "rapm", "team_minutes", "reliable"]
    if prior_season:
        impact_columns += ["rapm_no_prior", "prior"]
    payload = {
        "player_impact": records(team_rapm[impact_columns]),
        "best_lineups": records(lineups),
        "home_advantage_per_40": round(float(fit["home_advantage"]), 2),
        "prior_season": prior_season["label"] if prior_season else None,
    }
    warnings = [
        "Modelo aditivo: la proyección de un quinteto es la suma del RAPM de sus cinco, sin química. "
        "Di siempre los minutos reales que ese quinteto ha jugado junto a la proyección.",
        f"RAPM con menos de {impact.MIN_RELIABLE_MINUTES:.0f} minutos (reliable=false) no sirve para decidir.",
    ]
    if prior_season:
        warnings.append(
            f"RAPM con la temporada {prior_season['label']} como punto de partida (prior: su RAPM "
            f"×{fit['prior_weight']:.1f}; quien no jugó entonces parte de 0). rapm_no_prior = solo esta "
            "temporada; prior = el punto de partida. Con pocos minutos este año pesa más el prior: dilo."
        )
    return ok(
        payload,
        source="lineup_stints (toda la liga, regresión ridge)",
        scope=f"temporada {season}" + (f" · competición {competition_id}" if competition_id else ""),
        warnings=warnings,
        artifact=artifact("table", records(lineups), title="Mejores quintetos disponibles"),
    )
