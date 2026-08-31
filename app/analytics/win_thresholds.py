"""Umbrales de victoria: qué tres o cuatro números separan ganar de perder.

Propuesta 09 (`doc/features/propuestas/09_umbrales_de_victoria.md`). El
problema real no es de cálculo, es de selección: un entrenador no trabaja con
veinte estadísticas en la charla previa, trabaja con tres. Este módulo calcula
esos tres números y, con ellos, ordena el resto de la carpeta de propuestas —
si algo no mueve la probabilidad de ganar, no merece pantalla (§1).

**Los cuatro factores clásicos**, expresados siempre como "ganar esa batalla
al rival" (la propia cifra menos la del rival ese mismo partido, con el signo
puesto para que positivo = favorable) y NO como el porcentaje propio a secas:
el documento mide en vivo que el eFG% sin comparar (r=+0,46) engaña bastante
más que la diferencia (r=+0,66) — un equipo puede tirar bien y aun así perder
la batalla si el rival tira mejor todavía. `FACTORS` es la única definición de
cuáles son y cómo se leen; el resto del módulo trabaja sobre ella.

**v1 — descriptivo** (`league_objectives`): para cada factor se barre el
umbral que mejor separa el % de victorias de los dos lados
(`sweep_threshold`), exigiendo al menos `MIN_SIDE_GAMES` partidos en cada
lado — por debajo, un "umbral" es una anécdota con decimales. Sin modelo, sin
dependencias nuevas, y la curva entera se puede enseñar si hace falta.

**v2 — modelo** (`fit_logistic_model`): regresión logística de la victoria
sobre los cuatro factores en diferencias, con descenso de gradiente escrito a
mano sobre `numpy` — `scikit-learn`/`statsmodels` NO están en
`app/requirements.txt` y el documento recomienda esta vía antes que añadir
~100 MB a la imagen de la interfaz para cuatro variables. Da pesos relativos
(`factor_importance`) y una probabilidad estimada (`predict_win_probability`).

**Ajuste por rival** (`rival_concession_averages` + `rival_adjusted_card`):
contra un rival concreto hay 2-4 partidos en la temporada — sacar un umbral
de ahí es superstición con formato de tabla (§4). El ajuste real es en dos
pasos: el umbral de liga (arriba) más el desplazamiento que marca lo que ESE
rival concede/fuerza en su temporada completa (30-40 partidos), nunca su
historial cara a cara. Ver el docstring de `rival_adjusted_card` para la
derivación completa de la fórmula.

Es lógica pura sobre `pandas`/`numpy`, sin Streamlit ni SQLAlchemy — mismo
criterio que `shot_quality.py`/`zone_matchup.py`: se puede probar sin base de
datos. Los cuadros que pinta consumen `data.queries.game_factor_rows`.
"""
import math
from typing import Optional, Sequence

import numpy as np
import pandas as pd

#: Partidos mínimos EN CADA LADO de un umbral candidato (§4 v1). Con 737
#: partidos-equipo de sobra para umbrales de liga (§5) pero no para un solo
#: equipo ni, mucho menos, para un rival concreto — por eso el ajuste por
#: rival nunca recalcula el umbral desde cero, solo lo desplaza.
MIN_SIDE_GAMES = 30

#: Peso de la regularización con la que se ELIGE el umbral (no la que se
#: exige): maximizar la separación de victorias en crudo, con `min_side`
#: como único freno, empuja el barrido hacia el corte más extremo que aún
#: cumple los 30 partidos por lado — verificado en vivo con la base de datos
#: real: sin este freno, "rebote ofensivo" salía en "al menos 53%" (36
#: partidos de 1.474, el pico de la temporada, no un objetivo). Se puntúa
#: cada candidato por `separación × lado_pequeño / (lado_pequeño + k)`, el
#: mismo criterio `n/(n+k)` que ya regulariza `shot_quality.shrink` y
#: `zone_matchup` — entre dos cortes con separación parecida, gana el que
#: reparte más partidos a los dos lados.
SEPARATION_SHRINK_K = 100

