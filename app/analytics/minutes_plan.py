"""Planificador de minutos con carga (propuesta 17).

Diseño en `doc/features/propuestas/17_planificador_de_minutos.md`. El
constructor de quintetos (propuesta 12) dice QUÉ cinco sacar; la decisión de
verdad del día de partido es **cuántos minutos juega cada uno**: 200 minutos
de jugador (5 × 40) que repartir sin pasarse con quien viene cargado.

**Modelo.** Aditivo, el mismo que el constructor: cada minuto de un jugador
sustituye a un jugador medio, así que el margen proyectado del partido es

    margen = Σ_i rapm_i · m_i / 40

(RAPM en +/- por 40; con los mismos cinco los 40 minutos, sale la suma de sus
cinco RAPM, igual que `impact.best_lineups`). Se maximiza sujeto a:

- `Σ m_i = 200` (sin prórroga);
- `mín_i ≤ m_i ≤ tope_i`, con `tope_i ≤ 40`; no disponible ⇒ `m_i = 0`;
- por posición, `Σ_{i ∈ g} m_i ≥ F_g` (p. ej. 40 minutos de base y 40 de
  pívot) cuando la posición se conoce.

**Por qué el suelo de 40 minutos por posición es exacto, no una
aproximación.** Lo que se quiere es que en TODO momento haya un base (y un
pívot) en pista. Es necesario: si hay un base en cada instante, los bases
suman ≥ 40 minutos. Y es suficiente, por la regla de McNaughton (reparto
"envolvente"): se ponen los minutos de todos los jugadores uno detrás de otro
en una cinta de 200 minutos —los bases seguidos, luego el resto, los pívots
seguidos— y se corta en cinco tiras de 40, una por puesto en pista. El minuto
`t` del partido lo juegan quienes ocupan las posiciones `t, t+40, …, t+160`
de la cinta. Un jugador con `m_i ≤ 40` nunca aparece dos veces en el mismo
instante, y un bloque contiguo de ≥ 40 minutos (los bases) cubre todos los
restos módulo 40, es decir, todos los instantes. Con `k` jugadores de un
grupo a la vez, el suelo exacto es `40·k`. Vale a la vez para todos los
grupos porque son disjuntos (cada jugador tiene una sola posición). Lo que
NO dice el suelo es qué rotación concreta usar: eso sigue siendo del cuerpo
técnico.

**Por qué el reparto voraz es óptimo.** Los grupos de posición son disjuntos
(cada jugador tiene UNA etiqueta y se compara por igualdad, como en
`impact._position_ok`: "Ala-pívot" no cuenta como "Pívot"). Entonces:

1. Dentro de un grupo, fijado cuántos minutos `T_g` recibe, lo óptimo es
   dárselos a sus jugadores de mayor valor hasta su tope (intercambiar un
   minuto de uno peor por uno mejor del mismo grupo nunca empeora nada ni
   rompe ninguna restricción). Su valor `f_g(T_g)` es cóncavo y lineal a
   trozos, con pendientes = los valores de sus jugadores en orden
   decreciente.
2. Queda un problema separable y cóncavo: maximizar `Σ_g f_g(T_g)` con
   `T_g ≥ max(F_g, Σ mín)`, `T_g ≤ Σ topes` y `Σ_g T_g = 200` (los jugadores
   sin grupo forman otro sumando sin suelo). Para eso el algoritmo voraz de
   incrementos marginales es óptimo (condiciones KKT: en el óptimo hay un
   umbral `μ` tal que todo minuto asignado por encima del mínimo exigido vale
   ≥ μ y todo minuto libre vale ≤ μ).
3. `optimise_minutes` hace exactamente eso: arranca en los mínimos, cubre el
   déficit de cada suelo con los mejores del grupo (paso 1) y reparte el
   resto por valor decreciente entre todos (paso 2). Como el reparto dentro
   de cada grupo sigue siendo "los mejores primero" en los dos pasos, la
   solución final es la del argumento.

Con datos enteros la solución es entera: todas las fronteras del voraz son
topes, mínimos, suelos o el total. Los tests lo comparan con fuerza bruta.

**Opción prudente.** El RAPM es la media a posteriori del ridge; su
incertidumbre es mucho mayor para quien ha jugado poco. `risk_adjusted_rapm`
resta `κ` desviaciones típicas a posteriori (`τ·√(λ/(n+λ))`, con `n` minutos
en el ajuste): un jugador con pocos minutos solo gana minutos si su ventaja
esperada lo compensa. Con `κ = 0` es el RAPM puro.

**Topes por carga.** `load_caps` traduce el calendario en un tope por jugador
(reglas en `LoadRules`, todas parametrizables): el tope general, rebajado a lo
que le falta para cruzar el aviso de 140 minutos en 7 días de "Estado del
equipo" (el mismo umbral por defecto) y a un tope de descanso corto si llega
con ≤ 2 días tras un partido largo. Es una regla de calendario, no un dato
médico, y el entrenador la corrige en la tabla.

Lógica pura sobre `pandas`/`numpy`, sin Streamlit ni SQLAlchemy (regla del
paquete, ver `app/analytics/__init__.py`).
"""
import datetime as dt
import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics.impact import RIDGE_LAMBDA
except ImportError:  # pragma: no cover
    from analytics.impact import RIDGE_LAMBDA

