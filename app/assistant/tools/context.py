"""Herramientas de contexto y resolución (§4.1).

Son las que el modelo llama ANTES que ninguna otra: el resto del catálogo
acepta ids, nunca nombres libres, así que sin estas no puede empezar. Que
resolver sea una herramienta y no una adivinanza del modelo es lo que hace
que "Fenerbache" funcione igual con cualquier proveedor (§5).
"""
from sqlalchemy import text

from .. import resolve
from ..resolve import ghost_candidates, is_ambiguous
from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema

# `queries.py` se importa igual venga el paquete por `app.*` (pytest, raíz en
# sys.path) o por `assistant.*` (Streamlit, `app/` en sys.path). Es el mismo
# módulo en los dos casos; lo único que cambia es cómo se llega a él.
try:  # pragma: no cover - depende de cómo se arranque el proceso, no de la lógica
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from data import queries, queries_assistant


@register(
    "get_context",
    family="context",
    description=(
        "Estado de la base de datos: equipo propio, temporada seleccionada y disponibles, "
        "competiciones, fecha de hoy, rango de fechas con datos y qué NO se puede contestar. "
        "Llámala una vez al principio de la conversación."
    ),
    parameters=schema({}),
)
def get_context(ctx: ToolContext) -> dict:
    """Qué hay, qué temporada se está mirando y qué falta en ESTA base de datos.

    Las carencias van en la respuesta (`gaps`) y no solo en el prompt porque
    el modelo debe poder citarlas al explicar por qué no responde algo: el
    patrón de §2.3 es "no lo tengo, pero sí puedo darte esto otro", y para eso
    hace falta saber exactamente qué falta.
    """
    caps = ctx.capabilities
    season_label = next(
        (s["label"] for s in caps.seasons if s["id"] == ctx.season_id), str(ctx.season_id)
    )
    with ctx.engine.connect() as conn:
        own_team = conn.execute(
            text("SELECT name FROM teams WHERE id = :id"), {"id": ctx.own_team_id}
        ).scalar()

    return ok(
        {
            "own_team": {"id": ctx.own_team_id, "name": own_team},
            "season": {"id": ctx.season_id, "label": season_label},
            "seasons": caps.seasons,
            "competitions": caps.competitions,
            "today": ctx.today.isoformat(),
            "date_range": caps.date_range,
            "counts": caps.counts,
            "gaps": caps.missing_summary(),
        },
        source="seasons/competitions/games",
        scope=f"temporada seleccionada {season_label}",
    )


@register(
    "resolve_entity",
    family="context",
    description=(
        "Convierte lo que escribe el usuario ('Fenerbache', 'Howard', 'nosotros') en ids de "
        "jugador o equipo. Úsala SIEMPRE antes que cualquier herramienta que pida un id. "
        "Si devuelve ambiguous=true, pregunta al usuario cuál en vez de elegir tú."
    ),
    parameters=schema(
        {
            "query": {"type": "string", "description": "Nombre tal cual lo escribió el usuario."},
            "kind": {
                "type": "string",
                "enum": ["player", "team"],
                "description": "Acota la búsqueda. Omítelo si no sabes de qué tipo es.",
            },
        },
        required=["query"],
    ),
)
def resolve_entity(ctx: ToolContext, query: str, kind: str = None) -> dict:
    """Candidatos ordenados para un nombre libre. Con empate real NO elige (§5.1)."""
    candidates = resolve.resolve_entity(ctx.engine, query, kind=kind)
    if not candidates:
        return fail(
            "sin coincidencias",
            detail=f"No hay ningún jugador ni equipo que se parezca a {query!r} en la base de datos.",
            suggestion="Comprueba el nombre, o usa get_context para ver qué competiciones hay cargadas.",
        )
    ambiguous = is_ambiguous(candidates)
    ghosts = ghost_candidates(candidates)
    if ambiguous:
        note = "Varios candidatos empatados: pregunta al usuario a cuál se refiere."
    elif ghosts:
        # Desempatado por datos y no por nombre: se dice cuál se ha apartado y
        # por qué. Sin esta frase el modelo elegiría en silencio, que es
        # exactamente lo que §5.1 prohíbe (ver `resolve.ghost_candidates`).
        apartados = ", ".join(f"{c.name} ({c.id})" for c in ghosts)
        note = (
            f"Hay otro registro con el mismo nombre y CERO partidos cargados ({apartados}); "
            "se ha usado el que sí tiene datos. Dilo en la respuesta por si el usuario se "
            "refería al otro."
        )
    else:
        note = None

    return ok(
        {
            "candidates": [c.to_dict() for c in candidates],
            "ambiguous": ambiguous,
            "note": note,
        },
        source="players/teams + tablas puente de ids externos",
        scope=f"búsqueda: {query!r}",
    )