#: Tarjetas mínimas de categoría "process" (no de acierto) que debe tener el
#: panel (§5): "eFG% se lo come todo" — con r=+0,66 cualquier ranking sin
#: freno pone tirar mejor en las tres tarjetas, que no es accionable.
MIN_NON_SHOOTING_CARDS = 2

#: Los cuatro factores clásicos (Dean Oliver), tal y como los mide §1 del
#: documento. `own_col`/`opp_col` son las columnas de
#: `queries.game_factor_rows` (una fila por equipo y partido, con la fila del
#: rival del MISMO partido ya cruzada). `higher_is_better` es sobre la propia
#: cifra ("más eFG% es mejor"); el signo de la "batalla" lo pone
#: `_favorable_diff`. `category` distingue acierto ("shooting", solo eFG%) de
#: proceso ("process": tiros libres, rebote, pérdidas) para la regla de
#: diversidad de `league_objectives`.
FACTORS = [
    {
        "key": "efg_pct", "label": "acierto efectivo (eFG%)",
        "own_col": "efg_pct", "opp_col": "opp_efg_pct",
        "higher_is_better": True, "category": "shooting",
    },
    {
        "key": "ft_rate", "label": "tasa de tiros libres",
        "own_col": "ft_rate", "opp_col": "opp_ft_rate",
        "higher_is_better": True, "category": "process",
    },
    {
        "key": "orb_pct", "label": "rebote ofensivo",
        "own_col": "orb_pct", "opp_col": "opp_orb_pct",
        "higher_is_better": True, "category": "process",
    },
    {
        "key": "tov_pct", "label": "pérdidas",
        "own_col": "tov_pct", "opp_col": "opp_tov_pct",
        "higher_is_better": False, "category": "process",
    },
]

#: Columnas obligatorias de `queries.game_factor_rows` — se comprueban en la
#: puerta de las funciones que arrancan el cálculo entero, mismo criterio que
#: `shot_quality.COUNT_COLUMNS`.
REQUIRED_COLUMNS = ("team_id", "win")

#: Aviso permanente de qué son (y qué NO son) estos umbrales (§5 del
#: documento). Va siempre con el panel, no como nota al pie opcional: es
#: exactamente el sitio donde el cuerpo técnico puede sacar una conclusión
#: falsa si no se dice.
_OBJECTIVES_CAVEAT_TEMPLATE = (
    "Correlación, no causa: son los niveles que mejor separaron victorias de derrotas esta "
    "temporada, no una receta — ir por delante cambia cómo se juega, así que ganar el rebote "
    "ofensivo y ganar el partido comparten causas. El acierto (eFG%) es, con diferencia, la "
    "batalla que más pesa; los demás objetivos entran igual porque son los que se trabajan en "
    "la pista, no porque pesen más que tirar bien. Calculados sobre {scope}, de una única "
    "temporada y sin la anterior para validar fuera de muestra: son descriptivos de ESTA "
    "temporada, no una predicción."
)

#: El aviso con el ámbito histórico (las dos competiciones juntas) — se
#: mantiene como constante, sin parámetro, para quien ya la usaba tal cual
#: antes de que la interfaz pudiera acotar a una sola competición (§5:
#: "conviene poder separarlas", ver `objectives_caveat_text`).
OBJECTIVES_CAVEAT = _OBJECTIVES_CAVEAT_TEMPLATE.format(scope="ACB y Euroliga juntas")


def objectives_caveat_text(competition_label: Optional[str] = None) -> str:
    """El aviso de §5, con el ámbito correcto: las dos competiciones juntas, o una sola si se acotó.

    Args:
        competition_label: nombre de la competición si el panel se acotó a
            una sola (p.ej. "ACB"); `None` (por defecto) para el ámbito
            histórico de las dos juntas.
    """
    scope = "ACB y Euroliga juntas" if competition_label is None else f"partidos de {competition_label}"
    return _OBJECTIVES_CAVEAT_TEMPLATE.format(scope=scope)


