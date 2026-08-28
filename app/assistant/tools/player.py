"""Herramientas de jugador (§4.2).

`player_game` es la herramienta de la primera pregunta del encargo ("¿qué tal
jugó Markus Howard su último partido?"): resuelve el partido, trae la línea,
la sitúa en el contexto del partido y la compara con su propia media — porque
"21 puntos" solo significa algo comparado con lo suyo, y esa comparación la
hace la herramienta y no el modelo a ojo.
"""
from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from data import queries, queries_assistant


def _season(ctx: ToolContext, season_id) -> int:
    """Temporada pedida, o la seleccionada en la barra lateral (§5.2)."""
    return ctx.season_id if season_id is None else int(season_id)


def _player_season(ctx: ToolContext, player_id: str, season_id):
    """Como `_season`, cayendo a la última temporada con partidos de `player_id` si hace falta.

    Devuelve `(season, warning)` — ver `season_with_fallback`. `warning` es
    `None` salvo que se haya caído a una temporada anterior a la pedida.
    """
    preferred = _season(ctx, season_id)
    return season_with_fallback(queries.player_scouting_season, ctx.engine, player_id, preferred)


def _team_season(ctx: ToolContext, team_id: str, season_id):
    """Igual que `_player_season`, pero para herramientas acotadas por equipo (roster, minutos)."""
    preferred = _season(ctx, season_id)
    return season_with_fallback(queries.team_scouting_season, ctx.engine, team_id, preferred)


@register(
    "player_profile",
    family="player",
    description=(
        "Ficha de un jugador: dorsal, posición, equipo, altura, nacionalidad. "
        "Para sus números usa player_averages o player_game."
    ),
    parameters=schema({"player_id": {"type": "string"}}, required=["player_id"]),
)
def player_profile(ctx: ToolContext, player_id: str) -> dict:
    """Bio de `players` (+ lo que puebla `ingest/baskonia_web` para la plantilla propia)."""
    bio = queries.player_bio(ctx.engine, player_id)
    if bio is None:
        return fail("jugador desconocido", detail=f"No existe el jugador {player_id!r}.",
                    suggestion="Usa resolve_entity para obtener el id correcto.")
    warnings = []
    if not bio.get("birth_date") and not bio.get("nationality"):
        warnings.append(
            "Solo hay bio completa de la plantilla propia; de un rival suelen faltar altura, "
            "nacionalidad y fecha de nacimiento."
        )
    return ok(clean_dict(bio), source="players", scope=player_id, warnings=warnings or None)


@register(
    "player_game",
    family="player",
    description=(
        "Cómo jugó un jugador UN partido concreto: su línea, el contexto del partido y la "
        "comparación con su media de la temporada. Para medias de varios partidos usa "
        "player_averages; para una racha usa player_form."
    ),
    parameters=schema(
        {
            "player_id": {"type": "string"},
            "when": {
                "type": "string",
                "description": "'last' (su último partido jugado), 'first', o fecha YYYY-MM-DD.",
            },
            "game_id": {"type": "string", "description": "Partido concreto, si ya lo tienes."},
            "season_id": {"type": "integer"},
        },
        required=["player_id"],
    ),
    artifact="shot_chart",
)
def player_game(
    ctx: ToolContext, player_id: str, when: str = "last", game_id: str = None, season_id: int = None
) -> dict:
    """La pregunta 2.1 del encargo, resuelta de una llamada.

    "Su último partido" es el último con fila en `player_game_stats` **para
    ese jugador** — no el último del equipo, porque no siempre jugó (§5.2).
    """
    from .. import resolve

    season, fallback_warning = _player_season(ctx, player_id, season_id)
    if game_id is None:
        game_id = resolve.resolve_player_game(ctx.engine, player_id, season_id=season, when=when)
    if game_id is None:
        return fail(
            "sin datos",
            detail=f"{player_id} no tiene partidos registrados en la temporada {season}.",
            suggestion="Prueba con otra temporada (get_context las lista) o comprueba el id con resolve_entity.",
        )

    line = queries_assistant.player_game_line(ctx.engine, game_id, player_id)
    if line is None:
        return fail(
            "sin datos",
            detail=f"{player_id} no tiene línea de boxscore en el partido {game_id}.",
            suggestion="Puede que no jugara ese partido: prueba con when='last'.",
        )

    header = queries_assistant.game_header(ctx.engine, game_id)
    averages = queries.player_averages_all(ctx.engine, player_id, season)
    combined = averages[averages["competition"] == "Combinado"]
    shots = queries_assistant.game_shots_for_player(ctx.engine, game_id, player_id)

    warnings = [fallback_warning] if fallback_warning else []
    if line.get("fta") is None:
        warnings.append("Este partido no trae tiros libres (columna vacía en la base de datos), no es que no tirara.")
    if line.get("stl") is None:
        warnings.append(
            "Este partido no trae boxscore ampliado (robos/pérdidas/tapones/faltas/rebote of-def/+-/PIR), "
            "columna vacía en la base de datos."
        )

    return ok(
        {
            "line": clean_dict(line),
            "game": clean_dict(header),
            "season_average": records(combined)[0] if not combined.empty else None,
            "shots": {"attempts": int(len(shots)), "made": int(shots["made"].sum()) if not shots.empty else 0},
        },
        source="player_game_stats + games + player_stats_combined",
        scope=f"{game_id} · {header['game_date']} · {header['competition']}",
        gp=int(combined["gp"].iloc[0]) if not combined.empty else None,
        warnings=warnings or None,
        artifact=artifact("shot_chart", records(shots, limit=400), title=f"Tiros · {line['player_name']}"),
    )


