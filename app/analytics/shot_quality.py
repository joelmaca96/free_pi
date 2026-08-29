"""Calidad de tiro (xPPS): separar la decisión de tirar del acierto al tirar.

Propuesta 02 (`doc/features/propuestas/02_calidad_de_tiro.md`), versión **v1
por zonas**: el valor esperado de un tiro es el PPS medio de la liga en SU
zona, no una estimación por coordenada. La v2 por posición (rejilla/hexbin
suavizado sobre `pos_x`/`pos_y`) está descrita en §4 de ese documento y NO
está implementada aquí — el documento la separa a propósito porque la v1 ya
contesta la pregunta del entrenador y se explica en diez segundos.

De un tiro salen dos números que hoy la estadística clásica funde en uno:

- `xPPS`: lo que ese tiro vale **para toda la liga desde esa posición**. Mide
  la DECISIÓN — qué tiro se genera.
- `PPS`: los puntos que de verdad sacó. Mide el ACIERTO.

Su diferencia, con signo, es lo único que separa "tiramos bien y no entró" de
"tiramos mal". Y en defensa es la única lectura honesta: el xPPS concedido no
premia que el rival falle tiros abiertos.

Tres decisiones que no son detalles:

- **El valor del tiro (2 o 3) no está en la base de datos** y se deriva del
  `zone_id`, sin ambigüedad, desde el reteselado de `court_zones`
  (`tools/retile_court_zones.py`): las siete zonas de `THREE_POINT_ZONE_IDS`
  valen 3, el resto 2. No se deduce de las coordenadas: la clasificación en
  zona ya se hizo en la ingesta contra la elipse real
  (`packages/baskonia_core/court_geometry.py`), y volver a decidirlo aquí
  sería una segunda geometría que puede divergir de la primera.
- **La referencia se separa por competición.** ACB y Euroliga no tienen el
  mismo nivel de tiro y mezclarlas contamina la línea base. Una celda
  (competición, zona) con menos de `MIN_BASELINE_SHOTS` tiros no se usa: se
  cae a la referencia agrupada de TODAS las competiciones para esa zona (así
  Copa del Rey y Supercopa, con menos de mil tiros cada una, no generan una
  referencia de veinte tiros con la que juzgar a nadie).
- **Nada se imputa.** Los tiros sin `zone_id` y los que la fuente no localiza
  (`located = 0`, hoy los mates de ACB) quedan FUERA del cálculo y se
  devuelven contados aparte (`coverage`) para poder declararlo en la
  interfaz. Ojo con la consecuencia, que es real y hay que decirla: como esos
  mates son casi todos ACB y entran a ~99% de acierto, el PPS que sale de
  aquí es el del tiro de campo *localizado*, algo por debajo del real, y algo
  más en ACB que en Euroliga.

`PPS − xPPS` con pocos tiros es ruido puro, así que **siempre** se encoge
hacia 0 con `n / (n + SHRINK_K)` y por debajo de `MIN_SHOTS` no se muestra:
se dice "muestra insuficiente". Es la diferencia entre una métrica que un
entrenador vuelve a mirar y una que le miente una vez y ya no vuelve.

Todo el módulo trabaja sobre **recuentos ya agregados** (`shots`/`made` por
competición y zona), no sobre tiros fila a fila: dentro de una zona el valor
esperado es constante, así que agregar antes no pierde ni un decimal y evita
arrastrar 92.000 filas hasta la interfaz. Las consultas que producen esos
recuentos están en `app/data/queries.py` (`league_shot_counts`,
`team_shot_counts`, `game_shot_counts`, `player_shot_counts`).
"""
from typing import Iterable, Optional, Sequence

import pandas as pd

#: Zonas de `court_zones` que valen 3 puntos (seed de `schema.sql` tras el
#: reteselado de 2026-08-27): esquinas (4, 5), triple exterior frontal (6),
#: triples de ala (8, 9) y las dos "Ala (3)" que `classify_zone` resuelve
#: contra la elipse real (15, 17). Las antiguas 2 y 3 ("Ala izq./der." sin
#: partir) ya no reciben tiros; si alguna llegara, cae en el `else` (2
#: puntos), que es lo que era antes de partirlas.
THREE_POINT_ZONE_IDS = frozenset({4, 5, 6, 8, 9, 15, 17})

