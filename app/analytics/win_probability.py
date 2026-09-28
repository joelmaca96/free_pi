"""Probabilidad de victoria y momentos clave de un partido (propuesta 18, hoja de ruta A4).

`doc/features/propuestas/18_momentos_clave.md`. La vista de parciales (propuesta
01) ordena los tramos por el tamaño del parcial, y eso engaña: un 8-0 en el
minuto 3 pesa mucho menos que un 5-0 en el 38 con el partido igualado. Este
módulo mide cada tramo en la moneda que de verdad importa —**cuánto movió la
probabilidad de ganar**— y con eso da las 5 jugadas que decidieron el partido,
su reparto por quintetos (WPA) y una lista de clips para el vídeo.

**El modelo** (`fit_win_probability`): regresión logística de "gana el local"
sobre el estado del marcador, ajustada con TODOS los partidos de la temporada
(no solo los del Baskonia), por Newton/IRLS escrito sobre `numpy` —
`scikit-learn`/`statsmodels`/`scipy` no están en `app/requirements.txt`, mismo
criterio que `win_thresholds.fit_logistic_model`. Variables, con `t` = segundos
que quedan del periodo en curso y `m` = margen del local:

- constante: la ventaja de jugar en casa que no depende del reloj (la que
  queda, por ejemplo, en un empate a falta de 0 s → prórroga);
- `m / sqrt(t + c)`: el corazón del modelo. Un margen vale más cuanto menos
  tiempo queda para remontarlo, y la incertidumbre de lo que falta crece
  como la raíz del tiempo (paseo aleatorio, Stern 1994). `c` (`TIME_OFFSET_S`)
  evita dividir por cero en la bocina;
- `sqrt(t + c) / sqrt(2400)`: la ventaja de campo que todavía queda por
  jugar (se diluye a medida que se acaba el partido);
- `m / sqrt(2400)`: un término lineal en el margen que recoge lo que la raíz
  no explica. Si al ajustarlo la probabilidad dejara de crecer con el margen
  en algún punto del partido (pasa con muestras raras), se descarta y se
  reajusta sin él: **la monotonía en el margen no se negocia**.

**Estados muestreados a intervalos fijos** (`SAMPLE_INTERVAL_S`, 30 s de
juego), no evento a evento: un tramo con muchos eventos tipados (faltas y
rebotes seguidos) pesaría más en el ajuste solo por estar más documentado.
Cada partido aporta ~80 estados, todos con el resultado final del partido
como respuesta. Los estados NO son independientes (son el mismo partido): el
ajuste da buenas probabilidades, pero sus errores estándar no valdrían, y por
eso no se enseñan.

**Prórrogas**: se tratan como un final de partido más corto — un estado de la
prórroga lleva como tiempo restante lo que queda DE ESA prórroga (5 min como
mucho). Un empate con 3 minutos de prórroga por delante se parece mucho más a
un empate con 3 minutos del último cuarto que a nada que ocurra con 30 minutos
por jugar. Y un empate al final del tiempo reglamentario (t = 0, m = 0) queda
en la constante: la probabilidad de que el local gane la prórroga.

**Modelo de reserva** (`normal_model`): con menos de `MIN_GAMES_FOR_FIT`
partidos en la temporada (arranque de temporada, base de datos de pruebas) la
logística no tiene muestra para decir nada fiable. Se usa entonces la
aproximación normal clásica: el margen que falta por jugar es un paseo
aleatorio con deriva = ventaja de campo y desviación `σ·sqrt(t)`, calibrada con
cifras típicas de ACB/Euroliga (`FALLBACK_SIGMA_GAME`, `FALLBACK_HOME_EDGE`).
La interfaz dice cuál de los dos modelos está usando.

**Ventaja previa** (`pregame_edge`, opcional): puntos de ventaja esperados del
local sobre 40 minutos ADICIONALES a la ventaja de campo media (p.ej. la
predicción de la propuesta A2 cuando exista). Se suma al margen como lo que se
espera que el local gane en el tiempo que falta: `m + edge · t/2400`. Hoy la
pantalla la deja a 0 (solo marcador, reloj y campo).

Es lógica pura sobre `pandas`/`numpy`, sin Streamlit ni SQLAlchemy — las
consultas viven en `app/data/queries_win_probability.py`.
"""
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