@register(
    "player_game_log",
    family="player",
    description=(
        "Partido a partido de un jugador en una temporada (fecha, rival, minutos, puntos, "
        "rebotes, asistencias, eFG%). Para un solo partido usa player_game."
    ),
    parameters=schema(
        {
            "player_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "limit": {"type": "integer", "description": "Últimos N partidos (por defecto todos)."},
        },
        required=["player_id"],
    ),
    artifact="table",
)
def player_game_log(ctx: ToolContext, player_id: str, season_id: int = None, limit: int = None) -> dict:
    """Log de partidos ya existente en `queries.py`, recortado a los últimos N."""
    season, fallback_warning = _player_season(ctx, player_id, season_id)
    log = queries.player_game_log(ctx.engine, player_id, season)
    if log.empty:
        return fail("sin datos", detail=f"{player_id} no tiene partidos en la temporada {season}.",
                    suggestion="Prueba con otra temporada.")
    total = len(log)
    trimmed = log.tail(limit) if limit else log
    rows = records(trimmed.drop(columns=["rival_logo_url"], errors="ignore"))
    return ok(
        rows,
        source="player_game_stats + games",
        scope=f"temporada {season}",
        gp=total,
        truncated_from=total if len(rows) < total else None,
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("table", rows, title="Partido a partido"),
    )


