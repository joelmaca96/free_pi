"""Similitud de jugadores: a quién se parece un jugador, y en qué.

Propuesta 11 (`doc/features/propuestas/11_similitud_de_jugadores.md`). Dos
usos, un mismo cálculo (§1 del documento): traducir un rival desconocido a
alguien que el equipo ya ha defendido, y encontrar sustitutos de mercado para
una baja. Lo que hace útil la lista no es el ranking — es la explicación de
**en qué** se parecen dos jugadores y en qué no, que sale gratis del propio
cálculo (§4).

**El vector de perfil**, por jugador y por su competición dominante (la de
más partidos, si reparte la temporada entre ACB y Euroliga):

- 10 dimensiones de producción/eficiencia — los percentiles que YA calcula
  la vista `player_percentiles` (`queries_assistant.league_player_percentiles`),
  reutilizados tal cual (§6 del documento: "ya resuelven buena parte de la
  extracción del perfil"). Van en percentil DENTRO de su competición, no en
  valor bruto: comparar puntos por partido de ACB y Euroliga a pelo mezcla
  nivel con estilo (§4).
- 5 dimensiones de reparto de tiro por zona (`ZONE_GROUPS`), la pieza que
  distingue esto de comparar solo medias (§3): dos jugadores con los mismos
  puntos por partido son perfiles muy distintos si uno tira de esquina y el
  otro de poste. Se calculan aquí mismo (no hay vista para esto) con el
  mismo criterio de percentil dentro de competición, para que las 15
  dimensiones se traten todas igual.

**Distancia**: euclídea ponderada (RMS, "parecido en NIVEL": una diferencia
grande en una sola dimensión pesa) o coseno (ponderado igual, "parecido en
ESTILO": compara la FORMA del perfil, no la magnitud) — el documento pide
ofrecer las dos con un interruptor porque son dos preguntas reales y dan
listas distintas (§4). Las dos toleran huecos: una dimensión sin dato en
cualquiera de los dos jugadores se excluye de ESA comparación en vez de
imputarse, y por debajo de `MIN_DIMENSIONS_REQUIRED` dimensiones válidas el
candidato ni se ofrece (demasiado poco perfil para que "parecido" signifique algo).

Sin scikit-learn (§4: "280×280 distancias sobre un vector de 10-15
dimensiones, numpy sobra"): todo esto es `numpy`/`pandas` puro, sin
Streamlit ni SQLAlchemy — se prueba sin base de datos, mismo criterio que
`zone_matchup.py`/`win_thresholds.py`.
"""
import math
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

# ============================================================ dimensiones ==

#: Las 10 dimensiones de producción/eficiencia (§3 del documento), leídas
#: directamente de las columnas de percentil de `player_percentiles`
#: (`queries_assistant.league_player_percentiles`). Rebote total NO entra
#: aparte de ofensivo/defensivo: sumarlos habría contado el rebote dos veces
#: en la distancia (una vez como total, otra como la suma de sus partes) y
#: pesado de más esa familia frente al resto. `tov_pct`/`pf_pct` ya vienen
#: invertidos en la vista (percentil alto = pierde/comete pocas), así que se
#: leen igual que las demás sin más tratamiento.
STAT_DIMENSIONS = [
    {"key": "pts", "column": "pts_pct", "label": "Anotación", "group": "scoring"},
    {"key": "ast", "column": "ast_pct", "label": "Asistencias", "group": "passing"},
    {"key": "efg", "column": "efg_pct_pct", "label": "Eficiencia de tiro (eFG%)", "group": "efficiency"},
    {"key": "oreb", "column": "oreb_pct", "label": "Rebote ofensivo", "group": "rebounding"},
    {"key": "dreb", "column": "dreb_pct", "label": "Rebote defensivo", "group": "rebounding"},
    {"key": "stl", "column": "stl_pct", "label": "Robos", "group": "defense"},
    {"key": "blk", "column": "blk_pct", "label": "Tapones", "group": "defense"},
    {"key": "tov", "column": "tov_pct", "label": "Cuidado de balón (pocas pérdidas)", "group": "care"},
    {"key": "pf", "column": "pf_pct", "label": "Disciplina (pocas faltas)", "group": "care"},
    {"key": "pir", "column": "pir_pct", "label": "Valoración (PIR)", "group": "impact"},
]

