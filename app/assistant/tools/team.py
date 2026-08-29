"""Herramientas de equipo (§4.3).

`team_style` es la herramienta de la segunda pregunta del encargo ("el
Fenerbahçe, ¿qué estilo de juego tiene?"). El mecanismo importante está en
`_label`: la clasificación (alto/medio/bajo) la calcula **Python con umbrales
fijos sobre el percentil de la liga**, no el modelo a ojo. El modelo redacta
a partir de eso; no inventa la etiqueta, la explica (§6.2).

Es la diferencia entre "juega rápido" —una impresión— y "juega rápido (73.6
posesiones, p71 de la Euroliga)" —una afirmación comprobable de un vistazo.
"""
from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from data import queries, queries_assistant

# Umbrales de etiquetado, fijos y en un solo sitio (§6.2). Se eligen redondos
# a propósito: el valor exacto importa menos que el hecho de que sea siempre
# el mismo y esté escrito, en vez de reinterpretado en cada respuesta.
_HIGH_PERCENTILE = 0.70
_LOW_PERCENTILE = 0.30

# Aviso permanente al mezclar competiciones (§2.4, §7.1). No es una nota al
# pie: ACB da ratings oficiales y en Euroliga son una estimación propia, así
# que compararlos sin decirlo es presentar dos cosas distintas como la misma.
_EUROLEAGUE_WARNING = (
    "En Euroliga, ORtg/DRtg/pace son estimaciones propias (fórmula Dean Oliver), no dato "
    "oficial como en ACB: adviértelo si comparas las dos competiciones."
)


def _season(ctx: ToolContext, season_id) -> int:
    return ctx.season_id if season_id is None else int(season_id)


def _team_season(ctx: ToolContext, team_id: str, season_id):
    """Como `_season`, cayendo a la última temporada con partidos de `team_id` si hace falta.

    Mismo criterio que ya usaba la pantalla "Próximo rival"
    (`queries.team_scouting_season`) para un rival sin partidos todavía en la
    temporada en curso — aquí se aplica igual a cualquier equipo que pidan
    las herramientas del asistente, no solo al próximo rival. Devuelve
    `(season, warning)`, ver `season_with_fallback`.
    """
    preferred = _season(ctx, season_id)
    return season_with_fallback(queries.team_scouting_season, ctx.engine, team_id, preferred)


def _label(percentile) -> str:
    """Percentil (0-1) -> etiqueta. Umbrales fijos, nunca criterio del modelo."""
    if percentile is None:
        return "sin contexto"
    if percentile >= _HIGH_PERCENTILE:
        return "alto"
    if percentile <= _LOW_PERCENTILE:
        return "bajo"
    return "medio"


def _metric(value, percentile, *, league_teams=None) -> dict:
    """Una métrica con su percentil, su etiqueta y cuántos equipos la contextualizan.

    `p71` sin saber sobre cuántos equipos se calcula es un número sin escala:
    el percentil de una liga de 18 equipos y el de una de 4 no significan lo
    mismo, y el modelo tiene que poder decirlo.
    """
    if value is None:
        return {"value": None}
    entry = {"value": round(float(value), 1), "label": _label(percentile)}
    if percentile is not None:
        entry["percentile"] = int(round(float(percentile) * 100))
        if league_teams:
            entry["league_teams"] = int(league_teams)
    return entry