#: Tiempo reglamentario y duración de una prórroga, en segundos (mismos
#: valores que `queries._REGULATION_SECONDS`/`_OVERTIME_SECONDS`).
REGULATION_SECONDS = 2400.0
OVERTIME_SECONDS = 300.0
QUARTER_SECONDS = 600.0

#: Cada cuántos segundos de juego se toma una foto del marcador por partido
#: para el ajuste (ver docstring del módulo).
SAMPLE_INTERVAL_S = 30.0

#: `c` de `sqrt(t + c)`: evita la división por cero en la bocina y suaviza los
#: últimos segundos (con 20 s, un +2 a falta de 20 s ronda el 85-90%).
TIME_OFFSET_S = 20.0

#: Partidos mínimos de la temporada para ajustar la logística; por debajo se
#: usa el modelo normal de reserva. 60 partidos son ~5.000 estados.
MIN_GAMES_FOR_FIT = 60

#: Regularización L2 del ajuste (sobre ~60.000 estados es despreciable; solo
#: está para que una muestra separable no mande los coeficientes a infinito).
RIDGE = 1.0

#: Modelo de reserva: desviación típica del margen final de un partido entero
#: y ventaja de campo, en puntos. Valores típicos de ACB/Euroliga (el margen
#: final tiene σ ≈ 12-13 puntos; jugar en casa vale ≈ 3 puntos).
FALLBACK_SIGMA_GAME = 12.0
FALLBACK_HOME_EDGE = 3.0

#: Ventana máxima de un "momento clave" y cuántos se devuelven.
KEY_MOMENT_WINDOW_S = 120.0
N_KEY_MOMENTS = 5

#: Un momento que no mueve al menos 1 punto porcentual no es un momento.
MIN_MOMENT_WPA = 0.01

#: Margen del clip de vídeo: segundos antes y después de la ventana. El
#: "antes" es más largo porque el marcador se observa en el SIGUIENTE evento
#: tipado, no en la canasta (los tiros no son eventos): la jugada de verdad
#: empieza antes de lo que dice la escalera.
CLIP_PAD_BEFORE_S = 10.0
CLIP_PAD_AFTER_S = 5.0

NO_LINEUP = "(sin tramo de quinteto)"


# ================================================================ reloj ==


def seconds_remaining(seconds) -> np.ndarray:
    """Segundos que quedan del periodo "final" en curso, dado el segundo absoluto.

    En tiempo reglamentario, lo que falta para el minuto 40 (el modelo no sabe
    si habrá prórroga). En la prórroga, lo que falta para el final de ESA
    prórroga (ver docstring del módulo). Nunca negativo.
    """
    s = np.asarray(seconds, dtype=float)
    regulation = np.clip(REGULATION_SECONDS - s, 0.0, None)
    overtime_end = REGULATION_SECONDS + OVERTIME_SECONDS * np.ceil(
        np.clip(s - REGULATION_SECONDS, 0.0, None) / OVERTIME_SECONDS
    )
    overtime = np.clip(overtime_end - s, 0.0, None)
    return np.where(s <= REGULATION_SECONDS, regulation, overtime)