#: `court_zones.id` -> grupo de reparto de tiro (§3: "qué proporción de sus
#: tiros sale de cada sitio"). Cinco grupos con significado baloncestístico
#: real en vez de las 17 filas de `court_zones` sueltas (varias son puntos
#: degenerados de la misma zona resuelta por el lado que toque, ver el
#: comentario de `CREATE TABLE court_zones` en `schema.sql`): pintura, media
#: distancia, triple de esquina, triple de ala y triple central. Cubre las
#: 17 filas sin solapar ninguna. Point values de referencia (`shot_quality.
#: THREE_POINT_ZONE_IDS = {4, 5, 6, 8, 9, 15, 17}`): los grupos de triple
#: coinciden exactamente con ese conjunto partido en tres.
ZONE_GROUPS: Dict[int, str] = {
    1: "zone_paint", 10: "zone_paint", 13: "zone_paint",              # Pintura, Mate, Línea de fondo
    2: "zone_mid", 3: "zone_mid", 7: "zone_mid",                      # Ala izq./der. (genérica), media dist. central
    11: "zone_mid", 12: "zone_mid", 14: "zone_mid", 16: "zone_mid",   # Fondo izq./der., Ala izq./der. (2)
    4: "zone_corner3", 5: "zone_corner3",                             # Triple esquina izq./der.
    8: "zone_wing3", 9: "zone_wing3", 15: "zone_wing3", 17: "zone_wing3",  # Triple ala izq./der., Ala izq./der. (3)
    6: "zone_top3",                                                   # Triple exterior (parte alta)
}

ZONE_DIMENSIONS = [
    {"key": "zone_paint", "label": "Tiro en la pintura", "group": "shot_profile"},
    {"key": "zone_mid", "label": "Tiro de media distancia", "group": "shot_profile"},
    {"key": "zone_corner3", "label": "Triple de esquina", "group": "shot_profile"},
    {"key": "zone_wing3", "label": "Triple de ala", "group": "shot_profile"},
    {"key": "zone_top3", "label": "Triple central (parte alta)", "group": "shot_profile"},
]

#: Las 15 dimensiones del perfil, en el orden en que se enseñan (§2: "gráfico
#: de radar... con las 8-10 dimensiones del perfil" — aquí un poco más
#: completo, 10 + 5, dentro del "10-15" que pide §4).
DIMENSIONS = STAT_DIMENSIONS + ZONE_DIMENSIONS

#: Grupos de peso ajustable (§4: "pesos por dimensión, ajustables: quien
#: busca un sustituto para un tirador no quiere que el rebote pese lo
#: mismo"). Se expone por GRUPO y no por dimensión suelta — ocho controles
#: caben en un panel, quince no.
WEIGHT_GROUPS = {
    "scoring": "Anotación", "passing": "Pase", "efficiency": "Eficiencia de tiro",
    "rebounding": "Rebote", "defense": "Defensa (robos/tapones)", "care": "Cuidado de balón/disciplina",
    "impact": "Valoración (PIR)", "shot_profile": "Reparto de tiro por zona",
}

#: Mínimo de dimensiones con dato en LOS DOS jugadores para que la
#: comparación cuente — por debajo, "parecido" se calcularía sobre un tercio
#: del perfil o menos, que no es un perfil, es ruido con forma de perfil.
MIN_DIMENSIONS_REQUIRED = 8

#: Tiros mínimos en la temporada para que el reparto por zona sea perfil y no
#: anécdota (con 5 tiros, "40% en la esquina" son dos triples). Por debajo,
#: las 5 dimensiones de zona quedan en `NaN` para ese jugador y se excluyen
#: dimensión a dimensión, no el jugador entero — puede seguir comparándose
#: por producción/eficiencia.
MIN_SHOTS_FOR_ZONE_PROFILE = 20