#: Minutos de un partido sin prórroga: tope físico de cualquier jugador.
GAME_MINUTES = 40.0

#: Minutos de jugador que reparte un equipo: cinco en pista × 40.
TEAM_MINUTES = 5 * GAME_MINUTES

#: Suelos por posición por defecto: un base y un pívot en pista en todo
#: momento (ver la docstring del módulo: 40 minutos es el suelo exacto).
DEFAULT_POSITION_FLOORS: Dict[str, float] = {"Base": GAME_MINUTES, "Pívot": GAME_MINUTES}

#: Desviación típica a priori del impacto real de un jugador (τ, +/- por 40).
#: Es la misma que justifica `impact.RIDGE_LAMBDA` (λ = σ²/τ² ≈ 1200 con
#: τ ≈ 2,5): la desviación a posteriori de un RAPM con `n` minutos es
#: τ·√(λ/(n+λ)).
RAPM_PRIOR_SD = 2.5

#: Cuántas desviaciones típicas resta la opción prudente. 0,5 penaliza a quien
#: tiene 300 minutos en el ajuste ~0,4 puntos por 40 más que a quien tiene
#: 3000: suficiente para desempatar a favor de lo conocido, no para enterrar
#: a un suplente bueno.
RISK_KAPPA = 0.5

#: Partidos del equipo que cuentan para la "media reciente" de minutos.
RECENT_GAMES = 5

#: Mismo criterio que `components/impact.BUILDER_DEFAULT_MIN_MINUTES`: por
#: debajo, jugador de fondo de armario o alta reciente — entra en la tabla
#: pero no disponible por defecto.
DEFAULT_MIN_TEAM_MINUTES = 100.0

_EPS = 1e-6

# Etiquetas de "qué lo limita", compartidas por pantalla, asistente y tests.
BIND_UNAVAILABLE = "no disponible"
BIND_CAP_LOAD = "tope por carga"
BIND_CAP_SHORT_REST = "tope por descanso corto"
BIND_CAP_COACH = "tope del entrenador"
BIND_CAP_GENERAL = "tope general"
BIND_MIN = "mínimo fijado"
BIND_FLOOR = "cobertura de posición"
BIND_MARGINAL = "completa los 200 min"
BIND_OUT = "fuera de la rotación"

#: `cap_source` → etiqueta de "qué lo limita" cuando el jugador llega al tope.
CAP_LABELS = {
    "carga": BIND_CAP_LOAD,
    "descanso corto": BIND_CAP_SHORT_REST,
    "entrenador": BIND_CAP_COACH,
    "general": BIND_CAP_GENERAL,
}