def _check_columns(df: pd.DataFrame, factors: Sequence[dict]) -> None:
    needed = set(REQUIRED_COLUMNS)
    for factor in factors:
        needed.add(factor["own_col"])
        needed.add(factor["opp_col"])
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"faltan columnas en game_factor_rows: {', '.join(sorted(missing))}")


def _favorable_diff(df: pd.DataFrame, factor: dict) -> pd.Series:
    """La "batalla" de un factor: positivo siempre significa "se gana esa batalla".

    Para un factor donde más es mejor (eFG%, tasa de TL, ORB%) es la propia
    cifra menos la del rival; para uno donde menos es mejor (TOV%, pérdidas)
    se invierte el orden de la resta — así el signo se lee igual en los
    cuatro sin que quien llama tenga que recordar cuál va al revés.
    """
    own, opp = df[factor["own_col"]], df[factor["opp_col"]]
    return (own - opp) if factor["higher_is_better"] else (opp - own)


def sweep_threshold(
    diff: pd.Series, win: pd.Series, min_side: int = MIN_SIDE_GAMES, shrink_k: int = SEPARATION_SHRINK_K
) -> Optional[dict]:
    """El barrido de umbrales de §4 v1: qué corte de `diff` separa mejor victoria de derrota.

    Candidato a candidato (cada valor observado de `diff`, de menor a mayor),
    se compara el % de victorias de quien queda POR ENCIMA O IGUAL con el de
    quien queda por debajo, exigiendo que las dos mitades tengan al menos
    `min_side` partidos (por debajo de eso, la separación puede ser altísima
    y ser ruido puro de una muestra de cuatro) y que la mitad "por encima"
    gane MÁS que la de abajo — un candidato al revés no es un umbral, es la
    señal leída del derecho al revés.

    Entre los candidatos que pasan ese filtro, NO gana sin más el de mayor
    separación en crudo: eso empuja el corte hacia el más extremo que aún
    roza `min_side` (verificado en vivo, ver `SEPARATION_SHRINK_K`). Se
    puntúa cada uno por `separación × lado_pequeño / (lado_pequeño +
    shrink_k)` y gana la puntuación más alta — el mismo criterio de
    regularización que `shot_quality.shrink`, aplicado aquí al TAMAÑO del
    corte en vez de al número de tiros.

    Args:
        diff: la "batalla" ya con el signo puesto (`_favorable_diff`) —
            positivo = favorable, en las unidades que sea.
        win: 0/1, victoria del mismo partido-equipo que `diff`.
        min_side: partidos mínimos en CADA lado del corte (`MIN_SIDE_GAMES`).
        shrink_k: peso de la regularización de la puntuación (`SEPARATION_SHRINK_K`).

    Returns:
        `{"threshold", "win_pct_meets", "win_pct_miss", "n_meets", "n_miss",
        "n_total", "separation"}` (los `win_pct_*`/`separation` en puntos
        porcentuales, 0-100) del corte de mayor puntuación válido, o `None`
        si ningún candidato tiene las dos mitades suficientemente grandes y
        en la dirección correcta.
    """
    frame = pd.DataFrame({"diff": diff, "win": win}).dropna()
    if frame.empty:
        return None

    best, best_score = None, -1.0
    for candidate in np.sort(frame["diff"].unique()):
        meets = frame["diff"] >= candidate
        n_meets, n_miss = int(meets.sum()), int((~meets).sum())
        if n_meets < min_side or n_miss < min_side:
            continue
        win_pct_meets = 100.0 * frame.loc[meets, "win"].mean()
        win_pct_miss = 100.0 * frame.loc[~meets, "win"].mean()
        separation = win_pct_meets - win_pct_miss
        if separation <= 0:
            continue
        smaller_side = min(n_meets, n_miss)
        score = separation * smaller_side / (smaller_side + shrink_k)
        if score > best_score:
            best_score = score
            best = {
                "threshold": float(candidate),
                "win_pct_meets": win_pct_meets,
                "win_pct_miss": win_pct_miss,
                "n_meets": n_meets,
                "n_miss": n_miss,
                "n_total": n_meets + n_miss,
                "separation": separation,
            }
    return best