#: Filtro de minutos del §4: 300 el suelo, 500 lo recomendado ("por debajo,
#: los percentiles de un jugador son ruido y aparecerá como 'parecido' a
#: cualquiera"). Se aplica siempre a los CANDIDATOS, nunca al jugador de
#: referencia — alguien con pocos minutos puede seguir siendo la pregunta.
MIN_MINUTES_FLOOR = 300
MIN_MINUTES_RECOMMENDED = 500

#: Aviso permanente de §5 del documento — limitaciones de datos que hay que
#: decir siempre, no dejar como letra pequeña opcional.
#:
#: OJO al redactarlo: la limitación ya NO es que falte el físico. Desde que
#: `ingest/euroleague/roster.py` trae altura, peso y fecha de nacimiento, esos
#: campos existen para los jugadores de clubes de Euroliga (~38% de `players`,
#: el Baskonia y todos sus rivales europeos incluidos). Lo que sigue siendo
#: cierto, y es lo que hay que avisar, es que **el cálculo no los mira**: las
#: 15 dimensiones son percentiles de producción y de reparto de tiro (ver
#: `DIMENSIONS`), ni una de físico. Si algún día entran, hay que reescribir
#: esta constante Y este comentario.
SIMILARITY_CAVEAT = (
    "Puramente estadístico: compara producción y reparto de tiro, no físico. La altura y el peso "
    "están en la base de datos, pero el cálculo no los usa — puede emparejar a un base con un alero "
    "que produzca parecido, así que para fichajes mira siempre el vídeo antes de decidir. Tampoco "
    "pesa la edad, y la posición sigue vacía en casi la mitad de la base de datos: por eso se agrupa "
    "por perfil ESTADÍSTICO, no por posición nominal. Una sola temporada: describe cómo ha jugado "
    "alguien estos meses, no lo que es. No corrige el contexto de equipo — pocos minutos en un equipo "
    "dominante puede parecerse a muchos minutos en uno flojo."
)


# ============================================================== percentiles --


def _percent_rank(values: pd.Series) -> pd.Series:
    """`PERCENT_RANK` de SQLite ((rango - 1) / (n - 1)), en pandas para el reparto de tiro.

    Mismo criterio que `schema.sql` (`player_percentiles`, `team_style_
    percentiles`) y que `queries_assistant.referee_rankings` cuando calcula
    percentiles fuera de una vista: con 1 o 0 valores no hay "posición
    relativa" que definir, y se devuelve todo `NaN` en vez de forzar un 0.5
    que no significa nada.
    """
    valid = values.notna()
    n = int(valid.sum())
    if n <= 1:
        return pd.Series(np.nan, index=values.index)
    ranks = values[valid].rank(method="average")
    result = pd.Series(np.nan, index=values.index)
    result[valid] = (ranks - 1) / (n - 1)
    return result


