"""Herramientas de jugador (§4.2).

`player_game` es la herramienta de la primera pregunta del encargo ("¿qué tal
jugó Markus Howard su último partido?"): resuelve el partido, trae la línea,
la sitúa en el contexto del partido y la compara con su propia media — porque
"21 puntos" solo significa algo comparado con lo suyo, y esa comparación la
hace la herramienta y no el modelo a ojo.
"""
from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema

try:  # pragma: no cover - ver nota en tools/context.py
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from data import queries, queries_assistant


def _season(ctx: ToolContext, season_id) -> int:
    """Temporada pedida, o la seleccionada en la barra lateral (§5.2)."""
    return ctx.season_id if season_id is None else int(season_id)


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

    season = _season(ctx, season_id)
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

    warnings = []
    if line.get("fta") is None:
        warnings.append("Este partido no trae tiros libres (columna vacía en la base de datos), no es que no tirara.")

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
    season = _season(ctx, season_id)
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
        artifact=artifact("table", rows, title="Partido a partido"),
    )


@register(
    "player_averages",
    family="player",
    description=(
        "Medias de un jugador por competición y combinadas, con partidos jugados (gp). "
        "Cita siempre el gp: 'en 43 partidos', no 'esta temporada'."
    ),
    parameters=schema(
        {"player_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["player_id"]
    ),
    artifact="table",
)
def player_averages(ctx: ToolContext, player_id: str, season_id: int = None) -> dict:
    """Medias de `player_stats_*`, una fila por competición más la combinada."""
    season = _season(ctx, season_id)
    averages = queries.player_averages_all(ctx.engine, player_id, season)
    if averages.empty:
        return fail("sin datos", detail=f"{player_id} no tiene partidos en la temporada {season}.",
                    suggestion="Prueba con otra temporada, o comprueba el id con resolve_entity.")
    rows = records(averages)
    combined = next((r for r in rows if r["competition"] == "Combinado"), rows[0])
    return ok(
        rows,
        source="player_stats_by_competition / player_stats_combined",
        scope=f"temporada {season}",
        gp=combined.get("gp"),
        warnings=[
            "Los ratings y el pace de Euroliga son estimaciones propias (Dean Oliver), no dato oficial: "
            "dilo si comparas ACB con Euroliga."
        ],
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
    season = _season(ctx, season_id)
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
    season = _season(ctx, season_id)
    form = queries_assistant.player_form(ctx.engine, player_id, season, last_n)
    if form is None:
        return fail(
            "muestra insuficiente",
            detail=f"{player_id} tiene menos de 2 partidos en la temporada {season}: no hay tendencia que medir.",
            suggestion="Usa player_game para el partido suelto que sí exista.",
        )
    return ok(form, source="player_game_stats", scope=f"temporada {season}", gp=form["gp"])


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
    season = _season(ctx, season_id)
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
        artifact=artifact("table", rows, title="Carga de minutos"),
    )
