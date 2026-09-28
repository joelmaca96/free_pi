"""Predicción del partido y "qué la mueve" (propuesta 16, A2 de la hoja de ruta).

`upcoming_matchups.predicted_net_rating` existe en el esquema y ninguna
ingesta lo rellena. Esta es la versión que se calcula AL VUELO en la interfaz
(la aplicación es de solo lectura, nunca escribe esa columna): margen esperado
y probabilidad de victoria del próximo partido, **con la descomposición** en
puntos — "+2,1 por jugar en casa, −1,5 porque llegamos con 2 días de descanso
y ellos con 4" — que es lo que convierte un porcentaje en algo que se puede
discutir en la reunión.

**Modelo** (`fit_ratings`): mínimos cuadrados con ridge sobre el margen final
de cada partido de la temporada (`home_score − away_score`, todas las
competiciones juntas por defecto: ACB y Euroliga se cruzan a través de los
clubes que juegan las dos, y eso ancla las dos escalas). Una fila por partido:

    margen = rating_local − rating_visitante + campo·[no neutral]
             + descanso·(días_local − días_visitante) + ruido

- **Rating ajustado por calendario (SRS)**: una columna por equipo (+1 local,
  −1 visitante). Descuenta la fuerza de los rivales enfrentados: ganar de 10
  a los tres primeros no vale lo mismo que ganar de 10 a los tres últimos.
  El ridge (`TEAM_RIDGE`, en "partidos equivalentes") encoge hacia 0 (= un
  equipo medio) al que ha jugado poco: con λ≈6, un equipo con 3 partidos
  conserva un tercio de lo que dirían sus resultados. Sale del mismo
  argumento bayesiano que el λ del RAPM (propuesta 12): σ del margen de un
  partido ≈ 11 puntos, σ real de la fuerza de un equipo ≈ 4,5 → λ = σ²/τ² ≈ 6.
- **Ventaja de campo**: columna sin penalizar, 1 en partidos con local de
  verdad y 0 en los de sede neutral (Copa del Rey, Supercopa: ver
  `NEUTRAL_COMPETITIONS`). Se estima de la liga, no se supone.
- **Descanso**: días desde el partido anterior del equipo EN CUALQUIER
  competición (mismo criterio que `queries.rest_days`, propuesta 04),
  recortados a `[1, REST_CAP_DAYS]` — cinco días de descanso no son mejores
  que cuatro, y el primer partido de la temporada cuenta como descansado. El
  efecto por día **se estima de la liga en la misma regresión** (no es una
  constante supuesta), con dos frenos porque con una temporada la señal es
  débil: ridge `REST_RIDGE` que lo encoge hacia 0 (≈1 punto/día de desviación
  a priori) y restricción de signo `[0, MAX_REST_EFFECT]` — descansar más no
  puede restar puntos, y un coeficiente negativo con una temporada sería
  ruido que la pantalla contaría como si fuera un hallazgo. Si hay menos de
  `MIN_REST_GAMES` partidos con diferencia de descanso, el efecto es 0 y
  la descomposición lo dice.
- **Recencia** (opcional, `half_life_days`): peso exponencial por antigüedad
  del partido. Desactivado por defecto; el backtest dice si compensa.

**Probabilidad** (`win_probability`): aproximación normal Φ(margen/σ), con σ
la desviación de los residuos del ajuste encogida hacia `PRIOR_SIGMA` con
`SIGMA_PRIOR_GAMES` partidos ficticios — con pocos partidos (o con datos
sintéticos que el modelo explica al punto) el ajuste daría σ≈0 y
probabilidades del 100%. No incluye la incertidumbre de los propios ratings:
es algo optimista lejos del 50%, y la pantalla lo avisa.

**Honestidad** (`backtest`): validación "sin mirar al futuro" — para cada
fecha, se ajusta con los partidos ANTERIORES y se predicen los de ese día.
Devuelve error medio absoluto y % de ganadores acertados, para que la
pantalla enseñe cuánto acierta este modelo de verdad y no solo su número.

Lógica pura sobre `pandas`/`numpy` (sin Streamlit ni SQLAlchemy, sin
`scipy`), mismo criterio que `win_thresholds.py`/`impact.py`. Los datos los
sirve `data/queries_prediction.py`.
"""
import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