@dataclass(frozen=True)
class LoadRules:
    """Reglas que convierten la carga reciente en un tope de minutos.

    Attributes:
        default_max: tope de cualquier jugador sin carga especial. Por debajo
            de 40 a propósito: el modelo aditivo, sin tope, le daría 40
            minutos a los cinco mejores, y ningún entrenador juega así.
        alert_minutes: aviso de minutos en `window_days` días. 140 en 7 días
            es el aviso por defecto de "Carga acumulada" en "Estado del
            equipo": el tope es lo que le falta para cruzarlo HOY.
        window_days: ventana de calendario de la carga.
        min_load_cap: el tope por carga nunca baja de aquí. Dejarlo en 0 es
            decisión del entrenador (desmarcar "Disponible"), no de una regla.
        short_rest_days: descanso (días desde el último partido del equipo)
            a partir del cual se considera corto, inclusive.
        short_rest_last_game: minutos en el último partido que, con descanso
            corto, activan `short_rest_max`.
        short_rest_max: tope con descanso corto tras un partido largo.
    """

    default_max: float = 32.0
    alert_minutes: float = 140.0
    window_days: int = 7
    min_load_cap: float = 12.0
    short_rest_days: int = 2
    short_rest_last_game: float = 30.0
    short_rest_max: float = 28.0


# --------------------------------------------------------------- carga --


def last_game_date(game_log: pd.DataFrame, reference_date: dt.date) -> Optional[dt.date]:
    """Fecha del último partido del equipo ANTES de `reference_date`, o `None`."""
    if game_log.empty:
        return None
    dates = pd.to_datetime(game_log["game_date"])
    before = dates[dates < pd.Timestamp(reference_date)]
    return None if before.empty else before.max().date()


def rest_days_before(game_log: pd.DataFrame, reference_date: dt.date) -> Optional[int]:
    """Días de descanso con los que se llega a `reference_date` (mismo cómputo que `queries.rest_days`)."""
    last = last_game_date(game_log, reference_date)
    return None if last is None else (reference_date - last).days


def default_reference_date(
    last_played: Optional[dt.date],
    next_scheduled: Optional[dt.date],
    *,
    max_gap_days: int = 14,
    assumed_rest_days: int = 3,
) -> dt.date:
    """Fecha del partido que se planifica, si el entrenador no dice otra.

    El próximo partido del calendario si cae cerca del último jugado; si no
    (se mira una temporada cerrada, o no hay calendario cargado), un partido
    hipotético `assumed_rest_days` después del último — con la carga de
    verdad de esa semana, que es lo que se quiere ver.
    """
    if last_played is None:
        return next_scheduled or dt.date.today()
    if next_scheduled is not None and 0 < (next_scheduled - last_played).days <= max_gap_days:
        return next_scheduled
    return last_played + dt.timedelta(days=assumed_rest_days)


def recent_minutes_summary(
    game_log: pd.DataFrame,
    reference_date: dt.date,
    *,
    days: int = 7,
    last_n: int = RECENT_GAMES,
) -> pd.DataFrame:
    """Carga y minutos recientes de cada jugador ANTES del partido de `reference_date`.

    Args:
        game_log: una fila por (jugador, partido del equipo) con `player_id,
            game_date, minutes`; `minutes` NaN = no jugó (la salida de
            `queries.rolling_load`, que ya cruza cada jugador con todos los
            partidos del equipo).
        reference_date: fecha del partido que se planifica. Solo cuentan los
            partidos estrictamente anteriores.
        days: ventana de carga, semiabierta por la izquierda como
            `queries.rolling_load`: con 7, desde el mismo día de la semana
            anterior (excluido) hasta la víspera.
        last_n: partidos del EQUIPO que entran en la media reciente.

    Returns:
        `player_id, load_minutes, games_in_window, last_game_minutes,
        recent_avg_minutes, recent_games_played`. `last_game_minutes` es del
        último partido del equipo (0 si no jugó); `recent_avg_minutes` es la
        media en los partidos que SÍ jugó de los `last_n` últimos del equipo
        (NaN si no jugó ninguno: lesionado, o recién llegado).
    """
    columns = [
        "player_id", "load_minutes", "games_in_window", "last_game_minutes",
        "recent_avg_minutes", "recent_games_played",
    ]
    if game_log.empty:
        return pd.DataFrame(columns=columns)
    log = game_log[["player_id", "game_date", "minutes"]].copy()
    log["game_date"] = pd.to_datetime(log["game_date"])
    ref = pd.Timestamp(reference_date)
    log = log[log["game_date"] < ref]
    if log.empty:
        return pd.DataFrame(columns=columns)

    team_dates = np.sort(log["game_date"].unique())
    recent_dates = set(team_dates[-last_n:])
    last_date = team_dates[-1]
    window_start = ref - pd.Timedelta(days=days)

    minutes = log["minutes"].astype(float)
    played = minutes.fillna(0.0)
    in_window = log["game_date"] > window_start
    recent = log["game_date"].isin(recent_dates) & (minutes > 0)
    frame = pd.DataFrame({
        "player_id": log["player_id"],
        "load": played.where(in_window, 0.0),
        "in_window": in_window.astype(int),
        "last": played.where(log["game_date"] == last_date, 0.0),
        "recent_sum": minutes.where(recent, 0.0).fillna(0.0),
        "recent_n": recent.astype(int),
    })
    agg = frame.groupby("player_id", sort=True).sum()
    out = pd.DataFrame({
        "player_id": agg.index,
        "load_minutes": agg["load"].to_numpy(),
        "games_in_window": agg["in_window"].to_numpy(),
        "last_game_minutes": agg["last"].to_numpy(),
        "recent_avg_minutes": (agg["recent_sum"] / agg["recent_n"].replace(0, np.nan)).to_numpy(),
        "recent_games_played": agg["recent_n"].to_numpy(),
    })
    return out[columns].reset_index(drop=True)


