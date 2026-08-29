"""Herramientas de calidad de tiro (xPPS) — propuesta 02.

Es la familia que contesta "¿por qué perdimos?" mejor que cualquier tabla,
porque devuelve los dos números que el %TC funde en uno: lo que valían los
tiros que se generaron (`xPPS`, la decisión) y los puntos que sacaron (`PPS`,
el acierto). Con eso el modelo puede decir "el ataque funcionó, el tiro no
cayó" y sostenerlo, en vez de deducirlo del porcentaje.

Dos cosas que hace el código y NO el modelo, a propósito (mismo criterio que
`team_style` con sus umbrales, §6.2):

- **La frase de veredicto** (`shot_quality.verdict`) viaja hecha en `data`.
  El modelo la explica y la contextualiza; no decide él si un −0,09 es mucho
  o poco, ni redacta su propia versión de lo mismo con otro umbral.
- **El corte de muestra insuficiente.** Por debajo de `MIN_SHOTS` tiros la
  diferencia no se devuelve como número que el modelo pueda citar: se
  devuelve `null` y el aviso correspondiente. Una métrica que miente una vez
  ya no vuelve a mirarse.
"""
from .base import ToolContext, artifact, fail, ok, records, register, schema, season_with_fallback

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import shot_quality as sq
    from app.data import queries
except ImportError:  # pragma: no cover
    from analytics import shot_quality as sq
    from data import queries

_FAMILY = "shot_quality"

#: Aviso permanente de qué mide esto (§5 del documento). Sin defensor ni reloj
#: en `shots`, es calidad de LOCALIZACIÓN; presentarla como "calidad de tiro"
#: a secas prometería algo que los datos no sostienen.
_CAVEAT = (
    "Es calidad de LOCALIZACIÓN, no de tiro completa: no hay defensor ni distancia al defensor "
    "en los datos, así que un triple de esquina abierto y otro muy defendido valen igual aquí. "
    "Tampoco hay reloj en los tiros (no hay xPPS de los últimos cinco minutos) y los tiros libres "
    "quedan fuera. Dilo si la respuesta se apoya en esta métrica."
)


def _season(ctx: ToolContext, season_id) -> int:
    return ctx.season_id if season_id is None else int(season_id)


def _team_season(ctx: ToolContext, team_id: str, season_id):
    if season_id is not None:
        return int(season_id), None
    return season_with_fallback(queries.team_scouting_season, ctx.engine, team_id, ctx.season_id)


def _baseline(ctx: ToolContext, season: int):
    return sq.league_baseline(queries.league_shot_counts(ctx.engine, season))


def _payload(summary: dict, *, reference_xpps=None, subject: str, conceded: bool = False) -> dict:
    """Resumen -> dict para el modelo, con la diferencia CENSURADA si no hay muestra.

    `diff` sale `null` por debajo de `MIN_SHOTS` en vez de salir como número
    pequeño: si viaja el número, el modelo lo cita, y da igual lo que diga el
    aviso de al lado.
    """
    return {
        "shots": summary["shots"],
        "xpps": round(summary["xpps"], 3),
        "pps": round(summary["pps"], 3),
        "fg_pct": round(summary["fg_pct"], 1),
        "diff": round(summary["diff_shrunk"], 3) if summary["reliable"] else None,
        "enough_sample": summary["reliable"],
        "verdict": sq.verdict(
            summary, reference_xpps=reference_xpps, subject=subject, conceded=conceded
        ),
    }


@register(
    "shot_quality_game",
    family=_FAMILY,
    description=(
        "Calidad de tiro de UN partido, los dos equipos: xPPS generado (decisión) contra PPS real "
        "(acierto). Es la herramienta de '¿por qué perdimos?' y de 'tiramos mal o no entró'. "
        "Para el mapa de tiros usa player_shot_chart/team_shot_chart; esto son los números."
    ),
    parameters=schema({"game_id": {"type": "string"}}, required=["game_id"]),
)
def shot_quality_game(ctx: ToolContext, game_id: str) -> dict:
    counts = queries.game_shot_counts(ctx.engine, game_id)
    if counts.empty:
        return fail(
            "sin tiros",
            detail=f"El partido {game_id} no tiene tiros cargados.",
            suggestion="Prueba con otro partido, o con team_zone_profile para el agregado de temporada.",
        )

    # La liga con la que se compara es la de la temporada DEL PARTIDO, no la
    # seleccionada en la app: el modelo describe la liga de esa temporada.
    season = int(counts["season_id"].iloc[0])
    baseline = _baseline(ctx, season)
    valued = sq.with_expected(counts, baseline)
    if valued.empty:
        return fail(
            "sin tiros utilizables",
            detail=f"Ningún tiro del partido {game_id} está a la vez localizado y clasificado por zona.",
        )

    teams = sq.summarize_by(valued, ["team_id"])
    data = {
        row["team_id"]: _payload(
            {**row, "reliable": bool(row["reliable"])},
            subject=f"el ataque de {row['team_id']}",
        )
        for row in teams.to_dict("records")
    }
    _, coverage = sq.split_usable(counts)
    return ok(
        data,
        source="shots + court_zones (línea base de liga de la temporada)",
        scope=f"partido {game_id}",
        gp=1,
        warnings=[_CAVEAT, sq.coverage_caption(coverage)],
    )