def factor_correlations(df: pd.DataFrame, factors: Sequence[dict] = FACTORS) -> pd.DataFrame:
    """La tabla de §1 del documento: correlación de cada batalla con ganar.

    Reproduce el mensaje de vestuario ("el 80% de los partidos los gana quien
    tira mejor") con los datos que haya cargados, no los hardcodea — sirve
    para comprobar que la cifra de la propuesta sigue vigente según crece la
    base de datos.

    Returns:
        `key, label, correlation, win_pct_when_favorable, n_favorable,
        n_total`, de más a menos correlación. Un factor sin muestra válida
        (menos de 2 filas con `diff` y `win` no nulos) se omite.
    """
    _check_columns(df, factors)
    rows = []
    for factor in factors:
        diff = _favorable_diff(df, factor)
        valid = diff.notna() & df["win"].notna()
        if valid.sum() < 2:
            continue
        correlation = diff[valid].corr(df.loc[valid, "win"].astype(float))
        favorable = valid & (diff > 0)
        win_pct = 100.0 * df.loc[favorable, "win"].mean() if favorable.any() else float("nan")
        rows.append({
            "key": factor["key"],
            "label": factor["label"],
            "correlation": correlation,
            "win_pct_when_favorable": win_pct,
            "n_favorable": int(favorable.sum()),
            "n_total": int(valid.sum()),
        })
    columns = ["key", "label", "correlation", "win_pct_when_favorable", "n_favorable", "n_total"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values("correlation", ascending=False).reset_index(drop=True)


def _display_threshold(factor: dict, diff_threshold: float, concession_avg: float) -> Optional[float]:
    """Convierte el umbral de BATALLA (una diferencia) en un número que un jugador entiende.

    `diff_threshold` es una diferencia frente al rival, y decirle a un
    jugador "gana el rebote ofensivo por 5 puntos" es menos accionable que
    "coge el 32% de rebote ofensivo". Se reconstruye asumiendo un rival
    MEDIO: `concession_avg` es lo que concede/produce la liga entera en ese
    factor (`opp_col` promediado sobre todos los partidos), así que sumar (o
    restar, si menos es mejor) el umbral de batalla da el número propio que,
    contra un rival de nivel medio, habría cruzado ese umbral.
    """
    if pd.isna(concession_avg):
        return None
    sign = 1.0 if factor["higher_is_better"] else -1.0
    return float(concession_avg) + sign * diff_threshold


def _select_diverse(
    candidates: list, max_cards: int, min_non_shooting: int
) -> list:
    """Aplica la regla de §5: al menos `min_non_shooting` tarjetas que no sean de acierto.

    `candidates` ya viene ordenado de más a menos separación. Si el recorte a
    `max_cards` deja menos de `min_non_shooting` tarjetas de proceso, se
    cambian las peores tarjetas de acierto por las mejores de proceso que se
    hayan quedado fuera — nunca al revés: el objetivo es que no falte
    variedad, no maximizar separación a toda costa.
    """
    if len(candidates) <= max_cards:
        return candidates

    chosen = candidates[:max_cards]
    non_shooting_count = sum(1 for c in chosen if c["category"] != "shooting")
    if non_shooting_count >= min_non_shooting:
        return chosen

    leftover_non_shooting = [c for c in candidates[max_cards:] if c["category"] != "shooting"]
    for extra in leftover_non_shooting:
        if non_shooting_count >= min_non_shooting:
            break
        # La peor tarjeta de acierto de las elegidas (la de menos separación).
        shooting_in_chosen = [c for c in chosen if c["category"] == "shooting"]
        if not shooting_in_chosen:
            break
        worst_shooting = min(shooting_in_chosen, key=lambda c: c["separation"])
        chosen = [c for c in chosen if c is not worst_shooting] + [extra]
        non_shooting_count += 1

    return sorted(chosen, key=lambda c: c["separation"], reverse=True)


def league_objectives(
    df: pd.DataFrame,
    factors: Sequence[dict] = FACTORS,
    min_side: int = MIN_SIDE_GAMES,
    max_cards: int = 3,
    min_non_shooting: int = MIN_NON_SHOOTING_CARDS,
) -> list:
    """El panel de objetivos de §2a: hasta `max_cards` tarjetas, de más a menos separación.

    Args:
        df: salida de `queries.game_factor_rows`, de la temporada (y
            competiciones) que se quiera describir.
        min_side: ver `sweep_threshold`.
        max_cards: cuántas tarjetas enseñar como mucho.
        min_non_shooting: mínimo de tarjetas de categoría "process" entre las
            elegidas (§5 — "eFG% se lo come todo").

    Returns:
        Lista de dicts (uno por `FACTORS`, más los campos de
        `sweep_threshold` y `display_threshold`/`concession_avg`), de más a
        menos separación. Un factor sin umbral válido (`sweep_threshold`
        devuelve `None`) no entra. Vacío si ninguno lo tiene.
    """
    _check_columns(df, factors)
    candidates = []
    for factor in factors:
        diff = _favorable_diff(df, factor)
        result = sweep_threshold(diff, df["win"], min_side)
        if result is None:
            continue
        concession_avg = df[factor["opp_col"]].mean()
        display_threshold = _display_threshold(factor, result["threshold"], concession_avg)
        candidates.append({
            **factor, **result,
            "diff_threshold": result["threshold"],
            "concession_avg": concession_avg,
            "display_threshold": display_threshold,
        })
    candidates.sort(key=lambda c: c["separation"], reverse=True)
    return _select_diverse(candidates, max_cards, min_non_shooting)


def rival_concession_averages(df: pd.DataFrame, team_id: str, factors: Sequence[dict] = FACTORS) -> dict:
    """Lo que ESE equipo concede/fuerza en cada factor, sobre su temporada completa.

    Es el paso 2 de §4: `opp_col` en las filas de `team_id` es lo que hizo el
    RIVAL de `team_id` en cada uno de sus partidos — su ORB% concedido, su
    TOV% forzado, etc. Promediar esas filas da el perfil defensivo/ofensivo
    de `team_id` con 30-40 partidos detrás, no los 2-4 de un cara a cara.

    Returns:
        `{factor_key: promedio, ..., "n_games": int}`. `{}` si `team_id` no
        tiene ninguna fila en `df` (sin partidos con avanzadas esa temporada).
    """
    rows = df.loc[df["team_id"] == team_id]
    if rows.empty:
        return {}
    result = {factor["key"]: rows[factor["opp_col"]].mean() for factor in factors}
    result["n_games"] = int(len(rows))
    return result


def league_concession_averages(df: pd.DataFrame, factors: Sequence[dict] = FACTORS) -> dict:
    """Lo que concede/fuerza un equipo MEDIO de la liga en cada factor — la línea base del ajuste."""
    if df.empty:
        return {}
    return {factor["key"]: df[factor["opp_col"]].mean() for factor in factors}


def rival_adjusted_card(card: dict, rival_avg: dict, league_avg: dict) -> dict:
    """Desplaza una tarjeta de `league_objectives` al perfil de un rival concreto (§4, paso 2).

    La derivación: `display_threshold` de la tarjeta de liga ya es
    `concession_avg_liga ± umbral_de_batalla` (ver `_display_threshold`) —
    sustituir la concesión MEDIA de la liga por la concesión de ESTE rival da
    directamente el número ajustado, sin tocar el umbral de batalla (que
    sigue viniendo de los 737 partidos de la liga, la muestra grande):

        ajustado = liga + (concesión_rival − concesión_liga)

    Esa resta es el mismo desplazamiento para los cuatro factores sea cual
    sea su dirección (`higher_is_better`) — el signo ya está metido en cómo
    se construyó `display_threshold`.

    Args:
        card: una tarjeta de `league_objectives`.
        rival_avg: salida de `rival_concession_averages` para el rival.
        league_avg: salida de `league_concession_averages`, de la MISMA
            temporada/muestra que produjo `card` (si el rival y la liga
            vienen de temporadas distintas por un fallback, cada `df` se pide
            aparte y aun así se comparan bien: los dos son promedios en las
            mismas unidades).

    Returns:
        `card` con `adjusted_threshold`, `rival_shift` (el desplazamiento en
        puntos porcentuales) e `is_rival_adjusted`. Si falta el perfil del
        rival o de la liga para esta clave, `adjusted_threshold` cae al de
        liga sin desplazar e `is_rival_adjusted` sale en `False` — nunca se
        inventa un ajuste sin datos detrás.
    """
    key = card["key"]
    if not rival_avg or not league_avg or key not in rival_avg or key not in league_avg:
        return {**card, "adjusted_threshold": card["display_threshold"], "rival_shift": 0.0, "is_rival_adjusted": False}

    shift = rival_avg[key] - league_avg[key]
    base = card["display_threshold"]
    adjusted = None if base is None or pd.isna(shift) else base + shift
    return {**card, "adjusted_threshold": adjusted, "rival_shift": shift, "is_rival_adjusted": True}


def game_card_results(game_row: pd.Series, cards: list) -> list:
    """§2b: de las tarjetas de `league_objectives`, cuáles cumplió UN partido concreto.

    Args:
        game_row: una fila de `queries.game_factor_rows` (un equipo, un
            partido).
        cards: tarjetas de `league_objectives` (o ya ajustadas al rival con
            `rival_adjusted_card` — se lee `adjusted_threshold` si está, si
            no `display_threshold`).

    Returns:
        Una lista con cada tarjeta más `actual` (el valor real de ese
        partido) y `met` (`bool`, o `None` si el partido no tiene ese dato o
        no hay umbral con el que compararlo).
    """
    results = []
    for card in cards:
        threshold = card.get("adjusted_threshold", card.get("display_threshold"))
        actual = game_row.get(card["own_col"])
        if threshold is None or actual is None or pd.isna(actual):
            results.append({**card, "actual": actual, "met": None})
            continue
        met = actual >= threshold if card["higher_is_better"] else actual <= threshold
        results.append({**card, "actual": actual, "met": bool(met)})
    return results


def format_pct(value: Optional[float], decimals: int = 1) -> str:
    """`32.04` -> `'32,0%'` — coma decimal, como el resto de porcentajes de la interfaz."""
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.{decimals}f}%".replace(".", ",")