@register(
    "team_profile",
    family="team",
    description=(
        "Récord y perfil avanzado de un equipo (pace, ORtg, DRtg, net rating, eFG%, TS%) por "
        "competición, más tiros libres y boxscore ampliado (robos/tapones/pérdidas/faltas, propios "
        "y concedidos) si están disponibles. Para leerlo COMO ESTILO con contexto de liga usa team_style."
    ),
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="table",
)
def team_profile(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    """Récord + medias avanzadas, tal cual las devuelve `queries.py`."""
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    record = queries.team_record(ctx.engine, team_id, season, ctx.today)
    profile = queries.team_advanced_profile(ctx.engine, team_id, season)
    if profile.empty and record.empty:
        return fail(
            "sin datos",
            detail=f"El equipo {team_id} no tiene partidos en la temporada {season}.",
            suggestion="Prueba con la temporada anterior: un rival de Euroliga puede no haber jugado todavía.",
        )
    rows = records(profile)
    data = {"record": records(record), "profile": rows}
    warnings = [_EUROLEAGUE_WARNING]
    if fallback_warning:
        warnings.append(fallback_warning)

    if ctx.capabilities.free_throws:
        # Lanzados Y concedidos: cuántos libres regala una defensa dice más de
        # ella que cuántos lanza su ataque, y es una lectura que sin las
        # columnas `opp_*` de la vista no se podía hacer.
        data["free_throws"] = records(queries_assistant.team_free_throws(ctx.engine, team_id, season))
    else:
        warnings.append("Esta base de datos no tiene tiros libres: no hables del juego desde la línea.")

    if ctx.capabilities.box_extras:
        # Mismo criterio que `free_throws`: propio Y concedido (`opp_*`).
        data["box_extras"] = records(queries_assistant.team_box_extras(ctx.engine, team_id, season))
    else:
        warnings.append(
            "Esta base de datos no tiene boxscore ampliado de equipo: no hables de robos, tapones, "
            "pérdidas o faltas (propias ni concedidas)."
        )

    return ok(
        data,
        source="games + team_stats_by_competition",
        scope=f"temporada {season}",
        gp=int(profile["gp"].iloc[0]) if not profile.empty else None,
        warnings=warnings,
        artifact=artifact("table", rows, title="Perfil avanzado"),
    )


@register(
    "team_style",
    family="team",
    description=(
        "Estilo de juego de un equipo: perfil avanzado CON percentil dentro de su liga, reparto "
        "de tiro por zona y patrón por cuartos, todo en una llamada. Es la herramienta para "
        "'¿cómo juega X?'. Cada métrica trae una etiqueta (alto/medio/bajo) ya calculada: úsala, "
        "no la deduzcas."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "competition_id": {
                "type": "integer",
                "description": "Acota a una competición. El percentil SIEMPRE es dentro de una sola liga.",
            },
        },
        required=["team_id"],
    ),
    artifact="table",
)
def team_style(ctx: ToolContext, team_id: str, season_id: int = None, competition_id: int = None) -> dict:
    """La pregunta 2.2 del encargo. Degrada a números crudos si no hay percentiles.

    Que la base de datos no tenga todavía la vista de percentiles (porque no
    se ha reingerido desde que se añadió) NO es un error: se responde igual
    con los números crudos y con un aviso de que van sin contexto de liga.
    Prometer un percentil que no existe sería peor que no darlo.
    """
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    warnings = [_EUROLEAGUE_WARNING]
    if fallback_warning:
        warnings.append(fallback_warning)

    styled = []
    if ctx.capabilities.league_percentiles:
        percentiles = queries_assistant.team_style_row(ctx.engine, team_id, season, competition_id)
        for row in percentiles.to_dict("records"):
            league_teams = row.get("league_teams")
            styled.append(
                {
                    "competition": row["competition"],
                    "gp": int(row["gp"]),
                    "pace": _metric(row["pace"], row["pace_pct"], league_teams=league_teams),
                    "ortg": _metric(row["ortg"], row["ortg_pct"], league_teams=league_teams),
                    # `drtg_pct` viene ya invertido en la vista: percentil alto
                    # = defensa buena. Sin esa inversión, "p90 en DRtg" se
                    # leería como un elogio siendo lo contrario.
                    "drtg": _metric(row["drtg"], row["drtg_pct"], league_teams=league_teams),
                    "net_rating": _metric(row["net_rating"], row["net_rating_pct"], league_teams=league_teams),
                    "efg_pct": _metric(row["efg_pct"], row["efg_pct_pct"], league_teams=league_teams),
                    "ts_pct": _metric(row["ts_pct"], row["ts_pct_pct"], league_teams=league_teams),
                }
            )
        if not styled:
            warnings.append(
                "Sin percentil de liga para este equipo: le faltan partidos en esa competición "
                "(hacen falta 5) o no hay rivales suficientes cargados."
            )
    else:
        warnings.append(
            "Esta base de datos no tiene las vistas de percentiles: los números van SIN contexto "
            "de liga, así que no los adjetives ('rápido', 'eficiente') — dilos y ya."
        )

    profile = queries.team_advanced_profile(ctx.engine, team_id, season)
    if profile.empty:
        return fail(
            "sin datos",
            detail=f"El equipo {team_id} no tiene estadísticas avanzadas en la temporada {season}.",
            suggestion="Prueba con la temporada anterior o comprueba el id con resolve_entity.",
        )

    zones = queries.team_zone_profile(ctx.engine, team_id, season)
    quarters = queries.team_quarter_profile(ctx.engine, team_id, season)
    zone_rows = records(zones)
    total_volume = sum(row["volume"] for row in zone_rows) or 1
    for row in zone_rows:
        row["share"] = round(100.0 * row["volume"] / total_volume, 1)

    return ok(
        {
            "style": styled,
            "profile": records(profile),
            "zones": zone_rows,
            "quarters": records(quarters),
        },
        source="team_style_percentiles + game_zone_stats + game_team_quarter_stats",
        scope=f"temporada {season}",
        gp=int(profile["gp"].iloc[0]),
        warnings=warnings,
        artifact=artifact("bar", zone_rows, title="Reparto de tiro por zona"),
    )