@register(
    "resolve_game",
    family="context",
    description=(
        "Encuentra un partido YA JUGADO y devuelve su cabecera (fecha, competición, marcador). "
        "Para el próximo partido usa next_opponent, no esta."
    ),
    parameters=schema(
        {
            "team_id": {"type": "string", "description": "Equipo del partido."},
            "when": {
                "type": "string",
                "description": "'last' (el más reciente), 'first', o una fecha YYYY-MM-DD.",
            },
            "opponent_id": {"type": "string", "description": "Acota a los cruces contra este rival."},
            "competition_id": {"type": "integer", "description": "Acota a una competición."},
            "season_id": {"type": "integer", "description": "Temporada; por defecto, la seleccionada."},
        },
        required=["team_id"],
    ),
)
def resolve_game(
    ctx: ToolContext,
    team_id: str,
    when: str = "last",
    opponent_id: str = None,
    competition_id: int = None,
    season_id: int = None,
) -> dict:
    """Un `game_id` con su cabecera, o un error que dice qué probar en su lugar."""
    game_id = resolve.resolve_team_game(
        ctx.engine,
        team_id,
        when=when,
        opponent_id=opponent_id,
        competition_id=competition_id,
        season_id=season_id if season_id is not None else ctx.season_id,
        today=ctx.today,
    )
    if game_id is None:
        return fail(
            "sin partidos",
            detail=f"No hay ningún partido de {team_id} con ese filtro en los datos cargados.",
            suggestion="Prueba sin acotar rival/competición, o con otra temporada (get_context las lista).",
        )
    header = queries_assistant.game_header(ctx.engine, game_id)
    return ok(
        clean_dict(header),
        source="games",
        scope=f"{game_id} · {header['game_date']}",
    )


@register(
    "next_opponent",
    family="team",
    description=(
        "Próximo partido del equipo propio (calendario futuro) y lo que se sabe del rival. "
        "Solo tiene sentido en la temporada en curso."
    ),
    parameters=schema(
        {"limit": {"type": "integer", "description": "Cuántos partidos futuros devolver (por defecto 1)."}}
    ),
    artifact="table",
)
def next_opponent(ctx: ToolContext, limit: int = 1) -> dict:
    """Próximo(s) rival(es) desde `upcoming_matchups`.

    Los partidos futuros NO están en `games` (que solo guarda lo ya jugado),
    así que esto no se puede sacar de `resolve_game` — es una tabla distinta
    con su propia ingesta (`ingest/*/pipeline.py::run_upcoming`). Tampoco se
    acota por `ctx.season_id`: el próximo partido es el siguiente del
    calendario, viva en la temporada que viva (ver `queries.next_matchup`).
    """
    upcoming = queries.upcoming_matchups_list(ctx.engine, ctx.today)
    if upcoming.empty:
        return fail(
            "sin calendario",
            detail="No hay ningún partido futuro cargado en el calendario.",
            suggestion="Pregunta por partidos ya jugados con resolve_game.",
        )
    rows = records(upcoming, limit=max(1, limit))
    detail = queries.next_matchup(ctx.engine, ctx.today)
    return ok(
        {"upcoming": rows, "next": clean_dict(detail)},
        source="upcoming_matchups",
        scope=f"desde {ctx.today.isoformat()}",
        artifact=artifact("table", rows, title="Próximos partidos"),
    )