def card_value_text(card: dict) -> str:
    """Solo el número de la tarjeta: "Al menos 32,0%" — sin el nombre del factor.

    Separada de `card_headline` para que quien pinta el `st.metric` (donde el
    nombre del factor YA va en el título) no tenga que recortar la frase
    completa a mano.
    """
    threshold = card.get("adjusted_threshold", card.get("display_threshold"))
    if threshold is None:
        return "Sin referencia suficiente"
    comparator = "Al menos" if card["higher_is_better"] else "Como mucho"
    return f"{comparator} {format_pct(threshold)}"


def card_headline(card: dict) -> str:
    """La frase corta de la tarjeta: "Al menos 32,0% de rebote ofensivo"."""
    value = card_value_text(card)
    if value == "Sin referencia suficiente":
        return f"{card['label'][0].upper()}{card['label'][1:]}: {value.lower()}"
    return f"{value} de {card['label']}"


def card_caption(card: dict) -> str:
    """El pie en pequeño de §2a: el historial detrás del umbral DE LIGA (nunca el ajustado)."""
    return (
        f"{format_pct(card['win_pct_meets'], 0)} de victorias cuando se cumple ({card['n_meets']} partidos) "
        f"frente a {format_pct(card['win_pct_miss'], 0)} cuando no ({card['n_miss']} partidos), esta temporada."
    )


