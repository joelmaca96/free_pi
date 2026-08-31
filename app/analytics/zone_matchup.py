"""Dónde castigar al rival: cruzar lo que concede una defensa con lo que produce un ataque, por zona.

Propuesta 08 (`doc/features/propuestas/08_donde_castigar_al_rival.md`). La
pregunta del entrenador no es "¿desde dónde tira el rival?" (eso ya lo enseña
el mapa de tiros de `proximo_rival`), es la cruzada: "¿desde dónde nos van a
dejar tirar, y coincide eso con lo que nosotros metemos?". Dos perfiles —lo
que el rival concede por zona y lo que nosotros producimos por zona— cruzados
dan una lista corta y accionable, no una estadística más.

**La fuente es `game_zone_stats`** (vía `queries.team_zone_profile*`), no
`shots` tiro a tiro: `shots` solo trae `player_id`, y para saber de qué
equipo era el tirador hay que pasar por `players.team_id` — el equipo
ACTUAL del jugador, que un traspaso a mitad de temporada dejaría apuntando
al equipo equivocado en partidos anteriores. `game_zone_stats` ya trae el
equipo resuelto partido a partido (§3 del documento).

Este módulo reutiliza a propósito los "números que hacen que shot_quality sea
honesto" en vez de reinventarlos con otra constante al lado que pueda
divergir: `shot_value` (2 o 3 según `zone_id`), `ZONE_MERGES` (Línea de fondo
se funde en Pintura) y `POOLED_COMPETITION_ID` vienen de
`app/analytics/shot_quality.py`. Lo que SÍ es propio de aquí es la
regularización de un `fg_pct` de EQUIPO por zona (más agresiva que la de
`shots` por jugador — ver `SHRINK_K`/`MIN_BASELINE_SHOTS`/`MIN_SHOTS`, todos
del orden de 100-200 tiros por lo pequeña que es la muestra por equipo y
zona, §4 del documento) y el cálculo del VALOR de una zona en puntos por
partido, que es lo que ordena la lista corta.

Es lógica pura sobre `pandas`, sin Streamlit ni SQLAlchemy — igual que
`shot_quality.py` y por el mismo motivo: se puede probar sin base de datos.
"""
from typing import Optional

import pandas as pd

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics.shot_quality import POOLED_COMPETITION_ID, ZONE_MERGES, shot_value
except ImportError:  # pragma: no cover
    from analytics.shot_quality import POOLED_COMPETITION_ID, ZONE_MERGES, shot_value

#: Tiros mínimos de una celda (competición, zona) de la REFERENCIA DE LIGA
#: para usarla como propia; por debajo, se cae a la agrupada de todas las
#: competiciones para esa zona. Más alto que el de `shot_quality`
#: (`MIN_BASELINE_SHOTS=200` también ahí, no es casualidad: la muestra por
#: equipo y zona es mucho más pequeña que la de la liga entera, así que la
#: referencia con la que se compara tiene que ser sólida).
MIN_BASELINE_SHOTS = 200

#: Peso de la regularización de `diff_pp` (acierto de EQUIPO por zona contra
#: la liga): con `n` tiros se encoge hacia 0 por `n / (n + SHRINK_K)`. A 200
#: tiros —la zona más discreta del ejemplo real del documento— se conserva la
#: mitad de la diferencia observada.
SHRINK_K = 200

#: Por debajo de estos tiros una zona no entra en la lista de "a atacar" / "a
#: cuidar": con menos, el error típico del % (±3,5 pp a 200 tiros) es del
#: orden de la señal que se busca (§4 del documento). No oculta el mapa de
#: calor —ahí la propia regularización ya aplana lo poco fiable—, solo la
#: lista corta y accionable.
MIN_SHOTS = 100

#: Columnas que debe traer cualquier perfil de equipo por (competición, zona)
#: que entre a este módulo, vengan de `queries.team_zone_profile_by_competition`
#: o de una prueba. Igual que `shot_quality.COUNT_COLUMNS`: falla en la puerta
#: con un mensaje claro, no tres funciones más adentro con un `KeyError`.
TEAM_PROFILE_COLUMNS = ("competition_id", "zone_id", "zone_label", "fg_pct", "volume")