def period_clock(seconds: float, *, at_end: bool = False) -> Tuple[str, str]:
    """Segundo absoluto -> `('Q3', '04:12')`, con el reloj hacia atrás como en el acta.

    El mismo instante es dos cosas en el cambio de cuarto (el segundo 600 es
    "Q1 00:00" y "Q2 10:00"): `at_end=True` elige el cuarto que ACABA (para el
    final de un clip), `False` el que empieza (para el inicio).
    """
    s = max(float(seconds), 0.0)
    probe = s - 1e-6 if (at_end and s > 0) else s
    if probe < REGULATION_SECONDS:
        index = int(probe // QUARTER_SECONDS)
        start, length, label = index * QUARTER_SECONDS, QUARTER_SECONDS, f"Q{index + 1}"
    else:
        index = int((probe - REGULATION_SECONDS) // OVERTIME_SECONDS)
        start = REGULATION_SECONDS + index * OVERTIME_SECONDS
        length, label = OVERTIME_SECONDS, f"OT{index + 1}"
    remaining = int(round(min(max(start + length - s, 0.0), length)))
    return label, f"{remaining // 60:02d}:{remaining % 60:02d}"


def _clock_text(seconds: float, *, at_end: bool = False) -> str:
    quarter, clock = period_clock(seconds, at_end=at_end)
    return f"{quarter} {clock}"


# ================================================================ modelo ==


@dataclass(frozen=True)
class WinProbabilityModel:
    """Modelo ajustado (o de reserva). Inmutable y serializable (va a la caché).

    Attributes:
        kind: `'logistic'` (ajustado con la liga) o `'normal'` (reserva).
        coef: coeficientes de la logística en el orden de `_features`
            (vacío en el modelo normal). Con 3 coeficientes, el término lineal
            en el margen se descartó (ver docstring del módulo).
        n_games / n_states: muestra del ajuste (0 en el de reserva).
        home_win_rate: % de victorias locales en la muestra (referencia).
    """

    kind: str
    coef: Tuple[float, ...] = ()
    n_games: int = 0
    n_states: int = 0
    home_win_rate: Optional[float] = None

    @property
    def description(self) -> str:
        """Una línea para la interfaz: qué modelo es y con cuánta muestra."""
        if self.kind == "logistic":
            return (
                f"Logística ajustada con {self.n_games} partidos de la temporada "
                f"({self.n_states:,} estados de marcador cada {SAMPLE_INTERVAL_S:.0f} s)".replace(",", ".")
            )
        return (
            f"Aproximación normal de reserva (σ = {FALLBACK_SIGMA_GAME:.0f} puntos por partido, "
            f"ventaja de campo {FALLBACK_HOME_EDGE:.0f} puntos): la temporada tiene menos de "
            f"{MIN_GAMES_FOR_FIT} partidos con play-by-play para ajustar el modelo"
        )


def normal_model() -> WinProbabilityModel:
    """El modelo analítico de reserva (ver docstring del módulo)."""
    return WinProbabilityModel(kind="normal")


def _features(margin, t_remaining, *, with_linear: bool = True) -> np.ndarray:
    m = np.asarray(margin, dtype=float)
    t = np.asarray(t_remaining, dtype=float)
    root = np.sqrt(t + TIME_OFFSET_S)
    columns = [np.ones_like(m), m / root, root / math.sqrt(REGULATION_SECONDS)]
    if with_linear:
        columns.append(m / math.sqrt(REGULATION_SECONDS))
    return np.column_stack(columns)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))


def _normal_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf, otypes=[float])(np.asarray(z, dtype=float) / math.sqrt(2.0)))