#: Zonas que se funden en otra antes de calcular nada. "Línea de fondo" (13)
#: es una franja de 5 unidades pegada al fondo con 96 tiros en toda la liga y
#: un 79% de acierto: casi con seguridad tiros de aro mal situados. Con ese
#: volumen no es una zona, es un artefacto de teselado — se suma a "Pintura"
#: (1) en vez de publicar una referencia de liga hecha con 96 tiros.
ZONE_MERGES = {13: 1}

#: Tiros mínimos de una celda (competición, zona) para usarla como referencia
#: propia. Por debajo se usa la referencia agrupada de todas las competiciones
#: para esa zona.
MIN_BASELINE_SHOTS = 200

#: `competition_id` sintético de las filas de referencia agrupada (todas las
#: competiciones juntas). Negativo para no chocar nunca con un id real.
POOLED_COMPETITION_ID = -1

#: Peso de la regularización de `PPS − xPPS`: con `n` tiros la diferencia se
#: encoge hacia 0 por `n / (n + SHRINK_K)`. Del orden de 100 tiros (§4 del
#: documento): a 100 tiros se conserva la mitad, a 400 el 80%.
SHRINK_K = 100

#: Por debajo de estos tiros no se enseña `PPS − xPPS` en absoluto: se dice
#: "muestra insuficiente". Encoger no basta cuando el número de partida es
#: ruido.
MIN_SHOTS = 50

#: Columnas que tiene que traer cualquier recuento de entrada, venga de la
#: consulta que venga. Se comprueban en `split_usable`, que es la puerta de
#: todo el módulo: un `KeyError` a tres funciones de distancia no dice cuál de
#: las cuatro consultas se quedó corta.
COUNT_COLUMNS = ("competition_id", "zone_id", "located", "shots", "made")


def shot_value(zone_id) -> int:
    """Puntos que vale un tiro de `zone_id`: 3 en `THREE_POINT_ZONE_IDS`, 2 en el resto."""
    return 3 if zone_id in THREE_POINT_ZONE_IDS else 2


def _merged_zone_id(zone_id):
    return ZONE_MERGES.get(zone_id, zone_id)


def split_usable(counts: pd.DataFrame) -> "tuple[pd.DataFrame, dict]":
    """Separa los tiros que entran en el cálculo de los que se excluyen.

    Entra un tiro solo si tiene `zone_id` **y** la fuente lo localizó
    (`located = 1`). Lo demás no se imputa nunca: se cuenta y se devuelve
    para que la interfaz pueda declararlo (ver el docstring del módulo).

    Args:
        counts: recuentos con `COUNT_COLUMNS` (más las columnas de corte que
            traiga cada consulta: `team_id`, `player_id`...). `zone_label`
            es opcional; si viene, se conserva.

    Returns:
        `(usable, coverage)`. `usable` son los mismos recuentos filtrados,
        con `zone_id` ya fundido según `ZONE_MERGES`, reagrupado y sin la
        columna `located` (que ahí ya es constante). `coverage` es
        `{"total", "usable", "unlocated", "no_zone", "pct"}` — tiros, no
        filas.
    """
    missing = [column for column in COUNT_COLUMNS if column not in counts.columns]
    if missing:
        raise ValueError(f"faltan columnas en los recuentos de tiros: {', '.join(missing)}")
    if counts.empty:
        return counts.drop(columns=["located"]), {
            "total": 0, "usable": 0, "unlocated": 0, "no_zone": 0, "pct": 0.0,
        }

    located = counts["located"].fillna(1).astype(int) == 1
    has_zone = counts["zone_id"].notna()

    total = int(counts["shots"].sum())
    unlocated = int(counts.loc[~located, "shots"].sum())
    # Un tiro sin localizar Y sin zona se cuenta solo una vez, en `unlocated`:
    # las dos cifras suman exactamente los excluidos, sin solaparse.
    no_zone = int(counts.loc[located & ~has_zone, "shots"].sum())

    usable = counts.loc[located & has_zone].copy()
    usable["zone_id"] = usable["zone_id"].astype(int).map(_merged_zone_id)
    usable = _regroup(usable)

    usable_shots = int(usable["shots"].sum()) if not usable.empty else 0
    coverage = {
        "total": total,
        "usable": usable_shots,
        "unlocated": unlocated,
        "no_zone": no_zone,
        "pct": 100.0 * usable_shots / total if total else 0.0,
    }
    return usable, coverage