def load_caps(
    summary: pd.DataFrame, *, rest_days: Optional[int], rules: LoadRules = LoadRules()
) -> pd.DataFrame:
    """Tope sugerido de cada jugador a partir de su carga (reglas en `LoadRules`).

    1. Tope general: `rules.default_max`.
    2. Carga: si con el tope general cruzaría el aviso (`load_minutes + tope
       > alert_minutes`), el tope baja a lo que le falta para cruzarlo, sin
       bajar de `min_load_cap`.
    3. Descanso corto: con `rest_days ≤ short_rest_days` y
       `last_game_minutes ≥ short_rest_last_game`, como mucho
       `short_rest_max`.

    Se queda el MENOR de los tres, redondeado hacia abajo a minutos enteros,
    y se dice cuál manda.

    Returns:
        `player_id, max_minutes, cap_source` ("general", "carga" o "descanso
        corto") y `cap_detail` (la frase que lo justifica, vacía para el
        general).
    """
    rows = []
    for rec in summary.itertuples(index=False):
        cap, source, detail = float(rules.default_max), "general", ""
        load = float(getattr(rec, "load_minutes", 0.0) or 0.0)
        remaining = rules.alert_minutes - load
        if remaining < cap:
            cap = max(float(rules.min_load_cap), remaining)
            source = "carga"
            detail = f"{load:.0f} min en {rules.window_days} días"
        last = float(getattr(rec, "last_game_minutes", 0.0) or 0.0)
        if (
            rest_days is not None
            and rest_days <= rules.short_rest_days
            and last >= rules.short_rest_last_game
            and rules.short_rest_max < cap
        ):
            cap = float(rules.short_rest_max)
            source = "descanso corto"
            detail = f"{rest_days} día{'s' if rest_days != 1 else ''} de descanso tras {last:.0f} min"
        cap = float(math.floor(min(cap, GAME_MINUTES) + _EPS))
        rows.append({"player_id": rec.player_id, "max_minutes": cap, "cap_source": source, "cap_detail": detail})
    return pd.DataFrame(rows, columns=["player_id", "max_minutes", "cap_source", "cap_detail"])