def fit_logistic_irls(X: np.ndarray, y: np.ndarray, *, ridge: float = RIDGE, max_iter: int = 50) -> np.ndarray:
    """Regresión logística por Newton-Raphson (IRLS) con una L2 mínima.

    Converge en 5-8 iteraciones sobre ~60.000 filas y 4 columnas; la L2 solo
    evita que una muestra separable (pasa en tests con pocos partidos) mande
    los coeficientes a infinito.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    beta = np.zeros(X.shape[1])
    penalty = ridge * np.eye(X.shape[1])
    for _ in range(max_iter):
        p = _sigmoid(X @ beta)
        w = p * (1.0 - p)
        gradient = X.T @ (y - p) - penalty @ beta
        hessian = X.T @ (X * w[:, None]) + penalty
        step = np.linalg.solve(hessian, gradient)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def _is_monotonic_in_margin(coef: np.ndarray) -> bool:
    """dP/dm > 0 en todo el partido: `b1/sqrt(t+c) + b3 > 0` para todo t ≤ 2400."""
    b1 = coef[1]
    b3 = coef[3] / math.sqrt(REGULATION_SECONDS) if len(coef) > 3 else 0.0
    return b1 > 0 and b1 / math.sqrt(REGULATION_SECONDS + TIME_OFFSET_S) + b3 > 0


def fit_win_probability(states: pd.DataFrame, *, min_games: int = MIN_GAMES_FOR_FIT) -> WinProbabilityModel:
    """Ajusta el modelo con los estados de la liga, o cae al de reserva.

    Args:
        states: salida de `sample_game_states` concatenada para toda la liga
            (`game_id, seconds_remaining, margin, home_win`; margen del local).
        min_games: por debajo, modelo normal de reserva.
    """
    if states is None or states.empty or "game_id" not in states:
        return normal_model()
    n_games = int(states["game_id"].nunique())
    if n_games < min_games:
        return normal_model()

    y = states["home_win"].to_numpy(dtype=float)
    margin = states["margin"].to_numpy(dtype=float)
    t = states["seconds_remaining"].to_numpy(dtype=float)
    home_win_rate = float(states.groupby("game_id")["home_win"].first().mean())

    coef = fit_logistic_irls(_features(margin, t), y)
    if not _is_monotonic_in_margin(coef):
        coef = fit_logistic_irls(_features(margin, t, with_linear=False), y)
        if not _is_monotonic_in_margin(coef):
            return normal_model()
    return WinProbabilityModel(
        kind="logistic",
        coef=tuple(float(c) for c in coef),
        n_games=n_games,
        n_states=int(len(states)),
        home_win_rate=home_win_rate,
    )


def predict_home_wp(model: WinProbabilityModel, margin, t_remaining, pregame_edge: float = 0.0) -> np.ndarray:
    """P(gana el local | margen del local, segundos restantes[, ventaja previa]).

    Con el partido acabado (`t = 0`) y el marcador no empatado, la respuesta
    es 1 o 0 exactos — no una probabilidad del modelo —: así la WPA de un
    partido suma exactamente "final − inicio" y el último escalón de la
    curva cae donde tiene que caer.
    """
    m = np.atleast_1d(np.asarray(margin, dtype=float))
    t = np.atleast_1d(np.asarray(t_remaining, dtype=float))
    m, t = np.broadcast_arrays(m, t)
    effective = m + float(pregame_edge) * t / REGULATION_SECONDS

    if model.kind == "logistic" and model.coef:
        coef = np.asarray(model.coef, dtype=float)
        X = _features(effective, t, with_linear=len(coef) > 3)
        p = _sigmoid(X @ coef)
    else:
        sigma_per_root_s = FALLBACK_SIGMA_GAME / math.sqrt(REGULATION_SECONDS)
        drift = FALLBACK_HOME_EDGE * t / REGULATION_SECONDS
        p = _normal_cdf((effective + drift) / (sigma_per_root_s * np.sqrt(t + TIME_OFFSET_S)))

    finished = (t <= 0) & (m != 0)
    return np.where(finished, (m > 0).astype(float), p)


# ============================================================== muestreo ==


def sample_game_states(
    steps: pd.DataFrame,
    home_win: bool,
    *,
    interval_s: float = SAMPLE_INTERVAL_S,
    game_id: Optional[str] = None,
) -> pd.DataFrame:
    """Fotos del marcador cada `interval_s` segundos de juego de UN partido.

    Args:
        steps: escalera del marcador desde el LOCAL (`queries._score_steps`
            con `team_id=None`): `seconds, margin`, con el 0-0 de salida y el
            cierre en el marcador oficial (su último escalón es el final).
        home_win: resultado final (la respuesta del modelo).

    Returns:
        `game_id, seconds, seconds_remaining, margin, home_win`. Se descartan
        los estados ya decididos (`t = 0` con el marcador no empatado): no
        aportan nada al ajuste y harían explotar `m / sqrt(t + c)`.
    """
    columns = ["game_id", "seconds", "seconds_remaining", "margin", "home_win"]
    if steps is None or len(steps) < 2:
        return pd.DataFrame(columns=columns)
    seconds = steps["seconds"].to_numpy(dtype=float)
    margin = steps["margin"].to_numpy(dtype=float)
    order = np.argsort(seconds, kind="stable")
    seconds, margin = seconds[order], margin[order]

    grid = np.arange(0.0, seconds[-1], float(interval_s))
    idx = np.clip(np.searchsorted(seconds, grid, side="right") - 1, 0, None)
    sampled_margin = margin[idx]
    t_rem = seconds_remaining(grid)
    keep = ~((t_rem <= 0) & (sampled_margin != 0))
    return pd.DataFrame({
        "game_id": game_id,
        "seconds": grid[keep],
        "seconds_remaining": t_rem[keep],
        "margin": sampled_margin[keep],
        "home_win": int(bool(home_win)),
    })[columns]


# ========================================================= curva del partido ==


def game_wp_curve(
    steps: pd.DataFrame,
    model: WinProbabilityModel,
    *,
    is_home: bool,
    pregame_edge: float = 0.0,
) -> pd.DataFrame:
    """Probabilidad de victoria en cada escalón del marcador, vista desde un equipo.

    Args:
        steps: `queries.game_score_steps(engine, game_id, team_id)` — escalera
            ya orientada a `team_id` (`score_for`, `score_against`, `margin`).
        is_home: si `team_id` es el local (el modelo razona desde el local).
        pregame_edge: ventaja previa del LOCAL (ver docstring del módulo).

    Returns:
        Las columnas de `steps` más `seconds_remaining`, `wp` (0-1, del
        equipo) y `wpa` (cambio de `wp` respecto al escalón anterior; 0 en el
        primero). Vacío si no hay escalera.
    """
    if steps is None or steps.empty:
        return pd.DataFrame(columns=list(getattr(steps, "columns", [])) + ["seconds_remaining", "wp", "wpa"])
    curve = steps.sort_values("seconds", kind="stable").reset_index(drop=True).copy()
    t_rem = seconds_remaining(curve["seconds"].to_numpy(dtype=float))
    margin = curve["margin"].to_numpy(dtype=float)
    home_margin = margin if is_home else -margin
    p_home = predict_home_wp(model, home_margin, t_rem, pregame_edge)
    curve["seconds_remaining"] = t_rem
    curve["wp"] = p_home if is_home else 1.0 - p_home
    curve["wpa"] = curve["wp"].diff().fillna(0.0)
    return curve


# ============================================================ momentos clave ==

_MOMENT_COLUMNS = [
    "rank", "start_seconds", "end_seconds", "duration_s",
    "quarter_start", "clock_start", "quarter_end", "clock_end",
    "score_before", "score_after", "margin_before", "margin_after",
    "points_for", "points_against", "wp_before", "wp_after", "wpa", "direction", "label",
]


def key_moments(
    curve: pd.DataFrame,
    *,
    n: int = N_KEY_MOMENTS,
    window_s: float = KEY_MOMENT_WINDOW_S,
    min_wpa: float = MIN_MOMENT_WPA,
) -> pd.DataFrame:
    """Los `n` tramos que más movieron la probabilidad de victoria (|ΔWP|).

    Mismo esqueleto que `queries.detect_runs` —ventana deslizante sobre la
    escalera, recorte de los segundos planos en los extremos, descarte de
    ventanas solapadas—, pero la magnitud que ordena es el cambio de
    probabilidad y no el de margen. Por eso un 5-0 en el minuto 38 con el
    partido igualado pasa por delante de un 10-0 en el minuto 3.

    Solo cuentan ventanas en las que CAMBIA el marcador: la probabilidad del
    que va por delante también sube sola con el reloj, y "no pasó nada
    durante dos minutos" no es una jugada que enseñar en vídeo.

    Args:
        curve: salida de `game_wp_curve`.

    Returns:
        Una fila por momento, del que más movió al que menos (`rank` 1..n):
        reloj de inicio y fin, marcador antes y después, puntos de cada lado,
        `wp_before`/`wp_after`/`wpa` (0-1, del equipo de la curva) y una
        etiqueta legible. Vacío si no hay escalera o nada llega a `min_wpa`.
    """
    if curve is None or len(curve) < 2:
        return pd.DataFrame(columns=_MOMENT_COLUMNS)

    seconds = curve["seconds"].to_numpy(dtype=float)
    margin = curve["margin"].to_numpy(dtype=float)
    wp = curve["wp"].to_numpy(dtype=float)
    total = len(seconds)

    candidates = []
    for i in range(total - 1):
        best_k, best = None, 0.0
        for k in range(i + 1, total):
            if seconds[k] - seconds[i] > window_s:
                break
            if margin[k] == margin[i]:
                continue
            delta = wp[k] - wp[i]
            if abs(delta) > abs(best):
                best_k, best = k, delta
        if best_k is None:
            continue
        # Recorte (igual que `detect_runs`): el momento empieza en el ÚLTIMO
        # escalón con el margen de partida y acaba en el PRIMERO con el de
        # llegada.
        start = i
        while start + 1 < best_k and margin[start + 1] == margin[i]:
            start += 1
        end = best_k
        while end - 1 > start and margin[end - 1] == margin[best_k]:
            end -= 1
        delta = wp[end] - wp[start]
        if abs(delta) < min_wpa:
            continue
        candidates.append((abs(delta), -(seconds[end] - seconds[start]), start, end, delta))

    candidates.sort(reverse=True)
    accepted = []
    for _, _, start, end, delta in candidates:
        if any(seconds[start] < seconds[b] and seconds[a] < seconds[end] for a, b, _ in accepted):
            continue
        accepted.append((start, end, delta))
        if len(accepted) >= n:
            break

    rows = []
    for rank, (start, end, delta) in enumerate(accepted, start=1):
        head, tail = curve.iloc[start], curve.iloc[end]
        points_for = int(tail["score_for"] - head["score_for"])
        points_against = int(tail["score_against"] - head["score_against"])
        quarter_start, clock_start = period_clock(head["seconds"])
        quarter_end, clock_end = period_clock(tail["seconds"], at_end=True)
        score_before = f"{int(head['score_for'])}-{int(head['score_against'])}"
        score_after = f"{int(tail['score_for'])}-{int(tail['score_against'])}"
        rows.append({
            "rank": rank,
            "start_seconds": float(head["seconds"]),
            "end_seconds": float(tail["seconds"]),
            "duration_s": float(tail["seconds"] - head["seconds"]),
            "quarter_start": quarter_start,
            "clock_start": clock_start,
            "quarter_end": quarter_end,
            "clock_end": clock_end,
            "score_before": score_before,
            "score_after": score_after,
            "margin_before": int(head["margin"]),
            "margin_after": int(tail["margin"]),
            "points_for": points_for,
            "points_against": points_against,
            "wp_before": float(wp[start]),
            "wp_after": float(wp[end]),
            "wpa": float(delta),
            "direction": "favor" if delta > 0 else "contra",
            # Sin flecha a propósito (mismo motivo que `detect_runs`: la
            # etiqueta viaja al log del asistente y cp1252 no la tiene).
            "label": (
                f"{quarter_start} {clock_start} a {quarter_end} {clock_end} · "
                f"parcial {points_for}-{points_against} "
                f"({score_before} a {score_after}) · "
                f"{100 * wp[start]:.0f}% a {100 * wp[end]:.0f}% ({100 * delta:+.0f} pp)"
            ),
        })
    return pd.DataFrame(rows, columns=_MOMENT_COLUMNS)


# ======================================================== WPA por quinteto ==


def _lineup_table(stints: pd.DataFrame) -> pd.DataFrame:
    """Un tramo por fila con su quinteto como texto (nombres ordenados)."""
    grouped = stints.groupby("stint_id", sort=False)
    table = grouped.agg(
        start_seconds=("start_seconds", "first"),
        end_seconds=("end_seconds", "first"),
        points_for=("points_for", "first"),
        points_against=("points_against", "first"),
    )
    table["lineup"] = grouped["player_name"].apply(lambda names: " · ".join(sorted(str(n) for n in names)))
    return table.reset_index()


def lineup_wpa(
    curve: pd.DataFrame,
    stints: pd.DataFrame,
    model: WinProbabilityModel,
    *,
    is_home: bool,
    team_is_curve_team: bool = True,
    pregame_edge: float = 0.0,
) -> pd.DataFrame:
    """WPA por quinteto: cuánta probabilidad de victoria ganó (o perdió) cada cinco en pista.

    Se evalúa la probabilidad en cada escalón del marcador Y en cada cambio
    de quinteto (con el marcador vigente en ese instante), y cada trozo
    `(t_anterior, t]` se le apunta al tramo que estaba en pista. Así la
    deriva por reloj (ir ganando y que pase el tiempo también sube la
    probabilidad) va a quien estaba en pista mientras pasaba, y la suma de
    todos los quintetos es EXACTAMENTE `wp_final − wp_inicial` del equipo
    — si algún trozo no cae en ningún tramo (partido con tramos incompletos),
    va a una fila "(sin tramo de quinteto)" en vez de perderse.

    Args:
        curve: `game_wp_curve` (desde el equipo de la curva).
        stints: `queries.game_stints` del equipo cuyos quintetos se miden
            (una fila por tramo y jugador).
        is_home: si el equipo de la CURVA es el local.
        team_is_curve_team: `False` para los quintetos del rival: su WPA es
            la de la curva con el signo cambiado.

    Returns:
        `lineup, stints, minutes, points_for, points_against, wpa` (0-1, desde
        el equipo de los quintetos), de más a menos WPA.
    """
    columns = ["lineup", "stints", "minutes", "points_for", "points_against", "wpa"]
    if curve is None or len(curve) < 2:
        return pd.DataFrame(columns=columns)

    curve = curve.sort_values("seconds", kind="stable")
    curve_seconds = curve["seconds"].to_numpy(dtype=float)
    curve_margin = curve["margin"].to_numpy(dtype=float)
    game_end = float(curve_seconds[-1])
    sign = 1.0 if team_is_curve_team else -1.0

    table = _lineup_table(stints) if stints is not None and not stints.empty else pd.DataFrame(
        columns=["stint_id", "start_seconds", "end_seconds", "points_for", "points_against", "lineup"]
    )

    boundaries = np.concatenate([
        curve_seconds,
        table["start_seconds"].to_numpy(dtype=float),
        table["end_seconds"].to_numpy(dtype=float),
    ])
    times = np.unique(np.clip(boundaries, curve_seconds[0], game_end))
    idx = np.clip(np.searchsorted(curve_seconds, times, side="right") - 1, 0, None)
    margin = curve_margin[idx]
    t_rem = seconds_remaining(times)
    p_home = predict_home_wp(model, margin if is_home else -margin, t_rem, pregame_edge)
    wp = p_home if is_home else 1.0 - p_home
    # Los extremos, anclados a la curva: mismo inicio y mismo final exactos.
    wp[0] = float(curve["wp"].iloc[0])
    wp[-1] = float(curve["wp"].iloc[-1])
    deltas = np.diff(wp) * sign
    mids = (times[:-1] + times[1:]) / 2.0

    starts = table["start_seconds"].to_numpy(dtype=float)
    ends = table["end_seconds"].to_numpy(dtype=float)
    lineups = table["lineup"].to_numpy(dtype=object)
    owner = []
    for mid in mids:
        hit = np.flatnonzero((starts <= mid) & (mid < ends))
        owner.append(lineups[hit[0]] if len(hit) else NO_LINEUP)
    per_piece = pd.DataFrame({"lineup": owner, "wpa": deltas})
    wpa = per_piece.groupby("lineup")["wpa"].sum()

    if table.empty:
        result = pd.DataFrame({"lineup": wpa.index, "wpa": wpa.to_numpy()})
        result["stints"], result["minutes"], result["points_for"], result["points_against"] = 0, 0.0, 0, 0
    else:
        table["seconds"] = (table["end_seconds"] - table["start_seconds"]).clip(lower=0.0)
        summary = table.groupby("lineup").agg(
            stints=("stint_id", "count"),
            seconds=("seconds", "sum"),
            points_for=("points_for", "sum"),
            points_against=("points_against", "sum"),
        )
        summary["minutes"] = summary["seconds"] / 60.0
        result = summary.join(wpa, how="outer").reset_index().rename(columns={"index": "lineup"})
        result["wpa"] = result["wpa"].fillna(0.0)
        result[["stints", "points_for", "points_against"]] = (
            result[["stints", "points_for", "points_against"]].fillna(0).astype(int)
        )
        result["minutes"] = result["minutes"].fillna(0.0)
    result = result[columns]
    # La fila "sin tramo" solo si se lleva algo: un 0,0 de relleno despista.
    result = result[(result["lineup"] != NO_LINEUP) | (result["wpa"].abs() > 1e-9)]
    return result.sort_values("wpa", ascending=False).reset_index(drop=True)


# ================================================================ clips ==


def clip_list(
    moments: pd.DataFrame,
    *,
    game_end: Optional[float] = None,
    pad_before_s: float = CLIP_PAD_BEFORE_S,
    pad_after_s: float = CLIP_PAD_AFTER_S,
    team_label: str = "",
) -> pd.DataFrame:
    """Lista de clips de vídeo de los momentos clave, lista para CSV.

    Es la "exportación de clips" que el índice de propuestas dejó aparcada: sin
    conocer el formato del editor de vídeo del club, se ofrece lo universal —
    cuarto y reloj de inicio y fin, en el mismo formato que el acta— y el
    segundo absoluto por si el sistema de vídeo trabaja con tiempo corrido.

    Returns:
        `orden, cuarto_inicio, reloj_inicio, cuarto_fin, reloj_fin,
        segundo_inicio, segundo_fin, descripcion`, en orden cronológico.
    """
    columns = [
        "orden", "cuarto_inicio", "reloj_inicio", "cuarto_fin", "reloj_fin",
        "segundo_inicio", "segundo_fin", "descripcion",
    ]
    if moments is None or moments.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for moment in moments.sort_values("start_seconds").to_dict("records"):
        start = max(float(moment["start_seconds"]) - pad_before_s, 0.0)
        end = float(moment["end_seconds"]) + pad_after_s
        if game_end is not None:
            end = min(end, float(game_end))
        quarter_start, clock_start = period_clock(start)
        quarter_end, clock_end = period_clock(end, at_end=True)
        who = f" {team_label}" if team_label else ""
        rows.append({
            "orden": int(moment["rank"]),
            "cuarto_inicio": quarter_start,
            "reloj_inicio": clock_start,
            "cuarto_fin": quarter_end,
            "reloj_fin": clock_end,
            "segundo_inicio": round(start, 1),
            "segundo_fin": round(end, 1),
            "descripcion": (
                f"Momento clave #{int(moment['rank'])}: parcial {moment['points_for']}-"
                f"{moment['points_against']}{who} ({moment['score_before']} a {moment['score_after']}), "
                f"probabilidad de victoria {100 * moment['wp_before']:.0f}% a "
                f"{100 * moment['wp_after']:.0f}% ({100 * moment['wpa']:+.0f} pp)"
            ),
        })
    return pd.DataFrame(rows, columns=columns)