def _regroup(usable: pd.DataFrame) -> pd.DataFrame:
    """Reagrupa tras fundir zonas: `ZONE_MERGES` puede juntar dos filas en una.

    `zone_label` se recalcula desde el `zone_id` fundido (la etiqueta de la
    zona absorbida se pierde a propósito: 13 pasa a llamarse "Pintura",
    porque eso es lo que ahora es).
    """
    keys = [c for c in usable.columns if c not in ("shots", "made", "located", "zone_label")]
    labels = None
    if "zone_label" in usable.columns:
        labels = (
            usable.loc[~usable["zone_id"].isin(ZONE_MERGES), ["zone_id", "zone_label"]]
            .drop_duplicates("zone_id")
        )
    grouped = usable.groupby(keys, as_index=False, dropna=False)[["shots", "made"]].sum()
    if labels is not None:
        grouped = grouped.merge(labels, on="zone_id", how="left")
        grouped["zone_label"] = grouped["zone_label"].fillna(grouped["zone_id"].astype(str))
    return grouped


def league_baseline(counts: pd.DataFrame) -> pd.DataFrame:
    """Línea base de la liga: qué vale un tiro en cada (competición, zona).

    Args:
        counts: salida de `queries.league_shot_counts` — TODOS los tiros de
            la temporada, de todos los equipos y competiciones.

    Returns:
        `competition_id, zone_id, zone_label, shot_value, league_shots,
        league_made, league_fg_pct, league_pps, is_pooled`, con una fila por
        cada (competición, zona) observada **más** las filas agrupadas de
        `POOLED_COMPETITION_ID` (todas las competiciones juntas, una por
        zona). `is_pooled` marca las celdas que no llegaban a
        `MIN_BASELINE_SHOTS` y han tomado prestado el valor agrupado — es
        información que la interfaz debe poder enseñar, no un detalle
        interno. Vacío si no hay ningún tiro utilizable.
    """
    usable, _ = split_usable(counts)
    if usable.empty:
        return pd.DataFrame(
            columns=[
                "competition_id", "zone_id", "zone_label", "shot_value",
                "league_shots", "league_made", "league_fg_pct", "league_pps", "is_pooled",
            ]
        )

    by_zone = _with_rates(usable.groupby("zone_id", as_index=False)[["shots", "made"]].sum())
    by_zone["competition_id"] = POOLED_COMPETITION_ID

    by_cell = _with_rates(
        usable.groupby(["competition_id", "zone_id"], as_index=False)[["shots", "made"]].sum()
    )
    # Celda con poco volumen: se queda con la fila (para que la competición
    # siga teniendo entrada propia) pero con los ratios de la agrupada.
    thin = by_cell["shots"] < MIN_BASELINE_SHOTS
    pooled_rates = by_zone.set_index("zone_id")[["fg_pct", "pps"]]
    by_cell["is_pooled"] = thin
    for column in ("fg_pct", "pps"):
        by_cell.loc[thin, column] = by_cell.loc[thin, "zone_id"].map(pooled_rates[column]).values

    by_zone["is_pooled"] = True
    baseline = pd.concat([by_cell, by_zone], ignore_index=True)

    labels = _zone_labels(counts)
    baseline = baseline.rename(columns={"shots": "league_shots", "made": "league_made",
                                        "fg_pct": "league_fg_pct", "pps": "league_pps"})
    baseline["zone_label"] = baseline["zone_id"].map(labels).fillna(baseline["zone_id"].astype(str))
    return baseline[
        [
            "competition_id", "zone_id", "zone_label", "shot_value",
            "league_shots", "league_made", "league_fg_pct", "league_pps", "is_pooled",
        ]
    ]


def _with_rates(grouped: pd.DataFrame) -> pd.DataFrame:
    """Añade `shot_value`, `fg_pct` y `pps` a un agregado con `zone_id/shots/made`."""
    grouped = grouped.copy()
    grouped["shot_value"] = grouped["zone_id"].map(shot_value)
    grouped["fg_pct"] = 100.0 * grouped["made"] / grouped["shots"]
    grouped["pps"] = grouped["shot_value"] * grouped["made"] / grouped["shots"]
    return grouped


def _zone_labels(counts: pd.DataFrame) -> dict:
    if "zone_label" not in counts.columns:
        return {}
    labels = counts.loc[counts["zone_id"].notna() & counts["zone_label"].notna()]
    labels = labels.loc[~labels["zone_id"].astype(int).isin(ZONE_MERGES)]
    return dict(zip(labels["zone_id"].astype(int), labels["zone_label"]))