@register(
    "shot_quality_team",
    family=_FAMILY,
    description=(
        "Calidad de tiro de un equipo en la temporada, en ataque Y en defensa (xPPS concedido). "
        "El xPPS concedido es la métrica defensiva honesta: no premia que el rival falle tiros "
        "abiertos, a diferencia del %TC en contra."
    ),
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
    artifact="bar",
)
def shot_quality_team(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    counts = queries.team_shot_counts(ctx.engine, team_id, season)
    conceded_counts = queries.team_shot_counts(ctx.engine, team_id, season, conceded=True)
    if counts.empty and conceded_counts.empty:
        return fail("sin tiros", detail=f"No hay tiros de {team_id} en la temporada {season}.")

    baseline = _baseline(ctx, season)
    offense = sq.summarize(sq.with_expected(counts, baseline))
    defense = sq.summarize(sq.with_expected(conceded_counts, baseline))
    zones = sq.zone_profile(sq.with_expected(counts, baseline))

    warnings = [_CAVEAT]
    _, coverage = sq.split_usable(counts)
    warnings.append(sq.coverage_caption(coverage))
    if fallback_warning:
        warnings.append(fallback_warning)

    return ok(
        {
            "offense": _payload(offense, subject="el ataque"),
            "defense": _payload(defense, subject="la defensa", conceded=True),
            "zones_vs_league": records(
                zones.rename(columns={"diff_pp": "diff_vs_league_pp"})[
                    ["zone_label", "volume", "fg_pct", "league_fg_pct", "diff_vs_league_pp", "pps", "league_pps"]
                ]
            ),
        },
        source="shots + court_zones (línea base de liga de la temporada)",
        scope=f"temporada {season}",
        warnings=warnings,
        artifact=artifact("bar", records(zones), title="Tiro por zona contra la liga"),
    )


@register(
    "shot_quality_players",
    family=_FAMILY,
    description=(
        "Ranking de una plantilla por calidad de tiro: xPPS (quién ELIGE bien) y PPS − xPPS "
        "(quién ACIERTA por encima de lo que valen sus tiros). Son dos habilidades distintas; "
        "no las mezcles en una sola frase sobre el porcentaje."
    ),
    parameters=schema({"team_id": {"type": "string"}, "season_id": {"type": "integer"}}, required=["team_id"]),
)
def shot_quality_players(ctx: ToolContext, team_id: str, season_id: int = None) -> dict:
    season, fallback_warning = _team_season(ctx, team_id, season_id)
    counts = queries.player_shot_counts(ctx.engine, team_id, season)
    if counts.empty:
        return fail("sin tiros", detail=f"No hay tiros de jugadores de {team_id} en la temporada {season}.")

    valued = sq.with_expected(counts, _baseline(ctx, season))
    if valued.empty:
        return fail(
            "sin tiros utilizables",
            detail=f"Ningún tiro de {team_id} en la temporada {season} está localizado y con zona.",
        )

    ranking = sq.summarize_by(valued, ["player_id"], extra=["player_name"]).sort_values(
        "xpps", ascending=False
    )
    # Igual que en `_payload`: sin muestra no viaja el número, viaja el hueco.
    ranking.loc[~ranking["reliable"], "diff_shrunk"] = None
    # Solo viaja la diferencia REGULARIZADA, y con el nombre corto: mandar las
    # dos (`diff` cruda y `diff_shrunk`) es invitar al modelo a citar la que no
    # toca, que además es siempre la más llamativa.
    rows = records(
        ranking.drop(columns=["diff"]).rename(columns={"diff_shrunk": "diff"})[
            ["player_name", "shots", "xpps", "pps", "diff", "fg_pct", "reliable"]
        ]
    )
    warnings = [
        _CAVEAT,
        f"`diff` en null = menos de {sq.MIN_SHOTS} tiros: muestra insuficiente, no cero. Dilo así.",
    ]
    if fallback_warning:
        warnings.append(fallback_warning)
    return ok(
        rows,
        source="shots + court_zones (línea base de liga de la temporada)",
        scope=f"temporada {season}",
        warnings=warnings,
        truncated_from=len(ranking) if len(ranking) > len(rows) else None,
    )


@register(
    "shot_quality_league",
    family=_FAMILY,
    description=(
        "Qué vale un tiro desde cada zona en ESTA liga y esta temporada (PPS medio), de más a "
        "menos. Es la referencia con la que se calcula todo el xPPS; úsala para justificar por "
        "qué un tiro es bueno o malo, no para hablar de un equipo concreto."
    ),
    parameters=schema({"season_id": {"type": "integer"}}),
)
def shot_quality_league(ctx: ToolContext, season_id: int = None) -> dict:
    season = _season(ctx, season_id)
    baseline = _baseline(ctx, season)
    table = sq.league_zone_table(baseline)
    if table.empty:
        return fail("sin datos", detail=f"No hay tiros clasificados por zona en la temporada {season}.")
    return ok(
        records(table),
        source="shots + court_zones (todos los equipos y competiciones)",
        scope=f"temporada {season}",
        warnings=[
            "El valor del tiro (2 o 3) no está guardado en la base de datos: se deriva de la zona.",
            "La referencia real se separa por competición (ACB y Euroliga tiran distinto); esta "
            "tabla es la agrupada, que es la que se enseña.",
        ],
    )
