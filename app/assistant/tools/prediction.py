"""Herramienta de predicción del partido (propuesta 16, A2 de la hoja de ruta).

`game_prediction` responde a "¿cómo vemos el partido contra X?" / "¿somos
favoritos?" con el mismo cálculo que pinta "Próximo rival" y la portada del
dossier (`data/queries_prediction.matchup_prediction`): margen esperado,
probabilidad de victoria y **qué la mueve** en puntos (nivel ajustado por
calendario, pista, descanso), más cuánto acierta el modelo probado hacia
atrás — para que el asistente no venda un porcentaje sin su contexto.
"""
import datetime as dt

from .base import ToolContext, artifact, fail, ok, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import prediction
    from app.data import queries, queries_prediction
except ImportError:  # pragma: no cover
    from analytics import prediction
    from data import queries, queries_prediction

_MODEL_WARNING = (
    "Es un modelo (nivel ajustado por calendario + pista + descanso), no un pronóstico: no sabe de "
    "lesiones, bajas ni rotaciones. Da siempre el margen con su descomposición y el acierto del backtest."
)
#: Mismo umbral que la pantalla (`components/prediction.py::_FEW_GAMES`).
_FEW_GAMES = 8


@register(
    "game_prediction",
    family="team",
    description=(
        "Predicción de un partido del equipo propio: margen esperado, probabilidad de victoria y qué la "
        "mueve en puntos (nivel ajustado por calendario, jugar en casa/fuera, días de descanso de cada "
        "uno), con el acierto del modelo en la temporada. Sin opponent_id usa el próximo partido del "
        "calendario. Para partidos ya jugados NO: usa resolve_game."
    ),
    parameters=schema(
        {
            "opponent_id": {"type": "string", "description": "Rival (team_id); por defecto, el del próximo partido."},
            "is_home": {"type": "boolean", "description": "¿Jugamos en casa? Por defecto, lo que diga el calendario."},
            "date": {"type": "string", "description": "Fecha del partido YYYY-MM-DD; por defecto, la del calendario."},
            "season_id": {"type": "integer", "description": "Temporada de los datos; por defecto, la seleccionada."},
        }
    ),
    artifact="table",
)
def game_prediction(
    ctx: ToolContext, opponent_id: str = None, is_home: bool = None, date: str = None, season_id: int = None
) -> dict:
    """Margen, probabilidad, descomposición y backtest del partido contra `opponent_id`."""
    warnings = [_MODEL_WARNING]
    competition = None
    if opponent_id is None:
        matchup = queries.next_matchup(ctx.engine, ctx.today)
        if matchup is None:
            return fail(
                "sin calendario",
                detail="No hay ningún partido futuro cargado y no se ha dicho contra quién.",
                suggestion="Pasa opponent_id (y is_home) para predecir un cruce concreto.",
            )
        opponent_id = matchup["opponent_team_id"]
    else:
        matchup = queries_prediction.upcoming_matchup_vs(ctx.engine, opponent_id, ctx.today)
    if matchup is not None and date is not None and str(matchup["match_date"])[:10] != date:
        # Otra fecha que la del calendario = otro partido: su pista y su
        # competición no valen para este (p. ej. la Copa en sede neutral).
        matchup = None
    if matchup is not None:
        competition = matchup["competition"]
        if is_home is None:
            is_home = bool(matchup["is_home"])
        if date is None:
            date = str(matchup["match_date"])[:10]
    if is_home is None:
        warnings.append("Sin partido en el calendario ni is_home: se calcula en pista neutral (sin ventaja de campo).")

    try:
        match_date = dt.date.fromisoformat(date) if date else ctx.today
    except ValueError:
        return fail("fecha inválida", detail=f"{date!r} no es YYYY-MM-DD.", suggestion="Usa el formato 2026-10-04.")

    preferred = ctx.season_id if season_id is None else int(season_id)
    season, fallback_warning = season_with_fallback(queries.team_scouting_season, ctx.engine, opponent_id, preferred)
    if fallback_warning:
        warnings.append(fallback_warning)

    result = queries_prediction.matchup_prediction(
        ctx.engine, ctx.own_team_id, opponent_id, season, match_date, is_home, competition
    )
    if result is None:
        return fail(
            "sin datos",
            detail=f"La temporada {season} no tiene partidos jugados antes del {match_date} para ajustar el modelo.",
            suggestion="Prueba con la temporada anterior (season_id).",
        )
    pred, fit, bt = result["prediction"], result["fit"], result["backtest"]
    if pred.rival_games == 0:
        # Sin esto, un id mal escrito (o un nombre en vez de id) salía como
        # "equipo medio" con una predicción aparentemente normal.
        return fail(
            "sin datos",
            detail=f"El equipo {opponent_id} no tiene partidos en la temporada {season}: no hay nivel que estimarle.",
            suggestion="Comprueba el id con resolve_entity o prueba con la temporada anterior (season_id).",
        )
    rival_name = queries.team_name(ctx.engine, opponent_id) or opponent_id
    if min(pred.own_games, pred.rival_games) < _FEW_GAMES:
        warnings.append(
            f"Poca muestra ({pred.own_games} partidos nuestros, {pred.rival_games} del rival): el modelo los "
            "acerca a un equipo medio."
        )

    components = [
        {"piece": c["label"], "points": round(c["points"], 1), "detail": c["detail"]} for c in pred.components
    ]
    data = {
        "opponent_id": opponent_id,
        "opponent": rival_name,
        "match_date": match_date.isoformat(),
        "venue": result["venue"],
        "expected_margin": round(pred.expected_margin, 1),
        "win_probability": round(pred.win_probability, 3),
        "components": components,
        "explanation": prediction.explain_components(pred, rival_name),
        "summary": prediction.summary_sentence(pred, rival_name),
        "own_rating": round(pred.own_rating, 1),
        "rival_rating": round(pred.rival_rating, 1),
        "own_rest_days": pred.own_rest,
        "rival_rest_days": pred.rival_rest,
        "model": {
            "home_court_points": round(fit.home_court, 1),
            "rest_points_per_day": round(fit.rest_per_day, 2),
            "rest_effect_estimated": fit.rest_estimated,
            "sigma": round(fit.sigma, 1),
            "games_fitted": fit.n_games,
        },
        "backtest": None if bt is None else {
            "games": bt["n"],
            "mae_points": round(bt["mae"], 1),
            "winner_hit_rate": round(bt["hit_rate"], 3),
            "always_home_hit_rate": round(bt["home_hit_rate"], 3),
        },
    }
    return ok(
        data,
        source="games (margen final de todos los partidos de la temporada) + upcoming_matchups",
        scope=f"temporada {season} · partido del {match_date.isoformat()}",
        gp=fit.n_games,
        warnings=warnings,
        artifact=artifact("table", components, title=f"Qué mueve la predicción contra {rival_name}"),
    )