#: Ridge de los ratings de equipo, en partidos equivalentes (ver docstring).
TEAM_RIDGE = 6.0
#: Ridge del efecto del descanso, en partidos equivalentes: a priori ≈1
#: punto por día de diferencia (σ²/τ² = 11²/1² ≈ 120).
REST_RIDGE = 120.0
#: Tope de días de descanso que cuentan: 1 (back-to-back) … 4 o más.
REST_CAP_DAYS = 4
#: Techo del efecto por día de descanso, en puntos (restricción de signo y de
#: tamaño: ver docstring del módulo).
MAX_REST_EFFECT = 1.5
#: Mínimo de partidos con diferencia de descanso distinta de 0 para estimar
#: el efecto; por debajo, 0.
MIN_REST_GAMES = 20
#: σ a priori del margen de un partido (baloncesto FIBA: 10-12 puntos) y
#: cuántos partidos ficticios pesa, para que σ no salga ~0 con poca muestra.
PRIOR_SIGMA = 11.0
SIGMA_PRIOR_GAMES = 20.0
#: Competiciones que se juegan en sede neutral (fase final a partido único):
#: sin ventaja de campo aunque la fila tenga un "local" formal.
NEUTRAL_COMPETITIONS = ("Copa del Rey", "Supercopa")
#: Partidos mínimos de entrenamiento antes de empezar a predecir en el backtest.
MIN_TRAIN_GAMES = 40


def is_neutral_competition(name: Optional[str]) -> bool:
    """¿Se juega en sede neutral? Por nombre de competición (`NEUTRAL_COMPETITIONS`)."""
    return bool(name) and any(n.lower() in str(name).lower() for n in NEUTRAL_COMPETITIONS)


def clip_rest(days) -> float:
    """Días de descanso recortados a `[1, REST_CAP_DAYS]`; sin dato (primer partido) = el tope."""
    if days is None or (isinstance(days, float) and math.isnan(days)) or pd.isna(days):
        return float(REST_CAP_DAYS)
    return float(min(max(float(days), 1.0), REST_CAP_DAYS))


def win_probability(margin: float, sigma: float) -> float:
    """P(margen real > 0) con margen ~ Normal(`margin`, `sigma`). Monótona creciente en `margin`."""
    sigma = max(float(sigma), 1e-6)
    return 0.5 * (1.0 + math.erf(float(margin) / (sigma * math.sqrt(2.0))))


# ================================================================ datos ==


