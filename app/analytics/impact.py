"""Impacto ajustado (RAPM) y constructor de quintetos.

Propuesta 12 (`doc/features/propuestas/12_impacto_ajustado_y_constructor.md`).
El On/Off de `queries_assistant.player_on_off` responde "¿cómo le va al equipo
con él?", pero no separa al jugador de con quién comparte pista ni contra
quién: quien juega siempre con los titulares sale inflado, y el suplente que
se come los minutos contra titulares rivales sale castigado. El RAPM
(*Regularized Adjusted Plus-Minus*) sí: ajusta una regresión con los diez
jugadores en pista en cada tramo, así que el número de cada uno ya descuenta a
sus compañeros y a sus rivales.

**De tramos por equipo a tramos de diez jugadores.** `lineup_stints` guarda
tramos POR EQUIPO: el del local se corta cuando cambia el local, no cuando
cambia el visitante. `build_segments` cruza los tramos de los dos equipos de
un partido y los parte en cada frontera de cualquiera de los dos. El marcador
en cada frontera se conoce EXACTO —toda frontera es apertura o cierre de
algún tramo, y cada tramo guarda con qué margen entró y cuántos puntos hubo
dentro—, así que la diferencia de cada segmento no es una estimación
repartida a ojo.

**Unidades**: diferencia de puntos por 40 minutos, igual que el resto de la
interfaz (no por 100 posesiones: `lineup_stints` no guarda posesiones, ver
§5 de la propuesta 07). Un RAPM de +3 se lee "con él en pista, en lugar de un
jugador medio, el equipo gana 3 puntos más cada 40 minutos, descontado con
quién y contra quién jugó".

**El constructor** usa el modelo aditivo: el valor proyectado de un quinteto
es la suma del RAPM de sus cinco. No capta química (dos que se estorban),
por eso cada propuesta viaja con los minutos REALES que ese quinteto exacto
ha jugado y su diferencia observada: el entrenador ve a la vez lo que dice el
modelo y lo que ha pasado en pista.

Lógica pura sobre `pandas`/`numpy`, sin Streamlit ni SQLAlchemy (regla del
paquete, ver `app/analytics/__init__.py`).
"""
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

#: Penalización ridge, en minutos. Sale de un argumento bayesiano, no de un
#: ajuste a ojo: la varianza del margen en un minuto de juego ronda 5 pts²,
#: así que la diferencia por 40 de un tramo de `m` minutos tiene varianza
#: ≈ 40²·5/m = σ²/m con σ² ≈ 8000; el impacto real de un jugador tiene una
#: desviación de unos 2,5 puntos por 40 (τ), y el ridge óptimo es
#: λ = σ²/τ² ≈ 1200. En la práctica: un jugador con
#: 1200 minutos en pista conserva la mitad de lo que diría la regresión sin
#: regularizar; uno con 300, una quinta parte. `cross_validate_ridge` permite
#: comprobarlo contra los datos servidos.
RIDGE_LAMBDA = 1200.0

#: Minutos mínimos en pista para que el RAPM de un jugador se enseñe como
#: fiable. Por debajo el ridge ya lo ha llevado casi a 0, pero se marca
#: igualmente para no presentar un "+0,3" como si fuera un dato.
MIN_RELIABLE_MINUTES = 300.0

#: Tramos más cortos que esto no entran en el ajuste: un segmento de un
#: segundo con un tiro libre en medio es una diferencia por 40 absurda que
#: solo pesa por su minuto, pero ensucia las tablas de depuración.
MIN_SEGMENT_SECONDS = 1.0

_STINT_COLUMNS = {
    "stint_id", "game_id", "team_id", "is_home", "start_seconds", "end_seconds",
    "points_for", "points_against", "margin_start", "player_id",
}