def with_expected(counts: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    """Cruza unos recuentos con la línea base y añade puntos reales y esperados.

    Args:
        counts: recuentos de un equipo, jugador o partido
            (`COUNT_COLUMNS` + las columnas de corte que traiga).
        baseline: salida de `league_baseline`.

    Returns:
        Los recuentos utilizables (`split_usable`) con `shot_value, points,
        xpoints, league_fg_pct, league_pps, is_pooled` añadidos. Una zona sin
        referencia de liga (no ha aparecido NUNCA en la temporada) se
        descarta con el resto de lo inservible en vez de valorarse a cero.
    """
    usable, _ = split_usable(counts)
    if usable.empty or baseline.empty:
        return usable.assign(shot_value=[], points=[], xpoints=[]) if usable.empty else usable.iloc[0:0]

    cells = baseline.loc[baseline["competition_id"] != POOLED_COMPETITION_ID]
    pooled = baseline.loc[baseline["competition_id"] == POOLED_COMPETITION_ID]

    merged = usable.merge(
        cells[["competition_id", "zone_id", "league_fg_pct", "league_pps", "is_pooled"]],
        on=["competition_id", "zone_id"],
        how="left",
    )
    # Competición que no aparece en la línea base (un amistoso suelto): la
    # zona sigue valiendo lo que vale en el resto de la liga.
    missing = merged["league_pps"].isna()
    if missing.any():
        fill = merged.loc[missing, "zone_id"].map(pooled.set_index("zone_id")["league_pps"])
        fill_fg = merged.loc[missing, "zone_id"].map(pooled.set_index("zone_id")["league_fg_pct"])
        merged.loc[missing, "league_pps"] = fill.values
        merged.loc[missing, "league_fg_pct"] = fill_fg.values
        merged.loc[missing, "is_pooled"] = True

    merged = merged.loc[merged["league_pps"].notna()].copy()
    merged["shot_value"] = merged["zone_id"].map(shot_value)
    merged["points"] = merged["shot_value"] * merged["made"]
    merged["xpoints"] = merged["league_pps"] * merged["shots"]
    return merged


def shrink(diff: float, shots: int, k: int = SHRINK_K) -> float:
    """Encoge `diff` hacia 0 con peso `n / (n + k)` — obligatorio, ver §4 del documento."""
    if shots <= 0:
        return 0.0
    return diff * shots / (shots + k)


def summarize(valued: pd.DataFrame) -> dict:
    """Resumen de calidad de tiro de un conjunto de tiros ya valorados.

    Args:
        valued: salida de `with_expected` (o cualquier subconjunto de filas
            suyo — filtrar por equipo o jugador y volver a resumir es válido).

    Returns:
        `{"shots", "made", "points", "pps", "xpps", "diff", "diff_shrunk",
        "fg_pct", "reliable"}`. `reliable` es `shots >= MIN_SHOTS`: con menos,
        `diff`/`diff_shrunk` siguen calculados pero **no deben enseñarse**
        (`verdict` ya lo respeta). Con cero tiros todo sale a 0 y
        `reliable=False`.
    """
    shots = int(valued["shots"].sum()) if not valued.empty else 0
    if shots == 0:
        return {"shots": 0, "made": 0, "points": 0.0, "pps": 0.0, "xpps": 0.0,
                "diff": 0.0, "diff_shrunk": 0.0, "fg_pct": 0.0, "reliable": False}

    made = int(valued["made"].sum())
    points = float(valued["points"].sum())
    xpoints = float(valued["xpoints"].sum())
    pps, xpps = points / shots, xpoints / shots
    diff = pps - xpps
    return {
        "shots": shots,
        "made": made,
        "points": points,
        "pps": pps,
        "xpps": xpps,
        "diff": diff,
        "diff_shrunk": shrink(diff, shots),
        "fg_pct": 100.0 * made / shots,
        "reliable": shots >= MIN_SHOTS,
    }


def summarize_by(valued: pd.DataFrame, keys: Sequence[str], extra: Iterable[str] = ()) -> pd.DataFrame:
    """`summarize` aplicado por grupo — una fila por jugador, equipo o zona.

    Args:
        valued: salida de `with_expected`.
        keys: columnas por las que agrupar (`["player_id"]`, `["team_id"]`...).
        extra: columnas descriptivas que viajan con el grupo sin agregarse
            (`player_name`); se toma el primer valor de cada grupo.

    Returns:
        Las claves más `shots, made, fg_pct, pps, xpps, diff, diff_shrunk,
        reliable`, de más a menos tiros. Vacío si `valued` lo está.
    """
    columns = list(keys) + list(extra) + [
        "shots", "made", "fg_pct", "pps", "xpps", "diff", "diff_shrunk", "reliable",
    ]
    if valued.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for key_values, group in valued.groupby(list(keys), as_index=False, dropna=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        row = dict(zip(keys, key_values))
        for column in extra:
            row[column] = group[column].iloc[0]
        summary = summarize(group)
        row.update({c: summary[c] for c in
                    ("shots", "made", "fg_pct", "pps", "xpps", "diff", "diff_shrunk", "reliable")})
        rows.append(row)
    return pd.DataFrame(rows, columns=columns).sort_values("shots", ascending=False).reset_index(drop=True)


def zone_profile(valued: pd.DataFrame) -> pd.DataFrame:
    """Perfil por zona listo para `components/court.py::zone_heatmap`.

    Devuelve las MISMAS columnas que `queries.team_zone_profile`
    (`zone_label, fg_pct, volume, made`) para poder pintarse con el mapa que
    ya existe, más las tres que hacen falta para el modo "vs. liga":
    `league_fg_pct` (el acierto de toda la liga en esa zona, ponderado por
    cómo reparte SUS tiros el equipo entre competiciones), `diff_pp` (la
    diferencia en puntos porcentuales, que es lo que colorea el mapa) y
    `league_pps`.

    `league_fg_pct` se pondera por volumen propio a propósito: un equipo que
    tira el 70% en Euroliga tiene que compararse sobre todo contra la
    referencia de Euroliga, no contra la media aritmética de las dos.
    """
    columns = ["zone_label", "zone_id", "volume", "made", "fg_pct",
               "league_fg_pct", "diff_pp", "pps", "league_pps", "diff_pps"]
    if valued.empty:
        return pd.DataFrame(columns=columns)

    grouped = valued.assign(
        _fg_weighted=valued["league_fg_pct"] * valued["shots"],
        _pps_weighted=valued["league_pps"] * valued["shots"],
    ).groupby(["zone_id", "zone_label"], as_index=False).agg(
        volume=("shots", "sum"),
        made=("made", "sum"),
        points=("points", "sum"),
        _fg_weighted=("_fg_weighted", "sum"),
        _pps_weighted=("_pps_weighted", "sum"),
    )
    grouped["fg_pct"] = 100.0 * grouped["made"] / grouped["volume"]
    grouped["league_fg_pct"] = grouped["_fg_weighted"] / grouped["volume"]
    grouped["pps"] = grouped["points"] / grouped["volume"]
    grouped["league_pps"] = grouped["_pps_weighted"] / grouped["volume"]
    grouped["diff_pp"] = grouped["fg_pct"] - grouped["league_fg_pct"]
    grouped["diff_pps"] = grouped["pps"] - grouped["league_pps"]
    return grouped[columns].sort_values("volume", ascending=False).reset_index(drop=True)


def league_zone_table(baseline: pd.DataFrame) -> pd.DataFrame:
    """La tabla de §3 del documento: qué vale cada zona en esta liga.

    Solo las filas agrupadas (`POOLED_COMPETITION_ID`), que son las que
    tienen sentido enseñar como "lo que vale un tiro aquí" — el desglose por
    competición está en `baseline`, pero como argumento de vestuario lo que
    vale es la lista única ordenada por PPS.

    Returns:
        `zone_label, shot_value, league_shots, league_fg_pct, league_pps`, de
        más a menos PPS. Vacío si `baseline` lo está.
    """
    columns = ["zone_label", "shot_value", "league_shots", "league_fg_pct", "league_pps"]
    if baseline.empty:
        return pd.DataFrame(columns=columns)
    pooled = baseline.loc[baseline["competition_id"] == POOLED_COMPETITION_ID]
    return pooled[columns].sort_values("league_pps", ascending=False).reset_index(drop=True)


def format_pps(value: float) -> str:
    """`1.037` -> `'1,04'` — coma decimal, dos decimales, como el resto de la interfaz."""
    return f"{value:.2f}".replace(".", ",")


def format_diff(value: float) -> str:
    """`-0.094` -> `'−0,09'` (con signo SIEMPRE: sin él la diferencia no se lee)."""
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(value):.2f}".replace(".", ",")


def verdict(
    summary: dict,
    reference_xpps: Optional[float] = None,
    subject: str = "el ataque",
    conceded: bool = False,
) -> str:
    """La frase honesta de §2 del documento, a partir de un `summarize`.

    Es el único sitio donde se decide qué se puede afirmar: por debajo de
    `MIN_SHOTS` tiros no dice nada del acierto, dice que no hay muestra. Que
    esta frase viva aquí y no en cada página es lo que garantiza que el
    partido, la temporada y el asistente digan lo mismo del mismo dato.

    Args:
        summary: salida de `summarize`.
        reference_xpps: xPPS de referencia con el que comparar la generación
            (típicamente la media de temporada del propio equipo). `None`
            para no compararla con nada.
        subject: sujeto de la frase ("el ataque", "la defensa", "el rival").
        conceded: `True` cuando los tiros resumidos son los del RIVAL (lectura
            defensiva). Cambia los verbos —una defensa no "genera" ni "saca"
            xPPS, lo concede y lo encaja— y atribuye el acierto a quien
            disparó. Con los verbos de ataque, la frase defensiva se lee justo
            al revés de lo que dice.

    Returns:
        Una frase en castellano, ya con los números formateados.
    """
    if summary["shots"] == 0:
        return "Sin tiros localizados y clasificados por zona: no hay calidad de tiro que medir."

    generate, obtain = ("concedió", "encajó") if conceded else ("generó", "sacó")
    shooter = "el rival" if conceded else None

    # `str.capitalize()` no: pone en minúscula el RESTO de la cadena, y un
    # sujeto con nombre propio ("la defensa de Unicaja") saldría destrozado.
    generated = f"{subject[0].upper()}{subject[1:]} {generate} {format_pps(summary['xpps'])} xPPS"
    if reference_xpps is not None:
        delta = summary["xpps"] - reference_xpps
        comparison = "en línea con" if abs(delta) < 0.02 else ("por encima de" if delta > 0 else "por debajo de")
        generated += f" ({comparison} lo habitual, {format_pps(reference_xpps)})"

    if not summary["reliable"]:
        return (
            f"{generated} y {obtain} {format_pps(summary['pps'])} PPS, pero con {summary['shots']} tiros "
            f"la diferencia todavía es ruido: hacen falta {MIN_SHOTS} para leerla."
        )

    diff = summary["diff_shrunk"]
    who = f"{shooter} " if shooter else ""
    if abs(diff) < 0.03:
        reading = f"{who}acertó lo que tocaba" if shooter else "el acierto fue el que tocaba"
    else:
        # Magnitud sin signo: "acertó −0,03 por debajo" es un doble negativo que
        # se lee al revés de lo que dice. El signo lo pone la preposición.
        side = "por encima" if diff > 0 else "por debajo"
        reading = f"{who}acertó {format_pps(abs(diff))} {side} de lo que valían esos tiros"
    return f"{generated} y {obtain} {format_pps(summary['pps'])} PPS: {reading}."


def coverage_caption(coverage: dict) -> str:
    """Pie de gráfico que declara qué tiros se han quedado fuera y por qué.

    Nunca se omite aunque la cobertura sea alta: la exclusión de los mates
    sin localizar sesga el PPS hacia abajo (y más en ACB que en Euroliga,
    ver el docstring del módulo), y eso hay que poder verlo sin preguntar.
    """
    if coverage["total"] == 0:
        return "Sin tiros registrados."
    pieces = [
        f"{coverage['usable']} de {coverage['total']} tiros ({coverage['pct']:.0f}%) "
        "entran en el cálculo: localizados y con zona."
    ]
    excluded = []
    if coverage["unlocated"]:
        excluded.append(f"{coverage['unlocated']} sin ubicación de la fuente (mates de ACB)")
    if coverage["no_zone"]:
        excluded.append(f"{coverage['no_zone']} sin zona asignada")
    if excluded:
        pieces.append(
            f"Quedan fuera {' y '.join(excluded)} — no se imputan, así que el PPS real "
            "(que sí los incluiría) es algo mayor que el de aquí."
        )
    return " ".join(pieces)


#: Aviso permanente de qué mide y qué NO mide esta métrica (§5 del
#: documento). No es una nota al pie: sin defensor ni reloj en `shots`, esto
#: es calidad de LOCALIZACIÓN, y presentarla como "calidad de tiro" a secas
#: es prometer algo que los datos no sostienen.
QUALITY_CAVEAT = (
    "Calidad de **localización**, no de tiro completa: `shots` no guarda defensor ni distancia "
    "al defensor, así que un triple de esquina abierto y otro con la mano en la cara valen igual "
    "aquí. Tampoco hay reloj en los tiros, así que no hay xPPS \"de los últimos cinco minutos\". "
    "Tiros libres aparte: no están en `shots`."
)