def relax_caps(
    players: pd.DataFrame,
    *,
    total: float = TEAM_MINUTES,
    position_floors: Optional[Mapping[str, float]] = None,
) -> tuple:
    """Sube lo justo los topes SUGERIDOS si con ellos no hay reparto posible.

    En una semana de tres partidos, las reglas de carga pueden dejar a la
    plantilla por debajo de 200 minutos, o a los bases por debajo de sus 40:
    el plan no existiría, y un error no ayuda a nadie el día de partido. Se
    suben solo los topes que NO ha fijado el entrenador (`cap_source !=
    "entrenador"`), de los disponibles, sin pasar de 40, en dos pasos:

    1. Por cada suelo de posición que no se alcanza, el mismo `δ_g` (minutos
       enteros, el menor posible) a los jugadores de esa posición.
    2. Si el total sigue sin llegar a `total`, el mismo `δ` a todos.

    Cada subida se anota en `cap_detail`. Si ni subiendo hasta 40 llega, ese
    paso no toca nada: el plan dirá que no es factible y por qué.

    Args:
        players: `available, max_minutes` y, opcionales, `position`,
            `cap_source`, `cap_detail` (como en `plan_minutes`).
        position_floors: los mismos suelos que se pasarán a `plan_minutes`.

    Returns:
        `(players con los topes ajustados, [avisos])`: una frase por subida
        hecha, vacía si no hacía falta ninguna.
    """
    df = players.copy().reset_index(drop=True)
    for column, default in (("cap_source", "general"), ("cap_detail", ""), ("position", None)):
        if column not in df.columns:
            df[column] = default
    available = df["available"].fillna(False).astype(bool).to_numpy()
    caps = np.clip(df["max_minutes"].astype(float).fillna(0.0).to_numpy(), 0.0, GAME_MINUTES)
    adjustable = available & (df["cap_source"].fillna("general") != "entrenador").to_numpy()
    details = df["cap_detail"].fillna("").astype(str).tolist()
    notes = []

    def raise_to(members: np.ndarray, target: float, label: str) -> None:
        def capacity(delta: float) -> float:
            raised = np.where(members & adjustable, np.minimum(caps + delta, GAME_MINUTES), caps)
            return float(raised[members].sum())

        if capacity(0.0) >= target - _EPS or capacity(GAME_MINUTES) < target - _EPS:
            return
        delta = next(d for d in range(1, int(GAME_MINUTES) + 1) if capacity(float(d)) >= target - _EPS)
        raised = np.minimum(caps + delta, GAME_MINUTES)
        changed = members & adjustable & (raised > caps + _EPS)
        note = f"+{delta} para {label}"
        for i in np.flatnonzero(changed):
            caps[i] = raised[i]
            details[i] = f"{details[i]}, {note}" if details[i] else note
        notes.append(f"{delta} min los topes sugeridos para {label}")

    groups = np.array([_norm(p) for p in df["position"]])
    for label, floor in (position_floors or {}).items():
        if floor and floor > 0:
            raise_to(available & (groups == _norm(label)), float(floor), f"cubrir {label}")
    raise_to(available, float(total), f"llegar a {total:.0f}")

    df["max_minutes"] = np.where(available, caps, df["max_minutes"].astype(float).fillna(0.0))
    df["cap_detail"] = details
    return df, notes


# ------------------------------------------------------------ valor --


def rapm_posterior_sd(fit_minutes, *, ridge: float = RIDGE_LAMBDA, prior_sd: float = RAPM_PRIOR_SD) -> np.ndarray:
    """Desviación típica a posteriori aproximada del RAPM: τ·√(λ/(n+λ)).

    Aproximación de diseño ortogonal (cada jugador como si su columna no se
    solapara con las demás): exacta en el límite, y sobre todo MONÓTONA en
    los minutos, que es lo único que necesita la opción prudente.
    """
    n = np.clip(np.asarray(fit_minutes, dtype=float), 0.0, None)
    return prior_sd * np.sqrt(ridge / (n + ridge))


def risk_adjusted_rapm(rapm, fit_minutes, *, kappa: float = RISK_KAPPA, ridge: float = RIDGE_LAMBDA) -> np.ndarray:
    """RAPM prudente: RAPM − κ·desviación a posteriori (cota inferior, no media)."""
    return np.asarray(rapm, dtype=float) - kappa * rapm_posterior_sd(fit_minutes, ridge=ridge)