def build_segments(stint_rows: pd.DataFrame) -> pd.DataFrame:
    """Cruza los tramos de los dos equipos de cada partido en segmentos de diez jugadores.

    Args:
        stint_rows: una fila por (tramo, jugador), con al menos
            `stint_id, game_id, team_id, is_home, start_seconds, end_seconds,
            points_for, points_against, margin_start, player_id` (la salida
            de `queries.season_stint_rows`).

    Returns:
        Una fila por segmento: `game_id, start_seconds, end_seconds, minutes,
        margin_delta` (diferencia del LOCAL en ese segmento), `home_team_id,
        away_team_id, home_players, away_players` (tuplas de 5). Los tramos
        en que alguno de los dos equipos no tiene quinteto válido (huecos de
        la reconstrucción) no generan segmento.
    """
    columns = [
        "game_id", "start_seconds", "end_seconds", "minutes", "margin_delta",
        "home_team_id", "away_team_id", "home_players", "away_players",
    ]
    if stint_rows.empty:
        return pd.DataFrame(columns=columns)
    missing = _STINT_COLUMNS - set(stint_rows.columns)
    if missing:
        raise ValueError(f"Faltan columnas en los tramos: {sorted(missing)}")

    stints = (
        stint_rows.groupby("stint_id")
        .agg(
            game_id=("game_id", "first"),
            team_id=("team_id", "first"),
            is_home=("is_home", "first"),
            start=("start_seconds", "first"),
            end=("end_seconds", "first"),
            pf=("points_for", "first"),
            pa=("points_against", "first"),
            margin_start=("margin_start", "first"),
        )
        .reset_index()
    )
    players = stint_rows.sort_values("player_id").groupby("stint_id")["player_id"].agg(tuple)
    stints["players"] = stints["stint_id"].map(players)
    stints = stints[stints["players"].map(len) == 5]
    # Margen desde el punto de vista del LOCAL, en la apertura y el cierre.
    sign = np.where(stints["is_home"].astype(bool), 1, -1)
    stints = stints.assign(
        home_margin_start=sign * stints["margin_start"],
        home_margin_end=sign * (stints["margin_start"] + stints["pf"] - stints["pa"]),
    )

    # Recorrido en Python puro: `itertuples`/`groupby` por partido sobre
    # columnas de tuplas es lo más lento de todo el cálculo con cientos de
    # partidos, y aquí solo se leen registros.
    by_game: Dict[str, List[dict]] = {}
    for rec in stints.to_dict("records"):
        by_game.setdefault(rec["game_id"], []).append(rec)

    rows: List[dict] = []
    for game_id, game in by_game.items():
        home_recs = sorted((r for r in game if r["is_home"]), key=lambda r: r["start"])
        away_recs = sorted((r for r in game if not r["is_home"]), key=lambda r: r["start"])
        if not home_recs or not away_recs:
            continue
        # Marcador conocido en cada frontera: la apertura o el cierre de
        # cualquier tramo. Si dos tramos dicen algo distinto en el mismo
        # segundo (canasta y cambio en el mismo instante, el orden de la
        # fuente decide) se queda el primero: el error es de una canasta en
        # un segmento de duración casi nula.
        margin_at: Dict[float, int] = {}
        for rec in game:
            margin_at.setdefault(float(rec["start"]), int(rec["home_margin_start"]))
            margin_at.setdefault(float(rec["end"]), int(rec["home_margin_end"]))
        bounds = sorted(margin_at)

        hi = ai = 0
        for t0, t1 in zip(bounds, bounds[1:]):
            if t1 - t0 < MIN_SEGMENT_SECONDS:
                continue
            while hi < len(home_recs) and home_recs[hi]["end"] <= t0:
                hi += 1
            while ai < len(away_recs) and away_recs[ai]["end"] <= t0:
                ai += 1
            if hi >= len(home_recs) or ai >= len(away_recs):
                break
            h, a = home_recs[hi], away_recs[ai]
            if h["start"] > t0 or h["end"] < t1 or a["start"] > t0 or a["end"] < t1:
                continue  # alguno de los dos está en un hueco sin quinteto válido
            rows.append({
                "game_id": game_id,
                "start_seconds": t0,
                "end_seconds": t1,
                "minutes": (t1 - t0) / 60.0,
                "margin_delta": margin_at[t1] - margin_at[t0],
                "home_team_id": h["team_id"],
                "away_team_id": a["team_id"],
                "home_players": h["players"],
                "away_players": a["players"],
            })
    return pd.DataFrame(rows, columns=columns)