#: Columnas de la referencia de liga cruda (`queries.league_zone_baseline_counts`).
LEAGUE_COLUMNS = ("competition_id", "zone_id", "zone_label", "fg_pct", "volume")


def _check_columns(df: pd.DataFrame, required: "tuple[str, ...]", name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"faltan columnas en {name}: {', '.join(missing)}")


def _with_made(df: pd.DataFrame) -> pd.DataFrame:
    """Añade `made` si no viene ya (`fg_pct * volume / 100`, sin redondear todavía)."""
    if "made" in df.columns:
        return df
    return df.assign(made=df["fg_pct"] / 100.0 * df["volume"])


def _merge_zones(df: pd.DataFrame, keys: "tuple[str, ...]") -> pd.DataFrame:
    """Funde `ZONE_MERGES` (hoy solo 13 -> 1, "Línea de fondo" en "Pintura").

    Igual criterio que `shot_quality._regroup`: recalcula `fg_pct` desde
    `made`/`volume` ya sumados (nunca se promedian dos porcentajes
    directamente) y la etiqueta de la zona fundida se pierde a propósito —
    13 pasa a llamarse "Pintura", que es lo que ahora es.

    Args:
        keys: columnas de agrupación DESPUÉS de fundir `zone_id` (normalmente
            `("competition_id", "zone_id")` o solo `("zone_id",)`).
    """
    if df.empty:
        return df
    merged = _with_made(df).copy()
    original_zone_id = merged["zone_id"].astype(int)
    # OJO con el orden aquí: hay que decidir qué filas "eran" una zona fundida
    # ANTES de remapear la columna, no después — una vez remapeada, una fila
    # que era zona 13 tiene `zone_id == 1` como cualquier "Pintura" de verdad,
    # y `isin(ZONE_MERGES)` sobre la columna YA remapeada no encontraría
    # ningún 13 que excluir (el filtro sería un no-op silencioso, y qué
    # etiqueta gana para la zona 1 pasaría a depender del orden de las filas).
    was_merged = original_zone_id.isin(ZONE_MERGES)
    merged["zone_id"] = original_zone_id.map(lambda z: ZONE_MERGES.get(z, z))
    labels = merged.loc[~was_merged, ["zone_id", "zone_label"]].drop_duplicates("zone_id")
    grouped = merged.groupby(list(keys), as_index=False)[["volume", "made"]].sum()
    grouped = grouped.merge(labels, on="zone_id", how="left")
    grouped["zone_label"] = grouped["zone_label"].fillna(grouped["zone_id"].astype(str))
    grouped["fg_pct"] = 100.0 * grouped["made"] / grouped["volume"]
    return grouped


def league_baseline(raw: pd.DataFrame) -> pd.DataFrame:
    """Línea base de liga por (competición, zona): qué concede/produce TODA la liga ahí.

    Args:
        raw: salida de `queries.league_zone_baseline_counts` — TODOS los
            equipos de la temporada, ya sumados por (competición, zona).

    Returns:
        `competition_id, zone_id, zone_label, league_fg_pct, league_volume,
        is_pooled`, con una fila por (competición, zona) observada MÁS las
        filas agrupadas de `POOLED_COMPETITION_ID` (todas las competiciones
        juntas, una por zona). `is_pooled` marca las celdas por debajo de
        `MIN_BASELINE_SHOTS` que han tomado prestado el valor agrupado.
        Vacío si `raw` lo está.
    """
    columns = ["competition_id", "zone_id", "zone_label", "league_fg_pct", "league_volume", "is_pooled"]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    _check_columns(raw, LEAGUE_COLUMNS, "league_zone_baseline_counts")

    merged = _merge_zones(raw, ("competition_id", "zone_id"))

    by_zone = merged.groupby("zone_id", as_index=False)[["volume", "made"]].sum()
    by_zone["fg_pct"] = 100.0 * by_zone["made"] / by_zone["volume"]
    by_zone["competition_id"] = POOLED_COMPETITION_ID
    by_zone["is_pooled"] = True

    by_cell = merged.copy()
    thin = by_cell["volume"] < MIN_BASELINE_SHOTS
    pooled_rates = by_zone.set_index("zone_id")["fg_pct"]
    by_cell["is_pooled"] = thin
    by_cell.loc[thin, "fg_pct"] = by_cell.loc[thin, "zone_id"].map(pooled_rates).values

    baseline = pd.concat([by_cell, by_zone], ignore_index=True)
    labels = merged.drop_duplicates("zone_id").set_index("zone_id")["zone_label"]
    baseline["zone_label"] = baseline["zone_id"].map(labels).fillna(baseline["zone_id"].astype(str))
    baseline = baseline.rename(columns={"fg_pct": "league_fg_pct", "volume": "league_volume"})
    return baseline[columns]