def build_plan_input(
    fit_players: pd.DataFrame,
    team_minutes: pd.Series,
    roster: pd.DataFrame,
    summary: pd.DataFrame,
    caps: pd.DataFrame,
    names: Dict[str, str],
    *,
    default_max: float = LoadRules.default_max,
) -> pd.DataFrame:
    """Tabla de partida del planificador: plantilla activa + RAPM + carga + tope sugerido.

    Args:
        fit_players: `impact.fit_rapm(...)["players"]`.
        team_minutes: `impact.team_player_minutes(...)` del equipo.
        roster: plantilla activa (`queries.roster_cards`: `id, name,
            position`); vacía = se usan los jugadores de `team_minutes`.
        summary: `recent_minutes_summary(...)`.
        caps: `load_caps(...)`.
        names: `{player_id: nombre}` para cuando no hay `roster`.
        default_max: tope de quien no tenga fila en `caps`.

    Un jugador sin fila en el ajuste (nunca pisó un tramo válido) cuenta como
    RAPM 0 con 0 minutos: jugador medio, lo mismo que haría el constructor.
    Disponible por defecto quien ha jugado alguno de los `RECENT_GAMES`
    últimos partidos del equipo (el lesionado de larga duración no, aunque
    tenga muchos minutos en la temporada); si no hay partidos previos a la
    fecha, quien pasa de `DEFAULT_MIN_TEAM_MINUTES` con el equipo en los
    tramos.

    Returns:
        La tabla que espera `plan_minutes` (con `prudent`, el RAPM prudente,
        como segunda columna de valor), ordenada por disponibles y RAPM.
    """
    if not roster.empty:
        base = roster[["id", "name", "position"]].rename(columns={"id": "player_id", "name": "player_name"})
    else:
        base = pd.DataFrame({"player_id": team_minutes.index, "player_name": [names.get(p, p) for p in team_minutes.index]})
        base["position"] = None
    rapm = fit_players[["player_id", "rapm", "minutes", "reliable"]].rename(columns={"minutes": "fit_minutes"})
    df = base.merge(rapm, on="player_id", how="left")
    df["rapm"] = df["rapm"].fillna(0.0)
    df["fit_minutes"] = df["fit_minutes"].fillna(0.0)
    df["reliable"] = df["reliable"].fillna(False).astype(bool)
    df["team_minutes"] = df["player_id"].map(team_minutes).fillna(0.0)
    df = df.merge(summary, on="player_id", how="left").merge(caps, on="player_id", how="left")
    df["load_minutes"] = df["load_minutes"].fillna(0.0)
    df["max_minutes"] = df["max_minutes"].fillna(float(default_max))
    df["cap_source"] = df["cap_source"].fillna("general")
    df["cap_detail"] = df["cap_detail"].fillna("")
    played_recently = df["recent_games_played"].fillna(0) > 0
    if played_recently.any():
        df["available"] = played_recently
    else:
        df["available"] = df["team_minutes"] >= DEFAULT_MIN_TEAM_MINUTES
    df["min_minutes"] = 0.0
    df["prudent"] = risk_adjusted_rapm(df["rapm"], df["fit_minutes"])
    return df.sort_values(["available", "rapm"], ascending=[False, False]).reset_index(drop=True)


# ---------------------------------------------------------- optimizador --


class InfeasiblePlan(ValueError):
    """Las restricciones no dejan repartir los minutos (el mensaje dice cuál)."""


def _norm(label) -> str:
    """Etiqueta de posición comparable: sin mayúsculas ni espacios; vacía si no hay."""
    if label is None or (isinstance(label, float) and math.isnan(label)):
        return ""
    return str(label).strip().lower()