def _design(segments: pd.DataFrame, player_index: Dict[str, int]):
    """Índices de columna, signos, pesos y objetivo de cada segmento (forma dispersa)."""
    n = len(segments)
    idx = np.empty((n, 10), dtype=np.int64)
    for i, (home, away) in enumerate(zip(segments["home_players"], segments["away_players"])):
        idx[i, :5] = [player_index[p] for p in home]
        idx[i, 5:] = [player_index[p] for p in away]
    signs = np.concatenate([np.ones(5), -np.ones(5)])
    weights = segments["minutes"].to_numpy(dtype=float)
    # Objetivo: diferencia del local por 40 minutos en el segmento.
    target = 40.0 * segments["margin_delta"].to_numpy(dtype=float) / weights
    return idx, signs, weights, target


def _normal_equations(idx, signs, weights, target, n_players: int):
    """`XᵀWX` y `XᵀWy` sin materializar X (P+1 columnas: jugadores + ventaja de campo).

    Cada segmento toca solo 11 columnas (diez jugadores y el término de
    campo), así que se acumula con `bincount` sobre índices planos en vez de
    construir una matriz de segmentos × jugadores casi toda a cero.
    """
    size = n_players + 1
    full_idx = np.hstack([idx, np.full((len(idx), 1), n_players)])
    full_signs = np.concatenate([signs, [1.0]])
    cols = full_idx.shape[1]
    rows_flat = np.repeat(full_idx, cols, axis=1)            # i de cada par (i, j)
    cols_flat = np.tile(full_idx, (1, cols))                 # j de cada par (i, j)
    pair_sign = np.outer(full_signs, full_signs).ravel()
    flat = (rows_flat * size + cols_flat).ravel()
    xtx = np.bincount(flat, weights=(weights[:, None] * pair_sign[None, :]).ravel(), minlength=size * size)
    xty = np.bincount(
        full_idx.ravel(),
        weights=(full_signs[None, :] * (weights * target)[:, None]).ravel(),
        minlength=size,
    )
    return xtx.reshape(size, size), xty


def _solve(xtx: np.ndarray, xty: np.ndarray, ridge: float) -> np.ndarray:
    penalty = np.full(len(xty), ridge)
    penalty[-1] = 1e-6  # la ventaja de campo no se regulariza
    return np.linalg.solve(xtx + np.diag(penalty), xty)


def fit_rapm(segments: pd.DataFrame, ridge: float = RIDGE_LAMBDA) -> dict:
    """Ajusta el RAPM sobre segmentos de diez jugadores.

    Returns:
        `{"players": DataFrame, "home_advantage": float, "segments": int,
        "ridge": float}`. `players` trae `player_id, rapm, minutes,
        reliable`, ordenado por `rapm`. `home_advantage` es la ventaja de
        campo estimada en puntos por 40 (control, no se enseña como dato de
        jugador). Con `segments` vacío, `players` sale vacío.
    """
    empty = pd.DataFrame(columns=["player_id", "rapm", "minutes", "reliable"])
    if segments.empty:
        return {"players": empty, "home_advantage": float("nan"), "segments": 0, "ridge": ridge}

    players = sorted(set().union(*segments["home_players"], *segments["away_players"]))
    player_index = {p: i for i, p in enumerate(players)}
    idx, signs, weights, target = _design(segments, player_index)
    xtx, xty = _normal_equations(idx, signs, weights, target, len(players))
    beta = _solve(xtx, xty, ridge)

    minutes = np.zeros(len(players))
    for k in range(10):
        np.add.at(minutes, idx[:, k], weights)
    df = pd.DataFrame({"player_id": players, "rapm": beta[:-1], "minutes": minutes})
    df["reliable"] = df["minutes"] >= MIN_RELIABLE_MINUTES
    df = df.sort_values("rapm", ascending=False).reset_index(drop=True)
    return {"players": df, "home_advantage": float(beta[-1]), "segments": len(segments), "ridge": ridge}