# --------------------------------------------------------------------------- v2: modelo --


def _standardize(matrix: np.ndarray) -> "tuple[np.ndarray, np.ndarray, np.ndarray]":
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std = np.where(std == 0, 1.0, std)
    return (matrix - mean) / std, mean, std


def fit_logistic_model(
    df: pd.DataFrame,
    factors: Sequence[dict] = FACTORS,
    learning_rate: float = 0.15,
    n_iter: int = 5000,
    l2: float = 1e-3,
) -> Optional[dict]:
    """v2 de §4: regresión logística de la victoria sobre las cuatro batallas, a mano con numpy.

    Descenso de gradiente batch sobre las variables ESTANDARIZADAS (media 0,
    desviación 1) — sin estandarizar, `efg_pct` (que se mueve en puntos
    porcentuales de un dígito) y `orb_pct`/`tov_pct` (que se mueven más)
    tendrían pesos que no se pueden comparar entre sí, y comparar pesos es
    justo para lo que sirve este modelo (`factor_importance`). `l2` es una
    regularización pequeña y fija, no ajustada por validación cruzada: con
    cuatro variables y miles de partidos el sobreajuste no es el riesgo real
    aquí, es solo una red de seguridad barata contra pesos que se disparan si
    dos factores están muy correlacionados entre sí.

    Returns:
        `{"keys", "weights", "bias", "means", "stds", "n"}` — `weights` y los
        estadísticos de estandarización por clave de factor, listos para
        `predict_win_probability`/`factor_importance`. `None` si no queda
        ninguna fila con las cuatro batallas y `win` sin nulos.
    """
    _check_columns(df, factors)
    diffs = {factor["key"]: _favorable_diff(df, factor) for factor in factors}
    matrix = pd.DataFrame(diffs, index=df.index).assign(win=df["win"]).dropna()
    if matrix.empty:
        return None

    keys = [factor["key"] for factor in factors]
    X = matrix[keys].to_numpy(dtype=float)
    y = matrix["win"].to_numpy(dtype=float)
    Xs, mean, std = _standardize(X)
    n, k = Xs.shape
    weights = np.zeros(k)
    bias = 0.0
    for _ in range(n_iter):
        z = Xs @ weights + bias
        p = 1.0 / (1.0 + np.exp(-z))
        grad_w = Xs.T @ (p - y) / n + l2 * weights
        grad_b = float((p - y).mean())
        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b

    return {
        "keys": keys,
        "weights": dict(zip(keys, weights.tolist())),
        "bias": float(bias),
        "means": dict(zip(keys, mean.tolist())),
        "stds": dict(zip(keys, std.tolist())),
        "n": int(n),
    }


