"""Herramienta de momentos clave de un partido (propuesta 18, hoja de ruta A4).

`game_key_moments` contesta "¿qué jugadas decidieron el partido?" con la
MISMA lógica que la pestaña "Momentos clave" de "Partidos anteriores"
(`queries_win_probability.game_key_moments`): los tramos ordenados por cuánto
movieron la probabilidad de victoria —no por el tamaño del parcial, que es lo
que hace `game_runs`—, el quinteto de cada equipo en pista y la WPA por
quinteto. Dos definiciones distintas de "momento clave" en el chat y en la
pantalla es la forma más rápida de que se contradigan delante del entrenador.
"""
import pandas as pd

from .base import ToolContext, artifact, clean_dict, fail, ok, records, register, schema

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics.win_probability import NO_LINEUP
    from app.data import queries_assistant, queries_win_probability
except ImportError:  # pragma: no cover
    from analytics.win_probability import NO_LINEUP
    from data import queries_assistant, queries_win_probability

#: Quintetos por equipo que viajan al modelo (los de más y menos WPA).
_LINEUPS_EACH_SIDE = 3


def _pp(value) -> float:
    """Probabilidad (0-1) -> puntos porcentuales con un decimal, para citar."""
    return round(100.0 * float(value), 1)


def _lineup_rows(df) -> list:
    """Los mejores y los peores quintetos por WPA, sin repetir si hay pocos."""
    if df is None or df.empty:
        return []
    head = df.head(_LINEUPS_EACH_SIDE)
    tail = df.tail(_LINEUPS_EACH_SIDE)
    picked = pd.concat([head, tail[~tail.index.isin(head.index)]])
    return [
        {
            "quinteto": row["lineup"],
            "minutos": round(float(row["minutes"]), 1),
            "mas_menos": int(row["points_for"]) - int(row["points_against"]),
            "wpa_pp": _pp(row["wpa"]),
        }
        for row in picked.to_dict("records")
    ]


@register(
    "game_key_moments",
    family="team",
    description=(
        "Las jugadas que DECIDIERON un partido: los tramos que más movieron la probabilidad de "
        "victoria (modelo de liga por margen y tiempo restante), con marcador antes/después, "
        "quinteto de cada equipo en pista y WPA por quinteto. Para '¿qué decidió el partido?' o "
        "'¿qué quinteto nos hizo ganar/perder?'. Un 5-0 al final pesa más que un 10-0 al principio: "
        "para los parciales más grandes por puntos usa game_runs. Necesita game_id (resolve_game)."
    ),
    parameters=schema(
        {
            "game_id": {"type": "string"},
            "team_id": {
                "type": "string",
                "description": "Desde qué equipo se mira (por defecto, el equipo propio).",
            },
            "limit": {"type": "integer", "description": "Cuántos momentos (5 por defecto, máximo 10)."},
            "window_minutes": {
                "type": "number",
                "description": "Duración máxima de un momento en minutos (2 por defecto).",
            },
        },
        required=["game_id"],
    ),
    artifact="table",
    requires="play_events",
)
def game_key_moments(
    ctx: ToolContext,
    game_id: str,
    team_id: str = None,
    limit: int = 5,
    window_minutes: float = 2.0,
) -> dict:
    """Momentos clave, quintetos y WPA de un partido — la misma lógica que la pantalla."""
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
    team = team_id or (ctx.own_team_id if ctx.own_team_id in sides else sides[0])
    n = min(max(int(limit), 1), 10)

    bundle = queries_win_probability.game_key_moments(
        ctx.engine, game_id, team, n=n, window_s=float(window_minutes) * 60.0
    )
    if bundle is None or bundle["curve"].empty:
        return fail(
            "sin play-by-play",
            detail=f"El partido {game_id} no tiene marcador con reloj (play_events): no hay curva de probabilidad.",
            suggestion="Usa game_boxscore para el resumen del partido.",
        )
    moments = bundle["moments"]
    if moments.empty:
        return fail(
            "sin momentos clave",
            detail=f"Ninguna ventana de {window_minutes:g} minutos o menos movió la probabilidad de victoria.",
            suggestion="Sube window_minutes o usa game_runs para ver los parciales por puntos.",
        )

    curve, model = bundle["curve"], bundle["model"]
    detailed = []
    for row in moments.to_dict("records"):
        detailed.append({
            "orden": int(row["rank"]),
            "desde": f"{row['quarter_start']} {row['clock_start']}",
            "hasta": f"{row['quarter_end']} {row['clock_end']}",
            "marcador_antes": row["score_before"],
            "marcador_despues": row["score_after"],
            "parcial": f"{int(row['points_for'])}-{int(row['points_against'])}",
            "prob_antes_pct": _pp(row["wp_before"]),
            "prob_despues_pct": _pp(row["wp_after"]),
            "wpa_pp": _pp(row["wpa"]),
            "quinteto_propio": row.get("lineup_own"),
            "quinteto_rival": row.get("lineup_rival"),
        })

    warnings = [
        "La probabilidad de victoria sale SOLO de margen, tiempo restante y campo (modelo de liga): no "
        "sabe quién tiene la posesión ni cómo de bueno es cada equipo. Cítala como estimación.",
        "Los tiros no tienen reloj en la fuente: el salto del marcador se ve en el siguiente evento "
        "tipado, así que la canasta real cae un poco antes del inicio indicado.",
    ]
    if model.kind != "logistic":
        warnings.append(
            "Modelo de reserva (aproximación normal): la temporada tiene pocos partidos con "
            "play-by-play para ajustar la logística. Las probabilidades son orientativas."
        )
    if not (bundle["lineups_own"]["lineup"] != NO_LINEUP).any():
        warnings.append(f"Sin tramos de quinteto de {team} en este partido: no hay WPA por quinteto.")

    return ok(
        {
            "game": clean_dict(header),
            "team_id": team,
            "prob_inicial_pct": _pp(curve["wp"].iloc[0]),
            "prob_final_pct": _pp(curve["wp"].iloc[-1]),
            "momentos": detailed,
            "wpa_quintetos_propios": _lineup_rows(bundle["lineups_own"]),
            "wpa_quintetos_rival": _lineup_rows(bundle["lineups_rival"]),
            "modelo": model.description,
        },
        source="play_events + lineup_stints + modelo de probabilidad de victoria de la liga",
        scope=f"{game_id} · {header['game_date']} · visto desde {team} · ventana {window_minutes:g} min",
        gp=1,
        warnings=warnings,
        artifact=artifact("table", records(moments.drop(columns=["label"])), title="Momentos clave del partido"),
    )