def cross_validate_ridge(
    segments: pd.DataFrame,
    grid: Sequence[float] = (300.0, 600.0, 1200.0, 2400.0, 4800.0),
    folds: int = 5,
    seed: int = 0,
) -> pd.DataFrame:
    """Error de predicción fuera de muestra para cada λ, con pliegues por PARTIDO.

    Por partido y no por segmento: dos segmentos del mismo partido comparten
    rival, pabellón y dinámica, y partirlos entre entrenamiento y prueba
    filtraría información. Sirve para comprobar `RIDGE_LAMBDA` contra la base
    de datos servida (`tools/` o una celda de cuaderno), no para la
    interfaz, que usa la constante.

    Returns:
        `ridge, mse` (error cuadrático ponderado por minutos), ordenado por `ridge`.
    """
    if segments.empty:
        return pd.DataFrame(columns=["ridge", "mse"])
    players = sorted(set().union(*segments["home_players"], *segments["away_players"]))
    player_index = {p: i for i, p in enumerate(players)}
    games = segments["game_id"].unique()
    rng = np.random.default_rng(seed)
    fold_of_game = dict(zip(games, rng.integers(0, folds, size=len(games))))
    fold = segments["game_id"].map(fold_of_game).to_numpy()

    idx, signs, weights, target = _design(segments, player_index)
    total_xtx, total_xty = _normal_equations(idx, signs, weights, target, len(players))
    errors = {r: [0.0, 0.0] for r in grid}
    for f in range(folds):
        test = fold == f
        if not test.any():
            continue
        t_xtx, t_xty = _normal_equations(idx[test], signs, weights[test], target[test], len(players))
        design_test = np.hstack([idx[test], np.full((test.sum(), 1), len(players))])
        signs_test = np.concatenate([signs, [1.0]])
        for r in grid:
            beta = _solve(total_xtx - t_xtx, total_xty - t_xty, r)
            pred = (beta[design_test] * signs_test).sum(axis=1)
            errors[r][0] += float((weights[test] * (target[test] - pred) ** 2).sum())
            errors[r][1] += float(weights[test].sum())
    return pd.DataFrame(
        [{"ridge": r, "mse": num / den if den else float("nan")} for r, (num, den) in errors.items()]
    ).sort_values("ridge").reset_index(drop=True)


def observed_lineups(segments: pd.DataFrame, team_id: str) -> pd.DataFrame:
    """Minutos y diferencia REALES de cada quinteto exacto de `team_id`, desde los segmentos.

    Returns:
        `players` (tupla ordenada de 5), `minutes`, `plus_minus`, `per_40`.
    """
    columns = ["players", "minutes", "plus_minus", "per_40"]
    if segments.empty:
        return pd.DataFrame(columns=columns)
    home = segments[segments["home_team_id"] == team_id]
    away = segments[segments["away_team_id"] == team_id]
    rows = pd.concat([
        pd.DataFrame({"players": home["home_players"], "minutes": home["minutes"], "pm": home["margin_delta"]}),
        pd.DataFrame({"players": away["away_players"], "minutes": away["minutes"], "pm": -away["margin_delta"]}),
    ])
    if rows.empty:
        return pd.DataFrame(columns=columns)
    agg = rows.groupby("players").agg(minutes=("minutes", "sum"), plus_minus=("pm", "sum")).reset_index()
    agg["per_40"] = 40.0 * agg["plus_minus"] / agg["minutes"]
    return agg[columns]


def _position_ok(positions: Sequence[Optional[str]], required: Dict[str, int]) -> bool:
    for label, count in required.items():
        if sum(1 for p in positions if p and p.strip().lower() == label.lower()) < count:
            return False
    return True