def optimise_minutes(
    values: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
    *,
    groups: Optional[Sequence[Optional[str]]] = None,
    floors: Optional[Mapping[str, float]] = None,
    total: float = TEAM_MINUTES,
    names: Optional[Sequence[str]] = None,
):
    """Reparto óptimo de `total` minutos (algoritmo y demostración en la docstring del módulo).

    Maximiza `Σ values_i · m_i` con `lower_i ≤ m_i ≤ upper_i`, `Σ m_i = total`
    y, para cada grupo `g` de `floors`, `Σ_{groups_i = g} m_i ≥ floors[g]`.
    Los grupos se comparan sin mayúsculas ni espacios; cada jugador está en
    un grupo como mucho, así que son disjuntos por construcción.

    Returns:
        `(minutes, floor_minutes)`: arrays con los minutos de cada jugador y
        la parte de ellos que se asignó para cubrir un suelo de posición.

    Raises:
        InfeasiblePlan: mínimos por encima de topes, topes que no llegan a
            `total`, un grupo que no llega a su suelo o suelos + mínimos por
            encima de `total`.
    """
    v = np.asarray(values, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    n = len(v)
    labels = names if names is not None else [str(i) for i in range(n)]
    grp = [_norm(g) for g in (groups if groups is not None else [None] * n)]

    bad = np.flatnonzero(lo > hi + _EPS)
    if bad.size:
        raise InfeasiblePlan(
            "Mínimo por encima del tope: " + ", ".join(f"{labels[i]} ({lo[i]:.0f} > {hi[i]:.0f})" for i in bad) + "."
        )
    if lo.sum() > total + _EPS:
        raise InfeasiblePlan(f"Los mínimos fijados suman {lo.sum():.0f} min, más de los {total:.0f} del partido.")
    if hi.sum() < total - _EPS:
        raise InfeasiblePlan(
            f"Con esos topes y disponibles solo salen {hi.sum():.0f} de los {total:.0f} minutos: "
            "sube algún tope o añade disponibles."
        )

    # Orden por valor decreciente; empate por posición en la entrada, para
    # que el resultado sea determinista.
    order = sorted(range(n), key=lambda i: (-v[i], i))
    m = lo.copy()
    floor_alloc = np.zeros(n)
    for label, floor in (floors or {}).items():
        key = _norm(label)
        members = [i for i in order if grp[i] == key]
        capacity = float(hi[members].sum()) if members else 0.0
        if capacity < floor - _EPS:
            raise InfeasiblePlan(
                f"Los disponibles de posición {label} solo suman {capacity:.0f} min con sus topes; "
                f"hacen falta {floor:.0f}."
            )
        deficit = floor - float(m[members].sum()) if members else floor
        for i in members:
            if deficit <= _EPS:
                break
            add = min(hi[i] - m[i], deficit)
            m[i] += add
            floor_alloc[i] += add
            deficit -= add

    remaining = total - float(m.sum())
    if remaining < -_EPS:
        raise InfeasiblePlan(
            f"Mínimos y cobertura de posición ya suman {m.sum():.0f} min, más de los {total:.0f} del partido."
        )
    for i in order:
        if remaining <= _EPS:
            break
        add = min(hi[i] - m[i], remaining)
        m[i] += add
        remaining -= add
    return m, floor_alloc


# ------------------------------------------------------------------ plan --


def projected_margin(minutes, rapm) -> float:
    """Margen proyectado del partido frente a un equipo medio: Σ rapm·m / 40."""
    return float(np.dot(np.asarray(minutes, dtype=float), np.asarray(rapm, dtype=float)) / GAME_MINUTES)


def _fmt_minutes(value) -> str:
    return "—" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:.0f}"