def zone_dimension_percentiles(zone_volume: pd.DataFrame) -> pd.DataFrame:
    """El reparto de tiro por zona de CADA jugador, en percentil dentro de su competición.

    Args:
        zone_volume: salida de `queries.league_player_zone_volume`
            (`player_id, competition_id, zone_id, volume`), de TODOS los
            jugadores de la temporada — el percentil necesita el pool
            entero, no solo a quien se vaya a comparar después.

    Returns:
        `player_id, competition_id` + una columna `<zone_key>_pct` por cada
        `ZONE_DIMENSIONS`, todas en `[0, 1]` o `NaN` si el jugador no llega a
        `MIN_SHOTS_FOR_ZONE_PROFILE` tiros esa temporada. Vacío si
        `zone_volume` lo está.
    """
    pct_columns = [f"{dim['key']}_pct" for dim in ZONE_DIMENSIONS]
    empty_columns = ["player_id", "competition_id"] + pct_columns
    if zone_volume.empty:
        return pd.DataFrame(columns=empty_columns)

    df = zone_volume.copy()
    df["zone_group"] = df["zone_id"].map(ZONE_GROUPS)
    df = df.dropna(subset=["zone_group"])
    if df.empty:
        return pd.DataFrame(columns=empty_columns)

    grouped = df.groupby(["player_id", "competition_id", "zone_group"], as_index=False)["volume"].sum()
    shot_totals = grouped.groupby(["player_id", "competition_id"], as_index=False)["volume"].sum().rename(
        columns={"volume": "_total_shots"}
    )
    grouped = grouped.merge(shot_totals, on=["player_id", "competition_id"])
    grouped["share"] = grouped["volume"] / grouped["_total_shots"]

    wide = grouped.pivot_table(
        index=["player_id", "competition_id"], columns="zone_group", values="share", fill_value=0.0
    ).reset_index()
    # Un grupo sin NINGÚN tiro para nadie en este `zone_volume` (no debería
    # pasar con las 17 zonas reales, pero una BD de prueba pequeña sí puede
    # no tocar alguna) no aparece como columna del `pivot_table` — se añade
    # en 0.0 para que el resto del cálculo no falle con un `KeyError`.
    for dim in ZONE_DIMENSIONS:
        if dim["key"] not in wide.columns:
            wide[dim["key"]] = 0.0

    wide = wide.merge(shot_totals.drop_duplicates(["player_id", "competition_id"]), on=["player_id", "competition_id"], how="left")

    thin = wide["_total_shots"] < MIN_SHOTS_FOR_ZONE_PROFILE
    for dim in ZONE_DIMENSIONS:
        key = dim["key"]
        pct_col = f"{key}_pct"
        wide[pct_col] = wide.groupby("competition_id")[key].transform(_percent_rank)
        wide.loc[thin, pct_col] = np.nan

    return wide[["player_id", "competition_id"] + pct_columns]


def _dominant_competition_rows(percentiles: pd.DataFrame) -> pd.DataFrame:
    """Un jugador puede tener una fila por competición (reparte ACB+Euroliga): se elige
    la de más partidos como perfil principal — empate real cae a la de `competition_id`
    más bajo, determinista y sin azar."""
    if percentiles.empty:
        return percentiles
    ordered = percentiles.sort_values(["player_id", "gp", "competition_id"], ascending=[True, False, True])
    return ordered.groupby("player_id", as_index=False).first()


def build_player_vectors(
    index_df: pd.DataFrame, percentiles_df: pd.DataFrame, zone_volume_df: pd.DataFrame
) -> pd.DataFrame:
    """El vector de perfil de cada jugador de la liga, listo para comparar (§4 del documento).

    Args:
        index_df: salida de `queries.league_player_index` (roster + minutos
            totales de la temporada).
        percentiles_df: salida de `queries_assistant.league_player_percentiles`.
        zone_volume_df: salida de `queries.league_player_zone_volume`.

    Returns:
        Una fila por jugador (su competición dominante): `player_id, name,
        team_id, team_name, position, competition_id, gp, gp_total,
        minutes_total` + una columna `<dim_key>_pct` por cada `DIMENSIONS`
        (`NaN` donde falte). Vacío si `index_df` o `percentiles_df` lo están,
        o si ningún jugador de `percentiles_df` tiene fila en `index_df`
        (roster y percentiles de temporadas distintas).
    """
    pct_cols = [f"{d['key']}_pct" for d in DIMENSIONS]
    empty_columns = [
        "player_id", "name", "team_id", "team_name", "position",
        "competition_id", "gp", "gp_total", "minutes_total",
    ] + pct_cols
    if index_df.empty or percentiles_df.empty:
        return pd.DataFrame(columns=empty_columns)

    dominant = _dominant_competition_rows(percentiles_df)
    # Selección explícita a un DataFrame nuevo, NUNCA un `.rename()` sobre
    # `dominant` tal cual: la vista trae `efg_pct` (el valor bruto) Y
    # `efg_pct_pct` (su percentil) a la vez, así que renombrar `efg_pct_pct`
    # -> `efg_pct` sobre el frame original dejaría DOS columnas con la misma
    # etiqueta (la bruta y la renombrada) — un `df[["efg_pct"]]` sobre eso
    # devuelve las dos, descuadrando cualquier cálculo posicional aguas abajo
    # (verificado en vivo: `most_similar` reventaba con un desajuste de forma
    # 15 vs 16 columnas). Construir la columna una a una evita que la
    # colisión pueda darse siquiera.
    primary = dominant[["player_id", "competition_id", "gp"]].copy()
    for dim in STAT_DIMENSIONS:
        primary[f"{dim['key']}_pct"] = dominant[dim["column"]]

    zone_pct = zone_dimension_percentiles(zone_volume_df)
    merged = primary.merge(zone_pct, on=["player_id", "competition_id"], how="left")
    merged = merged.merge(index_df, on="player_id", how="inner")
    if merged.empty:
        return pd.DataFrame(columns=empty_columns)

    for col in pct_cols:
        if col not in merged.columns:
            merged[col] = np.nan
    return merged[empty_columns]