def prepare_games(games: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la tabla de partidos y le añade margen, sede neutral y descanso de cada equipo.

    Args:
        games: una fila por partido con `game_id, game_date, home_team_id,
            away_team_id, home_score, away_score` y, opcionales,
            `competition` (nombre, para deducir `neutral`) o `neutral`.

    Returns:
        La misma tabla ordenada por fecha, con `game_date` como `datetime`,
        `margin` (local − visitante), `neutral` (bool), `home_rest`/
        `away_rest` (días crudos desde el partido anterior del equipo en esta
        misma tabla, `NaN` en su primero) y `rest_diff` (recortados, local −
        visitante). El descanso se calcula sobre TODAS las filas recibidas,
        de cualquier competición — el matiz de la propuesta 04.
    """
    if games.empty:
        return games.assign(margin=[], neutral=[], home_rest=[], away_rest=[], rest_diff=[])
    df = games.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    df["margin"] = df["home_score"].astype(float) - df["away_score"].astype(float)
    if "neutral" not in df.columns:
        if "competition" in df.columns:
            df["neutral"] = df["competition"].map(is_neutral_competition)
        else:
            df["neutral"] = False
    df["neutral"] = df["neutral"].fillna(False).astype(bool)

    # Calendario "largo": una fila por equipo y partido, para el LAG de fechas.
    long = pd.concat(
        [
            df[["game_id", "game_date"]].assign(team_id=df["home_team_id"], side="home"),
            df[["game_id", "game_date"]].assign(team_id=df["away_team_id"], side="away"),
        ],
        ignore_index=True,
    ).sort_values(["team_id", "game_date", "game_id"])
    long["rest"] = long.groupby("team_id")["game_date"].diff().dt.days
    rest = long.pivot_table(index="game_id", columns="side", values="rest", aggfunc="first")
    df["home_rest"] = df["game_id"].map(rest["home"]) if "home" in rest else np.nan
    df["away_rest"] = df["game_id"].map(rest["away"]) if "away" in rest else np.nan
    df["rest_diff"] = df["home_rest"].map(clip_rest) - df["away_rest"].map(clip_rest)
    return df


# ================================================================ ajuste ==


@dataclass
class RatingsFit:
    """Resultado de `fit_ratings`: todo en puntos de margen por partido."""

    ratings: pd.Series  # team_id -> rating ajustado (0 = equipo medio)
    games_by_team: pd.Series  # team_id -> partidos en el ajuste
    home_court: float
    rest_per_day: float
    rest_estimated: bool  # False si no había muestra y se dejó en 0
    sigma: float
    n_games: int
    last_date: Optional[dt.date] = None
    competitions: List[str] = field(default_factory=list)

    def rating(self, team_id: str) -> float:
        return float(self.ratings.get(team_id, 0.0))

    def games(self, team_id: str) -> int:
        return int(self.games_by_team.get(team_id, 0))


def fit_ratings(
    games: pd.DataFrame,
    *,
    team_ridge: float = TEAM_RIDGE,
    rest_ridge: float = REST_RIDGE,
    half_life_days: Optional[float] = None,
    as_of: Optional[dt.date] = None,
) -> Optional[RatingsFit]:
    """Ajusta ratings de equipo, ventaja de campo y efecto del descanso (ver docstring del módulo).

    Args:
        games: salida de `prepare_games` (o la tabla cruda: se prepara aquí
            si le faltan las columnas).
        half_life_days: vida media del peso por recencia; `None` = todos
            los partidos pesan igual.
        as_of: fecha de referencia para la recencia (por defecto, el último
            partido de `games`).

    Returns:
        `RatingsFit`, o `None` si no hay ningún partido.
    """
    if games is None or games.empty:
        return None
    if "rest_diff" not in games.columns:
        games = prepare_games(games)

    teams = sorted(set(games["home_team_id"]) | set(games["away_team_id"]))
    index = {team: i for i, team in enumerate(teams)}
    n, n_teams = len(games), len(teams)
    home_col, rest_col = n_teams, n_teams + 1

    X = np.zeros((n, n_teams + 2))
    rows = np.arange(n)
    X[rows, games["home_team_id"].map(index).to_numpy()] = 1.0
    X[rows, games["away_team_id"].map(index).to_numpy()] = -1.0
    X[:, home_col] = (~games["neutral"].to_numpy()).astype(float)
    rest_diff = games["rest_diff"].to_numpy(dtype=float)
    rest_games = int((np.abs(rest_diff) > 0).sum())
    rest_estimated = rest_games >= MIN_REST_GAMES
    X[:, rest_col] = rest_diff if rest_estimated else 0.0
    y = games["margin"].to_numpy(dtype=float)

    dates = pd.to_datetime(games["game_date"])
    if half_life_days:
        reference = pd.Timestamp(as_of) if as_of is not None else dates.max()
        age = (reference - dates).dt.days.clip(lower=0).to_numpy(dtype=float)
        w = np.power(0.5, age / float(half_life_days))
        w = w / w.mean()  # λ sigue en "partidos equivalentes"
    else:
        w = np.ones(n)

    penalty = np.full(n_teams + 2, float(team_ridge))
    penalty[home_col] = 1e-6  # sin penalizar (solo estabilidad numérica)
    penalty[rest_col] = float(rest_ridge) if rest_estimated else 1e6
    XtW = X.T * w
    A = XtW @ X + np.diag(penalty)
    beta = np.linalg.solve(A, XtW @ y)

    # Restricción de signo/tamaño del descanso: si el libre se sale de
    # [0, MAX_REST_EFFECT], se fija en el borde y se reajusta el resto.
    rest_value = float(beta[rest_col])
    if rest_estimated and not (0.0 <= rest_value <= MAX_REST_EFFECT):
        rest_value = min(max(rest_value, 0.0), MAX_REST_EFFECT)
        keep = [i for i in range(n_teams + 2) if i != rest_col]
        y_adj = y - rest_value * X[:, rest_col]
        Xk = X[:, keep]
        XtWk = Xk.T * w
        beta_k = np.linalg.solve(XtWk @ Xk + np.diag(penalty[keep]), XtWk @ y_adj)
        beta = np.zeros(n_teams + 2)
        beta[keep] = beta_k
        beta[rest_col] = rest_value
    elif not rest_estimated:
        beta[rest_col] = 0.0

    # σ de los residuos, con los grados de libertad efectivos del ridge y
    # encogida hacia PRIOR_SIGMA (ver docstring del módulo).
    residuals = y - X @ beta
    rss = float((w * residuals**2).sum())
    try:
        dof_model = float(np.trace(np.linalg.solve(A, XtW @ X)))
    except np.linalg.LinAlgError:  # pragma: no cover - A es definida positiva
        dof_model = float(n_teams + 2)
    dof = max(float(w.sum()) - dof_model, 0.0)
    sigma = math.sqrt((rss + SIGMA_PRIOR_GAMES * PRIOR_SIGMA**2) / (dof + SIGMA_PRIOR_GAMES))

    ratings = pd.Series(beta[:n_teams], index=teams)
    ratings = ratings - ratings.mean()  # 0 = equipo medio de la tabla
    counts = pd.concat([games["home_team_id"], games["away_team_id"]]).value_counts()
    competitions = sorted(games["competition"].dropna().unique().tolist()) if "competition" in games else []
    return RatingsFit(
        ratings=ratings.sort_values(ascending=False),
        games_by_team=counts,
        home_court=float(beta[home_col]),
        rest_per_day=float(beta[rest_col]),
        rest_estimated=rest_estimated,
        sigma=sigma,
        n_games=n,
        last_date=dates.max().date(),
        competitions=competitions,
    )


# ============================================================ predicción ==


@dataclass
class Prediction:
    """Predicción de un partido desde el punto de vista del equipo propio."""

    expected_margin: float
    win_probability: float
    sigma: float
    components: List[Dict]  # [{key, label, points, detail}], suman expected_margin
    own_rating: float
    rival_rating: float
    own_games: int
    rival_games: int
    own_rest: Optional[int]
    rival_rest: Optional[int]
    venue: str  # 'home' | 'away' | 'neutral'


def predict_matchup(
    fit: RatingsFit,
    own_team_id: str,
    rival_team_id: str,
    *,
    venue: str,
    own_rest: Optional[int] = None,
    rival_rest: Optional[int] = None,
) -> Prediction:
    """Margen esperado, probabilidad y descomposición para el equipo propio contra un rival.

    Args:
        venue: `'home'` (jugamos en casa), `'away'` o `'neutral'`.
        own_rest/rival_rest: días desde el último partido de cada uno (en
            cualquier competición) hasta el del partido; `None` = sin partido
            previo reciente, cuenta como descansado.

    Returns:
        `Prediction`. `components` siempre trae las tres piezas en el mismo
        orden (rating, campo, descanso), aunque alguna valga 0, y su suma es
        exactamente `expected_margin`.
    """
    if venue not in ("home", "away", "neutral"):
        raise ValueError(f"venue desconocido: {venue!r}")
    own_rating, rival_rating = fit.rating(own_team_id), fit.rating(rival_team_id)
    home_sign = {"home": 1.0, "away": -1.0, "neutral": 0.0}[venue]
    own_rest_c, rival_rest_c = clip_rest(own_rest), clip_rest(rival_rest)

    components = [
        {
            "key": "rating",
            "label": "Nivel ajustado por calendario",
            "points": own_rating - rival_rating,
            "detail": f"{fmt_points(own_rating)} nosotros, {fmt_points(rival_rating)} ellos",
        },
        {
            "key": "home",
            "label": "Pista",
            "points": home_sign * fit.home_court,
            "detail": {"home": "en casa", "away": "fuera", "neutral": "pista neutral"}[venue],
        },
        {
            "key": "rest",
            "label": "Descanso",
            "points": fit.rest_per_day * (own_rest_c - rival_rest_c),
            "detail": f"{_rest_text(own_rest)} nosotros, {_rest_text(rival_rest)} ellos",
        },
    ]
    margin = float(sum(c["points"] for c in components))
    return Prediction(
        expected_margin=margin,
        win_probability=win_probability(margin, fit.sigma),
        sigma=fit.sigma,
        components=components,
        own_rating=own_rating,
        rival_rating=rival_rating,
        own_games=fit.games(own_team_id),
        rival_games=fit.games(rival_team_id),
        own_rest=None if own_rest is None else int(own_rest),
        rival_rest=None if rival_rest is None else int(rival_rest),
        venue=venue,
    )


def _rest_text(days: Optional[int]) -> str:
    if days is None:
        return "sin partido reciente"
    return f"{int(days)} día{'s' if int(days) != 1 else ''}"


def fmt_points(value: float) -> str:
    """`+2,1` / `−1,5` (coma decimal y signo menos tipográfico), como en la interfaz."""
    text = f"{abs(value):.1f}".replace(".", ",")
    return ("+" if value >= 0 else "−") + text


def explain_components(pred: Prediction, rival_name: str, *, min_points: float = 0.05) -> List[str]:
    """Frases de la descomposición, listas para la pantalla, el dossier y el asistente.

    Omite las piezas que valen menos de `min_points` (salvo el nivel, que
    siempre se dice): "+0,0 por el descanso" no aporta nada.
    """
    by_key = {c["key"]: c for c in pred.components}
    lines = []
    rating = by_key["rating"]["points"]
    if abs(rating) < 1.0:
        verdict = "nivel parecido"
    elif rating > 0:
        verdict = "somos mejores en lo que va de temporada"
    else:
        verdict = f"{rival_name} es mejor en lo que va de temporada"
    lines.append(f"{fmt_points(rating)} por nivel ajustado por calendario ({verdict})")
    home = by_key["home"]["points"]
    if abs(home) >= min_points:
        lines.append(f"{fmt_points(home)} por jugar {'en casa' if pred.venue == 'home' else 'fuera'}")
    rest = by_key["rest"]["points"]
    if abs(rest) >= min_points:
        lines.append(
            f"{fmt_points(rest)} porque llegamos con {_rest_text(pred.own_rest)} de descanso "
            f"y ellos con {_rest_text(pred.rival_rest)}"
        )
    return lines


def summary_sentence(pred: Prediction, rival_name: str, own_name: str = "Baskonia") -> str:
    """Una frase: margen, probabilidad y lo que la mueve (portada del dossier, asistente)."""
    who = own_name if pred.expected_margin >= 0 else rival_name
    points = f"{abs(pred.expected_margin):.1f}".replace(".", ",")
    head = (
        f"Predicción del modelo: {who} por {points} puntos "
        f"({pred.win_probability * 100:.0f}% de victoria del {own_name})"
    )
    return head + ": " + "; ".join(explain_components(pred, rival_name)) + "."


# ============================================================== backtest ==


def backtest(
    games: pd.DataFrame,
    *,
    min_train_games: int = MIN_TRAIN_GAMES,
    **fit_kwargs,
) -> Optional[Dict]:
    """Validación sin mirar al futuro: ajustar con lo anterior a cada fecha, predecir esa fecha.

    Args:
        games: salida de `prepare_games` (o cruda).
        min_train_games: no se predice nada hasta tener al menos estos
            partidos para ajustar (al principio de temporada el modelo solo
            sabría la ventaja de campo).
        fit_kwargs: los mismos de `fit_ratings` (ridge, recencia...).

    Returns:
        `{"n": partidos predichos, "mae": error medio absoluto en puntos,
        "hit_rate": % de ganadores acertados (0-1), "home_hit_rate": % que
        acertaría "siempre gana el local" en esos mismos partidos (la
        referencia tonta), "brier": error cuadrático medio de la
        probabilidad}`, o `None` si no hay partidos suficientes para
        predecir ninguno.
    """
    if games is None or games.empty:
        return None
    if "rest_diff" not in games.columns:
        games = prepare_games(games)
    dates = games["game_date"].dt.normalize()
    errors, hits, home_hits, briers = [], [], [], []
    for day in sorted(dates.unique()):
        train = games[dates < day]
        if len(train) < min_train_games:
            continue
        fit = fit_ratings(train, **fit_kwargs)
        test = games[dates == day]
        for row in test.itertuples(index=False):
            pred = (
                fit.rating(row.home_team_id) - fit.rating(row.away_team_id)
                + (0.0 if row.neutral else fit.home_court)
                + fit.rest_per_day * row.rest_diff
            )
            actual = float(row.margin)
            if actual == 0:  # imposible en baloncesto; por robustez
                continue
            errors.append(abs(pred - actual))
            hits.append((pred > 0) == (actual > 0))
            home_hits.append(actual > 0)
            p = win_probability(pred, fit.sigma)
            briers.append((p - (1.0 if actual > 0 else 0.0)) ** 2)
    if not errors:
        return None
    return {
        "n": len(errors),
        "mae": float(np.mean(errors)),
        "hit_rate": float(np.mean(hits)),
        "home_hit_rate": float(np.mean(home_hits)),
        "brier": float(np.mean(briers)),
    }


def ratings_table(fit: RatingsFit, team_names: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """Tabla `team_id, team, rating, games` ordenada de mejor a peor (para el desplegable de la liga)."""
    names = team_names or {}
    df = pd.DataFrame({"team_id": fit.ratings.index, "rating": fit.ratings.to_numpy()})
    df["team"] = df["team_id"].map(lambda t: names.get(t, t))
    df["games"] = df["team_id"].map(fit.games)
    return df[["team_id", "team", "rating", "games"]].reset_index(drop=True)


def split_by_competition(games: pd.DataFrame, competitions: Sequence[str]) -> pd.DataFrame:
    """Solo los partidos de esas competiciones (por nombre), para un ajuste acotado."""
    return games[games["competition"].isin(list(competitions))].reset_index(drop=True)