def predict_win_probability(model: dict, diffs: dict) -> float:
    """Probabilidad de victoria del modelo v2 para unas batallas dadas.

    Args:
        model: salida de `fit_logistic_model`.
        diffs: `{factor_key: valor_de_la_batalla}` (mismo signo que
            `_favorable_diff` — positivo = favorable). Una clave ausente se
            trata como "en la media de la liga" (aporta 0 al modelo
            estandarizado), no como 0 en unidades reales.
    """
    z = model["bias"]
    for key in model["keys"]:
        raw = diffs.get(key, model["means"][key])
        z += model["weights"][key] * (raw - model["means"][key]) / model["stds"][key]
    return 1.0 / (1.0 + math.exp(-z))


def factor_importance(model: dict, factors: Sequence[dict] = FACTORS) -> pd.DataFrame:
    """Pesos del modelo v2, en orden de importancia — la respuesta a "¿cuál pesa más?".

    Returns:
        `key, label, weight, share_pct`, de más a menos peso ABSOLUTO
        (`share_pct` reparte 100% entre los cuatro según ese peso). El signo
        de `weight` se conserva: siempre positivo si `_favorable_diff` está
        bien construida (ganar más una batalla no debería asociarse a perder
        más), y un signo negativo aquí es la señal de que algo del cálculo
        está mal, no un resultado a enseñar tal cual.
    """
    labels = {factor["key"]: factor["label"] for factor in factors}
    rows = [(key, labels.get(key, key), model["weights"][key]) for key in model["keys"]]
    importance = pd.DataFrame(rows, columns=["key", "label", "weight"])
    importance["abs_weight"] = importance["weight"].abs()
    total = importance["abs_weight"].sum()
    importance["share_pct"] = 100.0 * importance["abs_weight"] / total if total else 0.0
    return importance.sort_values("abs_weight", ascending=False).reset_index(drop=True)