@register(
    "player_averages",
    family="player",
    description=(
        "Medias de un jugador por competición y combinadas, con partidos jugados (gp) y, si esta "
        "base de datos los tiene, sus tiros libres (volumen y acierto) y su boxscore ampliado "
        "(robos, pérdidas, tapones dados/recibidos, faltas cometidas/recibidas, rebote "
        "ofensivo/defensivo, +/-, PIR, mates). Cita siempre el gp: 'en 43 partidos', no 'esta temporada'."
    ),
    parameters=schema(
        {"player_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["player_id"]
    ),
    artifact="table",
)
def player_averages(ctx: ToolContext, player_id: str, season_id: int = None) -> dict:
    """Medias de `player_stats_*`, una fila por competición más la combinada.

    Los tiros libres van en un bloque aparte (`free_throws`) y solo si esta
    base de datos los tiene: `efg_pct` los excluye por definición, así que sin
    ellos no hay forma de leer el juego desde la línea — y prometerlos cuando
    no están es justo lo que evita el sondeo de capacidades (§7.3).
    """
    season, fallback_warning = _player_season(ctx, player_id, season_id)
    averages = queries.player_averages_all(ctx.engine, player_id, season)
    if averages.empty:
        return fail("sin datos", detail=f"{player_id} no tiene partidos en la temporada {season}.",
                    suggestion="Prueba con otra temporada, o comprueba el id con resolve_entity.")
    rows = records(averages)
    combined = next((r for r in rows if r["competition"] == "Combinado"), rows[0])

    warnings = []
    if fallback_warning:
        warnings.append(fallback_warning)
    warnings.append(
        "Los ratings y el pace de Euroliga son estimaciones propias (Dean Oliver), no dato oficial: "
        "dilo si comparas ACB con Euroliga."
    )
    free_throws = []
    if ctx.capabilities.free_throws:
        free_throws = records(queries_assistant.player_free_throws(ctx.engine, player_id, season))
        # `gp_ft` por debajo de `gp` significa que parte de los partidos se
        # ingirieron antes de que existieran las columnas: el porcentaje es
        # correcto pero está calculado sobre menos partidos de los que jugó.
        if any(row.get("gp_ft", 0) < row.get("gp", 0) for row in free_throws):
            warnings.append(
                "Algunos partidos no traen tiros libres: el acierto desde la línea está calculado "
                "sobre los partidos de `gp_ft`, no sobre `gp`."
            )
    else:
        warnings.append("Esta base de datos no tiene tiros libres: no hables del juego desde la línea.")

    # Boxscore ampliado (Fase 1): robos/pérdidas/tapones (dados y recibidos)/
    # faltas (cometidas y recibidas)/rebote ofensivo-defensivo/+-/PIR/mates —
    # mismo patrón condicionado que el bloque `free_throws` de arriba.
    box_extras = []
    if ctx.capabilities.box_extras:
        box_extras = records(queries_assistant.player_box_extras(ctx.engine, player_id, season))
        if any(row.get("gp_box_extras", 0) < row.get("gp", 0) for row in box_extras):
            warnings.append(
                "Algunos partidos no traen boxscore ampliado (robos/pérdidas/tapones/faltas/rebote "
                "of-def/+-/PIR): esas medias están calculadas sobre los partidos de `gp_box_extras`, "
                "no sobre `gp`."
            )
    else:
        warnings.append(
            "Esta base de datos no tiene boxscore ampliado: no hables de robos, tapones, pérdidas, "
            "faltas, rebote ofensivo/defensivo, +/- ni PIR de este jugador."
        )

    data = {"averages": rows, "free_throws": free_throws, "box_extras": box_extras}
    return ok(
        data,
        source="player_stats_by_competition / player_stats_combined",
        scope=f"temporada {season}",
        gp=combined.get("gp"),
        warnings=warnings,
        artifact=artifact("table", rows, title="Medias por competición"),
    )


@register(
    "player_shot_profile",
    family="player",
    description=(
        "Dónde tira un jugador y con qué acierto, por zona de cancha (volumen, % y reparto). "
        "De temporada, o de un partido si pasas game_id. Los tiros libres no llevan coordenadas "
        "y quedan fuera."
    ),
    parameters=schema(
        {
            "player_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "game_id": {"type": "string", "description": "Acota a un partido concreto."},
        },
        required=["player_id"],
    ),
    artifact="shot_chart",
)
def player_shot_profile(ctx: ToolContext, player_id: str, season_id: int = None, game_id: str = None) -> dict:
    """Perfil de tiro por zona + los tiros crudos para pintar el mapa."""
    # Sin fallback de temporada si se pide un partido concreto: `game_id` ya
    # fija la temporada real, y cambiarla por debajo mandaría tiros de un
    # partido distinto al pedido.
    if game_id:
        season, fallback_warning = _season(ctx, season_id), None
    else:
        season, fallback_warning = _player_season(ctx, player_id, season_id)
    zones = queries_assistant.player_zone_profile(ctx.engine, player_id, season, game_id=game_id)
    if zones.empty:
        return fail(
            "sin tiros",
            detail=f"No hay tiros con coordenadas de {player_id} en ese filtro.",
            suggestion="No todas las fuentes traen coordenadas de todos los partidos; prueba con la temporada entera.",
        )
    shots = (
        queries_assistant.game_shots_for_player(ctx.engine, game_id, player_id)
        if game_id
        else queries.player_shots_season(ctx.engine, player_id, season)
    )
    return ok(
        records(zones),
        source="shots + court_zones",
        scope=f"partido {game_id}" if game_id else f"temporada {season}",
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("shot_chart", records(shots, limit=1200), title="Mapa de tiros"),
    )


@register(
    "player_form",
    family="player",
    description=(
        "¿Está en racha? Últimos N partidos frente a su propia temporada, en z-scores "
        "(0 = lo normal en él, +1 = una desviación típica por encima)."
    ),
    parameters=schema(
        {
            "player_id": {"type": "string"},
            "last_n": {"type": "integer", "description": "Tamaño de la ventana reciente (5 por defecto)."},
            "season_id": {"type": "integer"},
        },
        required=["player_id"],
    ),
)
def player_form(ctx: ToolContext, player_id: str, last_n: int = 5, season_id: int = None) -> dict:
    """El "está en racha" con un número detrás, no con un adjetivo."""
    season, fallback_warning = _player_season(ctx, player_id, season_id)
    form = queries_assistant.player_form(ctx.engine, player_id, season, last_n)
    if form is None:
        return fail(
            "muestra insuficiente",
            detail=f"{player_id} tiene menos de 2 partidos en la temporada {season}: no hay tendencia que medir.",
            suggestion="Usa player_game para el partido suelto que sí exista.",
        )
    return ok(
        form,
        source="player_game_stats",
        scope=f"temporada {season}",
        gp=form["gp"],
        warnings=[fallback_warning] if fallback_warning else None,
    )


@register(
    "player_minutes_load",
    family="player",
    description=(
        "Carga de minutos de la plantilla de un equipo en sus últimos N partidos, jugador a "
        "jugador. Un jugador sin minutos en un partido aparece vacío (no convocado, lesión, DNP): "
        "esa ausencia es el dato."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string"},
            "n_games": {"type": "integer", "description": "Ventana de partidos (5 por defecto)."},
            "season_id": {"type": "integer"},
        },
        required=["team_id"],
    ),
    artifact="table",
)
def player_minutes_load(ctx: ToolContext, team_id: str, n_games: int = 5, season_id: int = None) -> dict:
    """Reparto de minutos (`queries.minutes_load`), en formato largo."""
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    load = queries.minutes_load(ctx.engine, team_id, season, n_games)
    if load.empty:
        return fail("sin datos", detail=f"No hay minutos registrados de {team_id} en la temporada {season}.")
    # Formato ancho (una fila por jugador, una columna por fecha): mucho más
    # compacto en el contexto que el formato largo, que repite el nombre del
    # jugador en cada fila.
    wide = load.pivot_table(index="player_name", columns="game_date", values="minutes").reset_index()
    wide.columns = [str(c) for c in wide.columns]
    rows = records(wide)
    return ok(
        rows,
        source="player_game_stats",
        scope=f"últimos {n_games} partidos · temporada {season}",
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("table", rows, title="Carga de minutos"),
    )