def team_profile(raw_by_competition: pd.DataFrame) -> pd.DataFrame:
    """Perfil de un equipo por (competición, zona) listo para cruzar con `league_baseline`.

    Solo funde zonas (§ `_merge_zones`) — no hace falta más: `queries.
    team_zone_profile_by_competition` ya trae `fg_pct`/`volume`/`made` bien
    calculados (ponderados por volumen dentro de cada competición y zona).

    Args:
        raw_by_competition: salida de `queries.team_zone_profile_by_competition`.

    Returns:
        `competition_id, zone_id, zone_label, fg_pct, volume, made`. Vacío si
        `raw_by_competition` lo está.
    """
    columns = ["competition_id", "zone_id", "zone_label", "fg_pct", "volume", "made"]
    if raw_by_competition.empty:
        return pd.DataFrame(columns=columns)
    _check_columns(raw_by_competition, TEAM_PROFILE_COLUMNS, "team_zone_profile_by_competition")
    return _merge_zones(raw_by_competition, ("competition_id", "zone_id"))[columns]


def zone_diff_profile(team_by_competition: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    """Cruza el perfil de un equipo con la línea base de liga y agrega a nivel de ZONA.

    Mismo patrón que `shot_quality.zone_profile`: `league_fg_pct` se pondera
    por CÓMO REPARTE EL PROPIO EQUIPO sus tiros entre competiciones, no por la
    media aritmética de las dos — un equipo que concede el 75% de sus tiros
    en Euroliga tiene que compararse sobre todo contra la referencia de
    Euroliga. Las columnas de salida son literalmente las que ya sabe pintar
    `components/court.py::zone_heatmap(mode="vs_league")`.

    Args:
        team_by_competition: salida de `team_profile`.
        baseline: salida de `league_baseline` (de la MISMA temporada que
            `team_by_competition` — no se comprueba aquí, es responsabilidad
            de quien pide las dos consultas).

    Returns:
        `zone_id, zone_label, volume, made, fg_pct, league_fg_pct, diff_pp,
        diff_pp_shrunk`, de más a menos volumen. Una competición del equipo
        sin fila propia en `baseline` (amistoso suelto) cae a la agrupada.
        Vacío si `team_by_competition` o `baseline` lo están.
    """
    columns = ["zone_id", "zone_label", "volume", "made", "fg_pct", "league_fg_pct", "diff_pp", "diff_pp_shrunk"]
    if team_by_competition.empty or baseline.empty:
        return pd.DataFrame(columns=columns)

    cells = baseline.loc[baseline["competition_id"] != POOLED_COMPETITION_ID]
    pooled = baseline.loc[baseline["competition_id"] == POOLED_COMPETITION_ID].set_index("zone_id")["league_fg_pct"]

    merged = team_by_competition.merge(
        cells[["competition_id", "zone_id", "league_fg_pct"]],
        on=["competition_id", "zone_id"],
        how="left",
    )
    missing = merged["league_fg_pct"].isna()
    if missing.any():
        merged.loc[missing, "league_fg_pct"] = merged.loc[missing, "zone_id"].map(pooled).values
    merged = merged.loc[merged["league_fg_pct"].notna()].copy()
    if merged.empty:
        return pd.DataFrame(columns=columns)

    merged["_fg_weighted"] = merged["league_fg_pct"] * merged["volume"]
    grouped = merged.groupby(["zone_id", "zone_label"], as_index=False).agg(
        volume=("volume", "sum"), made=("made", "sum"), _fg_weighted=("_fg_weighted", "sum"),
    )
    grouped["fg_pct"] = 100.0 * grouped["made"] / grouped["volume"]
    grouped["league_fg_pct"] = grouped["_fg_weighted"] / grouped["volume"]
    grouped["diff_pp"] = grouped["fg_pct"] - grouped["league_fg_pct"]
    grouped["diff_pp_shrunk"] = grouped["diff_pp"] * grouped["volume"] / (grouped["volume"] + SHRINK_K)
    return grouped[columns].sort_values("volume", ascending=False).reset_index(drop=True)


def attack_targets(
    concede_diff: pd.DataFrame,
    produce_diff: pd.DataFrame,
    games_played: int,
    top_n: Optional[int] = 3,
    min_shots: int = MIN_SHOTS,
) -> pd.DataFrame:
    """La lista corta de §2b del documento: zonas donde conviene atacar (o defenderse).

    Sirve para las DOS mitades del plan con los mismos argumentos en otro
    orden (§2c): pasando el perfil defensivo del rival como `concede_diff` y
    el ofensivo propio como `produce_diff` sale "dónde atacar"; al revés
    —el defensivo propio como `concede_diff` y el ofensivo del rival como
    `produce_diff`, con los partidos jugados PROPIOS— sale "dónde nos van a
    castigar".

    Una zona entra si SU LADO QUE CONCEDE está por encima de la liga
    (`diff_pp_shrunk > 0`) Y su lado que produce también (misma condición) —
    coincidir en debilidad/fortaleza es lo que la convierte en un plan, no en
    una curiosidad. Se ordena por **puntos por partido que se pueden ganar**,
    no por diferencia de porcentaje (§2b): una zona con +8 pp pero 3 tiros
    por partido vale menos que otra con +3 pp y 15.

    Args:
        concede_diff: salida de `zone_diff_profile` del lado que CONCEDE la
            zona (la defensa que se va a atacar, o la propia si se calcula el
            reverso).
        produce_diff: salida de `zone_diff_profile` del lado que PRODUCE en
            esa zona (el ataque propio, o el del rival en el reverso).
        games_played: partidos jugados por el equipo de `concede_diff` en la
            temporada (`queries.team_games_played`) — convierte el volumen de
            la zona en tiros por partido, que es la unidad de la que sale el
            valor en puntos.
        top_n: cuántas filas devolver (de más a menos valor). `None` para
            todas las que superen el filtro.
        min_shots: tiros mínimos en CADA lado para que la zona entre en la
            lista (`MIN_SHOTS` por defecto, §4 del documento) — por debajo,
            ni se muestra: no es una zona, es ruido con forma de zona.

    Returns:
        `zone_label, concede_diff_pp, produce_diff_pp, shot_value,
        shots_per_game, value_pts_per_game, concede_volume, produce_volume`,
        de más a menos valor. Vacío si no hay ninguna zona que cumpla las tres
        condiciones (muestra suficiente en los dos lados y coincidencia de
        signo) o si `games_played` es 0.
    """
    columns = [
        "zone_label", "concede_diff_pp", "produce_diff_pp", "shot_value",
        "shots_per_game", "value_pts_per_game", "concede_volume", "produce_volume",
    ]
    if concede_diff.empty or produce_diff.empty or not games_played:
        return pd.DataFrame(columns=columns)

    merged = concede_diff.merge(
        produce_diff, on=["zone_id", "zone_label"], suffixes=("_concede", "_produce"),
    )
    if merged.empty:
        return pd.DataFrame(columns=columns)

    qualifies = (
        (merged["volume_concede"] >= min_shots)
        & (merged["volume_produce"] >= min_shots)
        & (merged["diff_pp_shrunk_concede"] > 0)
        & (merged["diff_pp_shrunk_produce"] > 0)
    )
    targets = merged.loc[qualifies].copy()
    if targets.empty:
        return pd.DataFrame(columns=columns)

    targets["shot_value"] = targets["zone_id"].map(shot_value)
    targets["shots_per_game"] = targets["volume_concede"] / games_played
    targets["value_pts_per_game"] = (
        targets["diff_pp_shrunk_concede"] / 100.0 * targets["shot_value"] * targets["shots_per_game"]
    )
    targets = targets.rename(columns={
        "diff_pp_shrunk_concede": "concede_diff_pp",
        "diff_pp_shrunk_produce": "produce_diff_pp",
        "volume_concede": "concede_volume",
        "volume_produce": "produce_volume",
    })
    targets = targets.sort_values("value_pts_per_game", ascending=False).reset_index(drop=True)
    if top_n is not None:
        targets = targets.head(top_n)
    return targets[columns]