@register(
    "team_quarter_profile",
    family="team",
    description="Puntos medios a favor y en contra por cuarto de un equipo (¿arranca fuerte? ¿se hunde en el tercero?).",
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="bar",
)
def team_quarter_profile(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    quarters = queries.team_quarter_profile(ctx.engine, team_id, season)
    if quarters.empty:
        return fail(
            "sin datos",
            detail=f"No hay parciales por cuarto de {team_id} en la temporada {season}.",
            suggestion="Los parciales necesitan play-by-play; no todos los partidos de Euroliga lo traen.",
        )
    rows = records(quarters)
    return ok(
        rows,
        source="game_team_quarter_stats",
        scope=f"temporada {season}",
        gp=int(quarters["gp"].max()),
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("bar", rows, title="Perfil por cuartos"),
    )


@register(
    "team_foul_quarter_profile",
    family="team",
    description=(
        "Faltas medias cometidas y recibidas por cuarto de un equipo — la pregunta de en qué "
        "momento del partido se mete en problemas de faltas (bonus temprano, titular que sale). "
        "Necesita play-by-play tipado (Fase 2): puede tener menos partidos que team_quarter_profile."
    ),
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="bar",
    requires="play_events",
)
def team_foul_quarter_profile(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    quarters = queries.team_foul_quarter_profile(ctx.engine, team_id, season)
    if quarters.empty:
        return fail(
            "sin datos",
            detail=f"No hay faltas por cuarto derivadas de {team_id} en la temporada {season}.",
            suggestion="Hace falta play-by-play tipado (Fase 2); no todos los partidos lo tienen todavía.",
        )
    rows = records(quarters)
    return ok(
        rows,
        source="game_team_quarter_stats (derivado de play_events)",
        scope=f"temporada {season}",
        gp=int(quarters["gp"].max()),
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("bar", rows, title="Faltas por cuarto"),
    )


@register(
    "team_zone_profile",
    family="team",
    description="Acierto y volumen de tiro por zona de cancha de un equipo, de más a menos volumen.",
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="bar",
)
def team_zone_profile(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    zones = queries.team_zone_profile(ctx.engine, team_id, season)
    if zones.empty:
        return fail("sin datos", detail=f"No hay tiros por zona de {team_id} en la temporada {season}.")
    rows = records(zones)
    return ok(
        rows,
        source="game_zone_stats + court_zones",
        scope=f"temporada {season}",
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("bar", rows, title="Tiro por zona"),
    )


@register(
    "team_shot_chart",
    family="team",
    description="Todos los tiros de un equipo en la temporada, para pintar el mapa. Para porcentajes por zona usa team_zone_profile.",
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="shot_chart",
)
def team_shot_chart(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    shots = queries.team_shots_season(ctx.engine, team_id, season)
    if shots.empty:
        return fail("sin tiros", detail=f"No hay tiros con coordenadas de {team_id} en la temporada {season}.")
    warnings = [
        "Cada tiro se atribuye al equipo ACTUAL del jugador: un traspaso a mitad de temporada "
        "arrastra sus tiros anteriores al equipo nuevo."
    ]
    if fallback_warning:
        warnings.append(fallback_warning)
    return ok(
        {"attempts": int(len(shots)), "made": int(shots["made"].sum())},
        source="shots",
        scope=f"temporada {season}",
        warnings=warnings,
        artifact=artifact("shot_chart", records(shots, limit=2000), title="Mapa de tiros"),
    )


@register(
    "head_to_head",
    family="team",
    description=(
        "Historial de enfrentamientos directos entre dos equipos, más recientes primero. "
        "Sin filtro de temporada ni competición a propósito: para preparar un partido, un cruce "
        "de hace dos años informa igual."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string", "description": "Equipo desde cuya perspectiva se leen los marcadores."},
            "opponent_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        required=["team_id", "opponent_id"],
    ),
    artifact="table",
)
def head_to_head(ctx: ToolContext, team_id: str, opponent_id: str, limit: int = 12) -> dict:
    h2h = queries.head_to_head(ctx.engine, team_id, opponent_id, limit)
    if h2h.empty:
        return fail(
            "sin cruces",
            detail=f"{team_id} y {opponent_id} no se han enfrentado en los datos cargados.",
            suggestion="Compara sus perfiles por separado con compare o team_style.",
        )
    rows = records(h2h)
    wins = int((h2h["pts_favor"] > h2h["pts_contra"]).sum())
    return ok(
        {"record": {"wins": wins, "losses": len(h2h) - wins}, "games": rows},
        source="games",
        scope="todas las temporadas y competiciones cargadas",
        gp=len(h2h),
        artifact=artifact("table", rows, title="Cara a cara"),
    )


@register(
    "team_roster",
    family="team",
    description=(
        "Plantilla de un equipo ordenada por minutos (quién juega y cuánto produce). "
        "Aproximada en temporadas pasadas: se guarda el equipo ACTUAL de cada jugador."
    ),
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="table",
)
def team_roster(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    roster = queries_assistant.team_roster_production(ctx.engine, team_id, season)
    if roster.empty:
        return fail("sin plantilla", detail=f"No hay jugadores registrados en {team_id}.")
    rows = records(roster)
    warnings = [
        "`players.team_id` es el equipo ACTUAL del jugador, no el que tenía esa temporada: "
        "en temporadas pasadas la plantilla es aproximada."
    ]
    if fallback_warning:
        warnings.append(fallback_warning)
    return ok(
        rows,
        source="players + player_stats_combined",
        scope=f"temporada {season}",
        warnings=warnings,
        artifact=artifact("table", rows, title="Plantilla"),
    )


@register(
    "game_boxscore",
    family="team",
    description=(
        "Boxscore completo de un partido (opcionalmente solo de un equipo), con parciales por "
        "cuarto y, si esta base de datos los tiene, árbitros/asistencia/pabellón/entrenadores. "
        "Necesita game_id: sácalo antes con resolve_game."
    ),
    parameters=schema(
        {"game_id": {"type": "string"}, "team_id": {"type": "string"}}, required=["game_id"]
    ),
    artifact="table",
)
def game_boxscore(ctx: ToolContext, game_id: str, team_id: str = None) -> dict:
    header = queries_assistant.game_header(ctx.engine, game_id)
    if header is None:
        return fail("partido desconocido", detail=f"No existe el partido {game_id!r}.",
                    suggestion="Usa resolve_game para obtener un game_id válido.")
    box = queries.game_boxscore(ctx.engine, game_id, team_id)
    if box.empty:
        return fail("sin boxscore", detail=f"El partido {game_id} no tiene boxscore cargado.")
    rows = records(box)
    quarters = queries.game_quarter_stats(ctx.engine, game_id)
    data = {"game": clean_dict(header), "boxscore": rows, "quarters": records(quarters)}

    # Metadata de partido (Fase 3): árbitros/asistencia/pabellón/entrenadores.
    # Sondeada a nivel de BASE DE DATOS por `Capabilities.game_metadata`, pero
    # un partido concreto puede seguir sin ella (se ingirió antes de esta fase,
    # o la fuente no la dio para ese partido) — de ahí el segundo aviso.
    warnings = []
    if ctx.capabilities.game_metadata:
        metadata = clean_dict(queries.game_metadata(ctx.engine, game_id))
        if metadata and any(value is not None for value in metadata.values()):
            data["metadata"] = metadata
        else:
            warnings.append(
                "Este partido concreto no trae árbitros/asistencia/pabellón/entrenadores: no todos "
                "los partidos ingeridos los tienen."
            )
    else:
        warnings.append(
            "Esta base de datos no tiene metadata de partido: no hables de árbitros, asistencia, "
            "pabellón ni entrenadores."
        )

    return ok(
        data,
        source="player_game_stats + game_team_quarter_stats",
        scope=f"{game_id} · {header['game_date']} · {header['competition']}",
        warnings=warnings or None,
        artifact=artifact("table", rows, title="Boxscore"),
    )


@register(
    "game_play_events",
    family="team",
    description=(
        "Play-by-play tipado de un partido: robos, pérdidas, tapones, rebote ofensivo/defensivo, "
        "asistencias, faltas recibidas y faltas personales, con reloj y marcador exactos. Para "
        "'¿en qué momento del partido pasó X?' — no todos los partidos lo tienen todavía (Fase 2)."
    ),
    parameters=schema(
        {
            "game_id": {"type": "string"},
            "event_type": {
                "type": "string",
                "enum": ["steal", "turnover", "block", "oreb", "dreb", "assist", "foul_drawn", "foul_personal"],
                "description": "Acota a un tipo de evento; omite para traerlos todos.",
            },
        },
        required=["game_id"],
    ),
    artifact="table",
    requires="play_events",
)
def game_play_events(ctx: ToolContext, game_id: str, event_type: str = None) -> dict:
    header = queries_assistant.game_header(ctx.engine, game_id)
    if header is None:
        return fail("partido desconocido", detail=f"No existe el partido {game_id!r}.",
                    suggestion="Usa resolve_game para obtener un game_id válido.")
    events = queries_assistant.game_play_events(ctx.engine, game_id, event_type)
    if events.empty:
        return fail(
            "sin eventos",
            detail=f"El partido {game_id} no tiene play-by-play tipado todavía (Fase 2).",
            suggestion="Prueba con game_boxscore para el resumen agregado de ese partido.",
        )
    rows = records(events)
    return ok(
        rows,
        source="play_events",
        scope=f"{game_id} · {header['game_date']} · {header['competition']}",
        gp=len(rows),
        artifact=artifact("table", rows, title="Play-by-play"),
    )


# Tope de eventos que se detallan por parcial. El play-by-play de tres
# minutos son ~20 filas y tres parciales agotarían el presupuesto de filas
# (`MAX_ROWS`) sin que el modelo necesite el detalle entero para redactar: lo
# que hace falta es el patrón (tres pérdidas seguidas), no el acta.
_RUN_EVENTS = 12


@register(
    "game_runs",
    family="team",
    description=(
        "Dónde se decidió un partido: los parciales (ventanas cortas en las que el marcador se "
        "movió mucho), cada uno con el quinteto que estaba en pista y qué pasó en esos minutos. "
        "Para '¿en qué momento se fue el partido?'. Necesita game_id: sácalo antes con resolve_game."
    ),
    parameters=schema(
        {
            "game_id": {"type": "string"},
            "team_id": {
                "type": "string",
                "description": "Desde qué equipo se mira el parcial (por defecto, el equipo propio).",
            },
            "window_minutes": {
                "type": "number",
                "description": "Duración máxima del parcial en minutos (3 por defecto).",
            },
            "min_swing": {
                "type": "integer",
                "description": "Puntos que como mínimo se mueve el marcador para llamarlo parcial (8 por defecto).",
            },
            "limit": {"type": "integer", "description": "Cuántos parciales detallar (3 por defecto)."},
        },
        required=["game_id"],
    ),
    artifact="table",
    requires="play_events",
)
def game_runs(
    ctx: ToolContext,
    game_id: str,
    team_id: str = None,
    window_minutes: float = 3.0,
    min_swing: int = 8,
    limit: int = 3,
) -> dict:
    """Los parciales de un partido, con quinteto y eventos — la misma lógica que la pantalla.

    Comparte `queries.game_runs`/`window_lineup` con la pestaña "Rotaciones"
    de "Partidos anteriores" a propósito: dos definiciones distintas de
    "parcial" —una en el gráfico y otra en el chat— es la forma más rápida de
    que el asistente y la pantalla se contradigan delante del entrenador.
    """
    header = queries_assistant.game_header(ctx.engine, game_id)
    if header is None:
        return fail("partido desconocido", detail=f"No existe el partido {game_id!r}.",
                    suggestion="Usa resolve_game para obtener un game_id válido.")

    sides = (header["home_team_id"], header["away_team_id"])
    if team_id is not None and team_id not in sides:
        return fail(
            "equipo ajeno al partido",
            detail=f"{team_id!r} no juega el partido {game_id}.",
            suggestion=f"Los dos equipos de ese partido son {sides[0]!r} y {sides[1]!r}.",
        )
    # Por defecto, el punto de vista del equipo propio; si el Baskonia no juega
    # ese partido (scouting de un rival), el del local. El signo del parcial
    # ("a favor"/"en contra") solo significa algo con un equipo delante.
    team = team_id or (ctx.own_team_id if ctx.own_team_id in sides else sides[0])

    runs = queries.game_runs(ctx.engine, game_id, team, float(window_minutes) * 60.0, int(min_swing))
    if runs.empty:
        return fail(
            "sin parciales",
            detail=(
                f"En {game_id} no hay ninguna ventana de {window_minutes:g} minutos o menos en la "
                f"que el marcador se mueva {min_swing} puntos o más."
            ),
            suggestion="Baja min_swing o sube window_minutes, o usa game_boxscore para el resumen del partido.",
        )

    stints = queries.game_stints(ctx.engine, game_id, team)
    detailed = []
    for run in runs.head(max(int(limit), 1)).to_dict("records"):
        # `clean_dict` antes de tocar nada: los valores salen de pandas como
        # escalares de numpy y el JSON del `tool_result` los serializaría como
        # texto ("2085.0"), que es justo lo que el modelo acabaría citando.
        run = clean_dict(run)
        lineup = queries.window_lineup(stints, run["start_seconds"], run["end_seconds"])
        events = queries.game_window_events(
            ctx.engine, game_id, run["start_seconds"], run["end_seconds"], team
        )
        run["quinteto"] = " · ".join(lineup["player_name"]) if not lineup.empty else None
        run["eventos"] = [
            f"{event['quarter']} {event['game_clock']} · {event['score_for']}-{event['score_against']} · "
            f"{event['team_name']}: {event['event_type']}"
            + (f" ({event['player_name']})" if event["player_name"] else "")
            for event in records(events, limit=_RUN_EVENTS)
        ]
        detailed.append(run)

    rows = records(runs)
    warnings = [
        "En la lista de eventos de un parcial NO hay tiros: `shots` no guarda ni cuarto ni reloj. "
        "Los puntos se ven por el salto del marcador — no digas que un parcial fue 'sin canastas'."
    ]
    if stints.empty:
        warnings.append(
            f"Este partido no tiene tramos de quinteto de {team}: hay parciales, pero no se puede "
            "decir quién estaba en pista."
        )
    return ok(
        {"game": clean_dict(header), "team_id": team, "parciales": detailed},
        source="play_events + lineup_stints",
        scope=(
            f"{game_id} · {header['game_date']} · visto desde {team} · "
            f"ventana {window_minutes:g} min · swing >= {min_swing}"
        ),
        gp=1,
        warnings=warnings,
        artifact=artifact("table", rows, title="Parciales del partido"),
    )