def best_lineups(
    rapm: pd.DataFrame,
    candidates: Iterable[str],
    *,
    observed: Optional[pd.DataFrame] = None,
    positions: Optional[Dict[str, Optional[str]]] = None,
    required_positions: Optional[Dict[str, int]] = None,
    must_include: Iterable[str] = (),
    top: int = 10,
) -> pd.DataFrame:
    """Los `top` quintetos con mejor proyección entre los jugadores disponibles.

    Args:
        rapm: `fit_rapm(...)["players"]`. Un candidato sin fila (nunca ha
            pisado un tramo válido) cuenta como 0: jugador medio, que es
            exactamente lo que el ridge diría de él.
        candidates: jugadores disponibles (el entrenador quita lesionados,
            no convocados, cargados de minutos...).
        observed: `observed_lineups(...)` del equipo, para acompañar cada
            proyección con lo que ese quinteto exacto ha hecho de verdad.
        positions: `{player_id: posición}` para `required_positions`.
        required_positions: p.ej. `{"Base": 1, "Pívot": 1}`. Se compara
            por igualdad exacta de etiqueta (sin mayúsculas): "Pívot" no
            cuenta un "Ala-pívot". Se ignora si no se pasa `positions`.
        must_include: jugadores que tienen que estar en todos los quintetos
            (p.ej. "¿con quién rodeo a Howard?").

    Returns:
        `players` (tupla de ids), `projected_per_40`, `observed_minutes`,
        `observed_per_40`. Vacío si hay menos de cinco candidatos.
    """
    columns = ["players", "projected_per_40", "observed_minutes", "observed_per_40"]
    pool = sorted(set(candidates) | set(must_include))
    fixed = sorted(set(must_include))
    if len(pool) < 5 or len(fixed) > 5:
        return pd.DataFrame(columns=columns)
    value = dict(zip(rapm["player_id"], rapm["rapm"])) if not rapm.empty else {}
    free = [p for p in pool if p not in fixed]

    results = []
    for combo in combinations(free, 5 - len(fixed)):
        lineup = tuple(sorted(fixed + list(combo)))
        if required_positions and positions is not None:
            if not _position_ok([positions.get(p) for p in lineup], required_positions):
                continue
        results.append((lineup, sum(value.get(p, 0.0) for p in lineup)))
    if not results:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(results, columns=["players", "projected_per_40"])
    df = df.sort_values("projected_per_40", ascending=False).head(top).reset_index(drop=True)
    if observed is not None and not observed.empty:
        obs = observed.set_index("players")
        df["observed_minutes"] = df["players"].map(obs["minutes"]).fillna(0.0)
        df["observed_per_40"] = df["players"].map(obs["per_40"])
    else:
        df["observed_minutes"] = 0.0
        df["observed_per_40"] = float("nan")
    return df[columns]


def replacement_options(
    rapm: pd.DataFrame, lineup: Sequence[str], out_player: str, candidates: Iterable[str], top: int = 3
) -> pd.DataFrame:
    """Mejores sustitutos de `out_player` en `lineup` (falta, lesión o descanso).

    Returns:
        `player_id, rapm, delta` — `delta` es el cambio de la proyección del
        quinteto por 40 al hacer el cambio (negativo = se pierde).
    """
    value = dict(zip(rapm["player_id"], rapm["rapm"])) if not rapm.empty else {}
    base = value.get(out_player, 0.0)
    rows = [
        {"player_id": p, "rapm": value.get(p, 0.0), "delta": value.get(p, 0.0) - base}
        for p in candidates
        if p not in lineup
    ]
    df = pd.DataFrame(rows, columns=["player_id", "rapm", "delta"])
    return df.sort_values("rapm", ascending=False).head(top).reset_index(drop=True)


def team_player_minutes(segments: pd.DataFrame, team_id: str) -> pd.Series:
    """Minutos de cada jugador CON `team_id` en los segmentos (un traspasado solo suma los suyos aquí)."""
    if segments.empty:
        return pd.Series(dtype=float)
    home = segments[segments["home_team_id"] == team_id][["home_players", "minutes"]]
    away = segments[segments["away_team_id"] == team_id][["away_players", "minutes"]]
    rows = pd.concat([
        home.rename(columns={"home_players": "players"}),
        away.rename(columns={"away_players": "players"}),
    ]).explode("players")
    if rows.empty:
        return pd.Series(dtype=float)
    return rows.groupby("players")["minutes"].sum().sort_values(ascending=False)