# =================================================================== distancia --


def expand_group_weights(group_weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Pesos por GRUPO (`WEIGHT_GROUPS`) -> pesos por DIMENSIÓN, 1.0 en cualquiera que no se ajuste."""
    weights = group_weights or {}
    return {dim["key"]: float(weights.get(dim["group"], 1.0)) for dim in DIMENSIONS}


def _dimension_columns() -> "list[str]":
    return [f"{dim['key']}_pct" for dim in DIMENSIONS]


def most_similar(
    vectors: pd.DataFrame,
    player_id: str,
    *,
    method: str = "cosine",
    group_weights: Optional[Dict[str, float]] = None,
    min_minutes: float = MIN_MINUTES_RECOMMENDED,
    competition_id: Optional[int] = None,
    allowed_team_ids: Optional[Sequence[str]] = None,
    top_n: int = 10,
) -> pd.DataFrame:
    """Los `top_n` jugadores más parecidos a `player_id` (§2 del documento: "salen los 8-10 más parecidos").

    Args:
        vectors: salida de `build_player_vectors`.
        method: `'cosine'` ("parecido en ESTILO": compara la forma del
            perfil, ignorando el nivel absoluto) o `'euclidean'` ("parecido
            en NIVEL": una diferencia grande en una sola dimensión pesa
            tanto como su tamaño real). §4: "porque son las dos preguntas
            reales y dan listas distintas".
        group_weights: `{grupo: peso}` de `WEIGHT_GROUPS`; grupos ausentes
            valen 1.0.
        min_minutes: filtro de minutos de LOS CANDIDATOS (nunca del jugador
            de referencia) — `MIN_MINUTES_RECOMMENDED` por defecto.
        competition_id: si se pasa, solo compara contra jugadores de esa
            competición.
        allowed_team_ids: si se pasa, solo compara contra jugadores de esos
            equipos — es el filtro "solo jugadores que ya hemos enfrentado"
            del §2, con `queries.opponent_team_ids` como origen habitual.
        top_n: cuántos candidatos devolver como mucho.

    Returns:
        `vectors` filtrado a los candidatos elegidos, con `similarity_score`
        (0-100, 100 = idéntico en las dimensiones disponibles) y
        `dims_used` (cuántas de las 15 dimensiones tenían dato en LOS DOS
        jugadores), de más a menos parecido. Vacío si `player_id` no está en
        `vectors`, si ningún candidato pasa los filtros, o si ninguno llega
        a `MIN_DIMENSIONS_REQUIRED` dimensiones comparables.

    Raises:
        ValueError: `method` no es `'cosine'` ni `'euclidean'`.
    """
    if method not in ("cosine", "euclidean"):
        raise ValueError(f"método desconocido: {method!r} (válidos: 'cosine', 'euclidean')")

    target_rows = vectors.loc[vectors["player_id"] == player_id]
    if target_rows.empty:
        return vectors.iloc[0:0]
    target = target_rows.iloc[0]

    candidates = vectors.loc[vectors["player_id"] != player_id].copy()
    candidates = candidates.loc[candidates["minutes_total"].fillna(0) >= min_minutes]
    if competition_id is not None:
        candidates = candidates.loc[candidates["competition_id"] == competition_id]
    if allowed_team_ids is not None:
        candidates = candidates.loc[candidates["team_id"].isin(set(allowed_team_ids))]
    if candidates.empty:
        return candidates.assign(similarity_score=pd.Series(dtype=float), dims_used=pd.Series(dtype=int))

    cols = _dimension_columns()
    weight_map = expand_group_weights(group_weights)
    weights = np.array([weight_map[dim["key"]] for dim in DIMENSIONS])
    target_vec = target[cols].to_numpy(dtype=float)
    matrix = candidates[cols].to_numpy(dtype=float)

    n = len(candidates)
    scores = np.full(n, np.nan)
    dims_used = np.zeros(n, dtype=int)
    for i in range(n):
        row = matrix[i]
        valid = ~np.isnan(row) & ~np.isnan(target_vec)
        dims_used[i] = int(valid.sum())
        if dims_used[i] < MIN_DIMENSIONS_REQUIRED:
            continue
        w = weights[valid]
        a = target_vec[valid]
        b = row[valid]
        if method == "euclidean":
            # RMS ponderado: sqrt(sum(w*(a-b)^2) / sum(w)), no la suma sin
            # más — así el número no depende de CUÁNTAS dimensiones tenía
            # disponibles el candidato, solo de cuánto se parecen en las que
            # sí tiene. Percentiles en [0,1] -> RMS en [0,1] -> score en
            # [0,100] sin necesitar normalizar por separado.
            rms = math.sqrt(float(np.sum(w * (a - b) ** 2)) / float(w.sum()))
            scores[i] = max(0.0, 100.0 * (1.0 - rms))
        else:
            denom = math.sqrt(float(np.sum(w * a * a))) * math.sqrt(float(np.sum(w * b * b)))
            cosine = (float(np.sum(w * a * b)) / denom) if denom > 0 else 0.0
            # Percentiles no negativos -> coseno ya vive en [0,1] casi
            # siempre; el `min`/`max` es solo por seguridad ante redondeo.
            scores[i] = max(0.0, min(100.0, 100.0 * cosine))

    result = candidates.assign(similarity_score=scores, dims_used=dims_used)
    result = result.loc[result["dims_used"] >= MIN_DIMENSIONS_REQUIRED]
    result = result.sort_values("similarity_score", ascending=False)
    return result.head(top_n).reset_index(drop=True)


def explain_similarity(
    target_row: pd.Series,
    candidate_row: pd.Series,
    *,
    group_weights: Optional[Dict[str, float]] = None,
    n_closest: int = 3,
    n_farthest: int = 1,
) -> dict:
    """"En qué se parecen y en qué no" (§2): sale gratis del propio cálculo (§4).

    Ordena las dimensiones por diferencia PONDERADA (peso × diferencia en
    puntos de percentil) para que la explicación cuente lo mismo que decidió
    el ranking — una dimensión con peso alto y diferencia moderada puede
    haber pesado más en la distancia que una de peso bajo con diferencia
    grande, y la explicación tiene que ser consistente con eso.

    Args:
        target_row / candidate_row: filas de `vectors` (salida de
            `build_player_vectors`, o de `most_similar`).

    Returns:
        `{"closest": [...], "farthest": [...]}`, cada entrada
        `{"key", "label", "diff_pp"}` (`diff_pp` en puntos de percentil,
        0-100, sin ponderar — el peso solo ordena, no se enseña como número).
        Una dimensión sin dato en cualquiera de los dos jugadores no entra.
    """
    weight_map = expand_group_weights(group_weights)
    diffs = []
    for dim in DIMENSIONS:
        col = f"{dim['key']}_pct"
        a, b = target_row.get(col), candidate_row.get(col)
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            continue
        diff_pp = abs(float(a) - float(b)) * 100.0
        diffs.append({
            "key": dim["key"], "label": dim["label"], "diff_pp": diff_pp,
            "_weighted": diff_pp * max(weight_map[dim["key"]], 1e-9),
        })

    diffs.sort(key=lambda d: d["_weighted"])
    closest = [{"key": d["key"], "label": d["label"], "diff_pp": d["diff_pp"]} for d in diffs[:n_closest]]
    farthest = [
        {"key": d["key"], "label": d["label"], "diff_pp": d["diff_pp"]}
        for d in sorted(diffs, key=lambda d: d["_weighted"], reverse=True)[:n_farthest]
    ]
    return {"closest": closest, "farthest": farthest}
