"""Herramienta de similitud de jugadores (propuesta 11): "¿a quién se parece?"

Reutiliza EXACTAMENTE el mismo motor que pinta el bloque "se parece a" de
`components/player_dialog.py` y la página `screens/similitud.py`
(`analytics.similarity`) — la respuesta del asistente y la de la pantalla no
pueden divergir, porque las dos llaman a `build_player_vectors` +
`most_similar` sobre las mismas tres consultas.

Sin capa de LLM: la explicación de "en qué se parecen" (`explain_similarity`)
ya sale en español llano de reglas fijas (§4 del documento: "sale gratis del
propio cálculo"), no hace falta redactarla.
"""
import pandas as pd

from .base import ToolContext, artifact, fail, ok, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import similarity as similarity_engine
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import similarity as similarity_engine
    from data import queries, queries_assistant

#: Candidatos por defecto (§2 del documento: "salen los 8-10 más parecidos").
DEFAULT_TOP_N = 8


def _player_season(ctx: ToolContext, player_id: str, season_id):
    """Como `player.py::_player_season` (mismo criterio de caída a temporada anterior);
    duplicada a propósito, ver la nota de `fatigue.py::_team_season`."""
    preferred = ctx.season_id if season_id is None else int(season_id)
    return season_with_fallback(queries.player_scouting_season, ctx.engine, player_id, preferred)


def build_league_vectors(ctx: ToolContext, season: int):
    """El vector de perfil de TODOS los jugadores de la liga esa temporada.

    Compartida entre la herramienta y las dos pantallas (`player_dialog.py`,
    `screens/similitud.py`) para que las tres consultas de origen se pidan
    siempre igual — ver el docstring del módulo.
    """
    index_df = queries.league_player_index(ctx.engine, season)
    percentiles_df = queries_assistant.league_player_percentiles(ctx.engine, season)
    zone_volume_df = queries.league_player_zone_volume(ctx.engine, season)
    return similarity_engine.build_player_vectors(index_df, percentiles_df, zone_volume_df)


def _competition_labels(ctx: ToolContext) -> dict:
    competitions = queries.list_competitions(ctx.engine)
    return dict(zip(competitions["id"], competitions["name"])) if not competitions.empty else {}


def _result_row(row, target_row, competition_labels: dict, *, group_weights=None) -> dict:
    explanation = similarity_engine.explain_similarity(target_row, row, group_weights=group_weights)
    return {
        "player_id": row["player_id"],
        "name": row["name"],
        "team_id": row["team_id"],
        "team_name": row["team_name"],
        "competition": competition_labels.get(row["competition_id"], row["competition_id"]),
        "gp": int(row["gp_total"]) if pd.notna(row["gp_total"]) else None,
        "minutes_total": round(float(row["minutes_total"]), 1) if pd.notna(row["minutes_total"]) else None,
        "similarity_score": round(float(row["similarity_score"]), 1),
        "closest": explanation["closest"],
        "farthest": explanation["farthest"],
    }


@register(
    "similar_players",
    family="player",
    description=(
        "A quién se parece un jugador dentro de TODA la base de datos (cualquier equipo o competición), "
        "con la explicación de en qué se parecen y en qué se alejan. Sirve para dos preguntas: traducir a "
        "un rival desconocido a alguien que el equipo ya ha defendido, o buscar sustitutos de mercado para "
        "una baja. 'method' cambia la pregunta: cosine compara la FORMA del perfil (estilo, ignorando el "
        "nivel absoluto, por defecto); euclidean compara también el NIVEL. Puramente estadístico — el "
        "cálculo no mira altura ni peso (aunque la base de datos los tenga), así que puede emparejar "
        "posiciones distintas que produzcan parecido."
    ),
    parameters=schema(
        {
            "player_id": {"type": "string"},
            "season_id": {"type": "integer"},
            "method": {
                "type": "string", "enum": ["cosine", "euclidean"],
                "description": "'cosine' (estilo, por defecto) o 'euclidean' (también nivel).",
            },
            "top_n": {"type": "integer", "description": "Cuántos candidatos como mucho (8 por defecto)."},
            "competition_id": {"type": "integer", "description": "Acota la búsqueda a una sola competición."},
            "only_faced": {
                "type": "boolean",
                "description": "Solo jugadores de equipos a los que el equipo propio se ha enfrentado esta temporada.",
            },
        },
        required=["player_id"],
    ),
    artifact="table",
)
def similar_players(
    ctx: ToolContext,
    player_id: str,
    season_id: int = None,
    method: str = "cosine",
    top_n: int = None,
    competition_id: int = None,
    only_faced: bool = False,
) -> dict:
    """La lista de más parecidos a `player_id`, con procedencia y aviso de limitaciones (§5 del documento)."""
    season, fallback_warning = _player_season(ctx, player_id, season_id)
    method = method or "cosine"
    n = int(top_n) if top_n else DEFAULT_TOP_N

    vectors = build_league_vectors(ctx, season)
    if vectors.empty or player_id not in set(vectors["player_id"]):
        return fail(
            "sin datos",
            detail=(
                f"{player_id} no tiene perfil de percentiles en la temporada {season} "
                "(hace falta al menos 5 partidos en una competición)."
            ),
            suggestion="Prueba con otra temporada, o comprueba el id con resolve_entity.",
        )

    allowed_team_ids = None
    if only_faced:
        allowed_team_ids = queries.opponent_team_ids(ctx.engine, ctx.own_team_id, season)
        if not allowed_team_ids:
            return fail(
                "sin rivales registrados",
                detail=f"{ctx.own_team_id} no tiene partidos registrados en la temporada {season}.",
                suggestion="Quita only_faced, o prueba con otra temporada.",
            )

    try:
        results = similarity_engine.most_similar(
            vectors, player_id, method=method, competition_id=competition_id,
            allowed_team_ids=allowed_team_ids, top_n=n,
        )
    except ValueError as exc:
        return fail("parámetro inválido", detail=str(exc), suggestion="method debe ser 'cosine' o 'euclidean'.")

    if results.empty:
        return fail(
            "sin candidatos",
            detail="Ningún jugador cumple los filtros (minutos mínimos, competición, solo rivales enfrentados) "
                   "con perfil suficientemente completo para comparar.",
            suggestion="Prueba sin only_faced, o sin acotar competition_id.",
        )

    target_row = vectors.loc[vectors["player_id"] == player_id].iloc[0]
    competition_labels = _competition_labels(ctx)
    rows = [_result_row(row, target_row, competition_labels) for _, row in results.iterrows()]

    warnings = [fallback_warning] if fallback_warning else []
    warnings.append(similarity_engine.SIMILARITY_CAVEAT)

    return ok(
        {"target": {"player_id": player_id, "name": target_row["name"]}, "method": method, "similar": rows},
        source="player_percentiles + shots (reparto de tiro por zona)",
        scope=f"temporada {season}" + (" · solo rivales enfrentados" if only_faced else ""),
        gp=len(rows),
        warnings=warnings,
        artifact=artifact("table", rows, title=f"Jugadores similares a {target_row['name']}"),
    )