@register(
    "player_advanced_profile",
    family="player",
    description=(
        "Estadísticas avanzadas OFICIALES de un jugador partido a partido (ratio de asistencias/"
        "robos/pérdidas, % de rebote separado, TS%, fuente de puntos por tipo de tiro, ritmo "
        "individual), con si su equipo ganó o perdió ese partido. Solo existe para ACB, nunca para "
        "Euroliga (esa fuente no tiene avanzadas oficiales por partido, solo agregado de temporada)."
    ),
    parameters=schema(
        {"player_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["player_id"]
    ),
    artifact="table",
    requires="player_advanced_stats",
)
def player_advanced_profile(ctx: ToolContext, player_id: str, season_id: int = None) -> dict:
    """Avanzadas oficiales por partido (Fase 4, ACB-only) — ver `queries_assistant.player_advanced_profile`."""
    season, fallback_warning = _player_season(ctx, player_id, season_id)
    profile = queries_assistant.player_advanced_profile(ctx.engine, player_id, season)
    if profile.empty:
        return fail(
            "sin datos",
            detail=f"{player_id} no tiene avanzadas oficiales por partido en la temporada {season}.",
            suggestion=(
                "Esta fase solo se ingiere para el Baskonia y su próximo rival (por coste de red), y "
                "solo existe en ACB — un jugador de Euroliga o un rival histórico no la tendrá."
            ),
        )
    rows = records(profile)
    warnings = [fallback_warning] if fallback_warning else []
    warnings.append(
        "'win' se deduce del equipo ACTUAL del jugador (players.team_id): en un jugador traspasado "
        "a mitad de temporada puede no coincidir con el equipo real de ese partido concreto."
    )
    return ok(
        rows,
        source="player_advanced_stats + games",
        scope=f"temporada {season}",
        gp=len(rows),
        warnings=warnings,
        artifact=artifact("table", rows, title="Avanzadas oficiales por partido"),
    )


@register(
    "player_quarter_profile",
    family="player",
    description=(
        "Rendimiento medio de un jugador por cuarto a lo largo de la temporada (puntos, "
        "rebotes, asistencias, +/-, PIR): ¿empieza fuerte y decae, o al revés (impacto desde el "
        "banquillo en el último cuarto)? Solo existe para ACB — Euroliga no publica boxscore de "
        "jugador por cuarto, solo puntos de equipo (ver team_quarter_profile para eso)."
    ),
    parameters=schema(
        {"player_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["player_id"]
    ),
    artifact="bar",
    requires="quarter_player_stats",
)
def player_quarter_profile(ctx: ToolContext, player_id: str, season_id: int = None) -> dict:
    """Perfil por cuarto de un jugador (Fase 3, ACB-only) — ver `queries_assistant.player_quarter_profile`."""
    season, fallback_warning = _player_season(ctx, player_id, season_id)
    quarters = queries_assistant.player_quarter_profile(ctx.engine, player_id, season)
    if quarters.empty:
        return fail(
            "sin datos",
            detail=f"{player_id} no tiene boxscore por cuarto en la temporada {season}.",
            suggestion="Esta fase solo existe en partidos de ACB: un jugador que solo jugó en Euroliga no la tendrá.",
        )
    rows = records(quarters)
    return ok(
        rows,
        source="player_game_quarter_stats",
        scope=f"temporada {season} · solo partidos de ACB",
        gp=int(quarters["gp"].max()),
        warnings=[fallback_warning] if fallback_warning else None,
        artifact=artifact("bar", rows, title="Rendimiento por cuarto"),
    )