def plan_minutes(
    players: pd.DataFrame,
    *,
    value_column: str = "rapm",
    position_floors: Optional[Mapping[str, float]] = None,
    total: float = TEAM_MINUTES,
) -> dict:
    """Plan de minutos del próximo partido con explicación jugador a jugador.

    Args:
        players: una fila por jugador de la plantilla con `player_id,
            player_name, rapm, available, max_minutes` y, opcionales,
            `min_minutes` (0), `position`, `cap_source` ("general"),
            `cap_detail`, `recent_avg_minutes` y la columna de valor si no es
            `rapm` (p. ej. la prudente de `risk_adjusted_rapm`).
        value_column: qué se maximiza. El margen proyectado que se devuelve
            es SIEMPRE con el RAPM (la esperanza), también en la opción
            prudente: así las dos se comparan en la misma moneda.
        position_floors: `{posición: minutos}`; `None` o vacío = sin
            cobertura de posición (posiciones desconocidas).

    Returns:
        `{"feasible", "message", "table", "projected_margin", "objective"}`.
        `table`: `player_id, player_name, position, rapm, value, available,
        min_minutes, max_minutes, planned_minutes, recent_avg_minutes,
        delta, binding, detail, explanation`, ordenada por minutos del plan.
        `binding` es qué restricción lo deja donde está (constantes `BIND_*`)
        y `explanation` la frase para el entrenador: "Howard: 31 → 26 min
        (tope por carga: 152 min en 7 días)". Sin solución, `feasible` es
        falso, `message` dice por qué y los minutos van a NaN.
    """
    df = players.copy().reset_index(drop=True)
    for column, default in (
        ("min_minutes", 0.0), ("position", None), ("cap_source", "general"), ("cap_detail", ""),
        ("recent_avg_minutes", np.nan),
    ):
        if column not in df.columns:
            df[column] = default
    df["available"] = df["available"].fillna(False).astype(bool)
    df["value"] = df[value_column].astype(float).fillna(0.0)
    df["rapm"] = df["rapm"].astype(float).fillna(0.0)
    available = df["available"].to_numpy()
    lower = np.where(available, np.clip(df["min_minutes"].astype(float).fillna(0.0), 0.0, GAME_MINUTES), 0.0)
    upper = np.where(available, np.clip(df["max_minutes"].astype(float).fillna(0.0), 0.0, GAME_MINUTES), 0.0)
    df["min_minutes"], df["max_minutes"] = lower, upper

    floors = {k: float(v) for k, v in (position_floors or {}).items() if v and v > 0}
    floor_by_group = {_norm(k): v for k, v in floors.items()}
    groups = [p if a else None for p, a in zip(df["position"], available)]
    try:
        minutes, floor_alloc = optimise_minutes(
            df["value"], lower, upper, groups=groups, floors=floors, total=total,
            names=df["player_name"].tolist(),
        )
        feasible, message = True, None
    except InfeasiblePlan as exc:
        minutes, floor_alloc = np.full(len(df), np.nan), np.zeros(len(df))
        feasible, message = False, str(exc)

    df["planned_minutes"] = minutes
    df["delta"] = df["planned_minutes"] - df["recent_avg_minutes"].astype(float)

    bindings, details = [], []
    for i, row in df.iterrows():
        m = minutes[i]
        if not row["available"]:
            binding, detail = BIND_UNAVAILABLE, ""
        elif not feasible:
            binding, detail = "", ""
        elif m >= upper[i] - _EPS:
            binding = CAP_LABELS.get(row["cap_source"], BIND_CAP_GENERAL)
            detail = row["cap_detail"] or f"{upper[i]:.0f} min"
        elif floor_alloc[i] > _EPS and m <= lower[i] + floor_alloc[i] + _EPS:
            position = row["position"]
            binding = f"{BIND_FLOOR} ({position})"
            detail = f"hacen falta {floor_by_group.get(_norm(position), 0.0):.0f} min de {position}"
        elif lower[i] > _EPS and m <= lower[i] + _EPS:
            binding, detail = BIND_MIN, f"{lower[i]:.0f} min"
        elif m > _EPS:
            binding, detail = BIND_MARGINAL, "se lleva los minutos que quedan hasta 200"
        else:
            binding, detail = BIND_OUT, f"valor {row['value']:+.1f}, por debajo de los que juegan"
        bindings.append(binding)
        details.append(detail)
    df["binding"], df["detail"] = bindings, details

    def _explain(row) -> str:
        head = f"{row['player_name']}: {_fmt_minutes(row['recent_avg_minutes'])} → {_fmt_minutes(row['planned_minutes'])} min"
        if not row["binding"]:
            return head
        return f"{head} ({row['binding']}: {row['detail']})" if row["detail"] else f"{head} ({row['binding']})"

    df["explanation"] = df.apply(_explain, axis=1) if not df.empty else pd.Series(dtype=str)
    columns = [
        "player_id", "player_name", "position", "rapm", "value", "available", "min_minutes", "max_minutes",
        "planned_minutes", "recent_avg_minutes", "delta", "binding", "detail", "explanation",
    ]
    table = df[columns].sort_values(
        ["planned_minutes", "value"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True)
    return {
        "feasible": feasible,
        "message": message,
        "table": table,
        "projected_margin": projected_margin(minutes, df["rapm"]) if feasible else float("nan"),
        "objective": projected_margin(minutes, df["value"]) if feasible else float("nan"),
    }


def recent_distribution_margin(players: pd.DataFrame, *, total: float = TEAM_MINUTES) -> float:
    """Margen proyectado con el reparto RECIENTE de los disponibles, reescalado a `total` minutos.

    Referencia para el plan: "si repartes como vienes repartiendo (sin los
    que no están)". Reescalar es una aproximación —quitar a uno no reparte
    sus minutos en proporción—, y se presenta como tal. NaN si ningún
    disponible tiene minutos recientes.
    """
    df = players[players["available"].fillna(False).astype(bool)]
    recent = df["recent_avg_minutes"].astype(float).fillna(0.0).to_numpy()
    if recent.sum() <= _EPS:
        return float("nan")
    return projected_margin(recent * total / recent.sum(), df["rapm"].astype(float).fillna(0.0))
