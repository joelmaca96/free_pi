"""Señales semanales: qué ha cambiado de verdad, no ruido con forma de cambio.

Propuesta 10 (`doc/features/propuestas/10_senales_semanales.md`). La pregunta
que contesta este módulo es "de los últimos K partidos (K=5 por defecto)
frente al resto de la temporada, ¿qué diferencia es grande Y no es azar?" —
para jugador, equipo y rotación (carga es la excepción, ver más abajo: es un
umbral de calendario, no un contraste estadístico).

**El problema serio es de comparaciones múltiples** (§4 del documento): con
~15 jugadores × ~10 métricas se hacen ~150 contrastes por semana, y a
p < 0,05 eso son 7-8 falsas alarmas aunque no haya pasado nada. Dos medidas,
las dos obligatorias, aplicadas siempre juntas en `select_top_signals`:

1. **Corrección por número de contrastes** (Benjamini-Hochberg, `benjamini_
   hochberg`) sobre TODOS los p-valores de la semana a la vez, no metric a
   metric.
2. **Tamaño de efecto mínimo** en unidades de baloncesto (`Signal.min_effect`,
   fijado por quien construye cada candidato — ver `_PLAYER_RATE_METRICS` y
   compañía) — un cambio detectable pero minúsculo no le sirve a nadie.

**Sin scipy** (el documento pide "sin dependencias nuevas: numpy y pandas
bastan"): los contrastes de dos proporciones y de dos medias se resuelven con
la aproximación normal de toda la vida (`math.erf`), no con la t exacta. Con
K=5 el test ya es débil por diseño (§4) — la aproximación no cambia esa
conclusión, solo evita una dependencia nueva para un caso en el que la
diferencia entre normal y t con >=3 grados de libertad no mueve la decisión
de qué pasa el filtro.

Módulo de lógica pura sobre `pandas`/`numpy`, sin Streamlit ni SQLAlchemy ni
LLM — mismo criterio que `zone_matchup.py`/`shot_quality.py`: se prueba sin
base de datos. La redacción con LLM (opcional, dos capas como en
`app/reports/postgame_ppt.py`) vive en `polish_headlines`, al final del
fichero, y es la única función de aquí que sabe que existe un LLM — el resto
no lo necesita ni lo importa.
"""
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from itertools import chain
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# Mismo patrón de import doble que `zone_matchup.py` (pytest vs. Streamlit,
# ver `assistant/tools/context.py`): solo se reutiliza `ZONE_MERGES`, para que
# "Línea de fondo" se funda con "Pintura" en el reparto semanal exactamente
# igual que en `shot_quality`/`zone_matchup` — sin esto esa zona residual
# entraría y saldría del radar por volumen mínimo de partido a partido, no
# por juego real.
try:  # pragma: no cover - depende de cómo se arranque el proceso, no de la lógica
    from app.analytics.shot_quality import ZONE_MERGES
except ImportError:  # pragma: no cover
    from analytics.shot_quality import ZONE_MERGES

# ============================================================ estadística ==


def _normal_two_sided_p(z: float) -> float:
    """p-valor de dos colas de un estadístico z, vía la función de error de `math.erf`.

    `math.erf` es de la librería estándar (no numpy/scipy) y basta para la
    CDF de la normal: `Phi(z) = 0.5 * (1 + erf(z / sqrt(2)))`.
    """
    if z is None or math.isnan(z):
        return 1.0
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))


def two_proportion_test(makes_a: float, attempts_a: float, makes_b: float, attempts_b: float) -> "tuple[float, float]":
    """z-test de dos proporciones. `a` es lo reciente, `b` la línea base.

    Returns:
        `(diff_pp, p_value)` — `diff_pp = 100 * (p_a - p_b)`, con signo (sube
        o baja). `(0.0, 1.0)` si a alguno de los dos lados le faltan intentos:
        sin denominador no hay proporción que contrastar.
    """
    if not attempts_a or not attempts_b:
        return 0.0, 1.0
    p_a, p_b = makes_a / attempts_a, makes_b / attempts_b
    p_pool = (makes_a + makes_b) / (attempts_a + attempts_b)
    variance = p_pool * (1.0 - p_pool) * (1.0 / attempts_a + 1.0 / attempts_b)
    diff = p_a - p_b
    if variance <= 0:
        return 100.0 * diff, 1.0 if diff == 0 else 0.0
    z = diff / math.sqrt(variance)
    return 100.0 * diff, _normal_two_sided_p(z)


def two_mean_test(recent: Sequence[float], baseline: Sequence[float]) -> "tuple[float, float, float, float]":
    """Contraste de medias por partido, recientes contra línea base (Welch, aproximado por la normal).

    Args:
        recent: valores por partido de la ventana reciente (K partidos).
        baseline: valores por partido del resto de la temporada.

    Returns:
        `(mean_recent, mean_baseline, diff, p_value)`. `diff = mean_recent -
        mean_baseline`, con signo. Con menos de 2 valores utilizables a
        cualquier lado no hay varianza que estimar: se devuelven las medias
        que se puedan calcular y `p_value = 1.0` (nunca pasa el filtro de
        significación, que es lo correcto sin muestra).
    """
    recent_arr = np.asarray([v for v in recent if v is not None and not math.isnan(v)], dtype=float)
    baseline_arr = np.asarray([v for v in baseline if v is not None and not math.isnan(v)], dtype=float)
    mean_r = float(recent_arr.mean()) if len(recent_arr) else float("nan")
    mean_b = float(baseline_arr.mean()) if len(baseline_arr) else float("nan")
    if len(recent_arr) < 2 or len(baseline_arr) < 2 or math.isnan(mean_r) or math.isnan(mean_b):
        diff = (mean_r - mean_b) if not (math.isnan(mean_r) or math.isnan(mean_b)) else 0.0
        return mean_r, mean_b, diff, 1.0

    var_r, var_b = float(recent_arr.var(ddof=1)), float(baseline_arr.var(ddof=1))
    diff = mean_r - mean_b
    se = math.sqrt(var_r / len(recent_arr) + var_b / len(baseline_arr))
    if se <= 0:
        return mean_r, mean_b, diff, 1.0 if diff == 0 else 0.0
    return mean_r, mean_b, diff, _normal_two_sided_p(diff / se)


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.10) -> List[bool]:
    """Máscara de significación FDR de Benjamini-Hochberg (§4 del documento).

    Controla la proporción ESPERADA de falsas alarmas entre las que se
    enseñan, no la probabilidad de cada contraste por separado — es lo que
    hace falta cuando se hacen ~150 a la vez cada semana. `q=0.10` (no el
    0,05 de libro): el filtro de tamaño de efecto mínimo es la segunda
    barrera obligatoria (§4), así que un `q` algo más permisivo aquí no deja
    pasar ruido puro, solo evita ser doblemente estricto.

    Args:
        p_values: un p-valor por contraste, en cualquier orden.
        q: tasa de falso descubrimiento admitida.

    Returns:
        Una máscara booleana en el MISMO orden que `p_values`: `True` = pasa
        la corrección. Lista vacía si `p_values` lo está.
    """
    n = len(p_values)
    if n == 0:
        return []
    values = np.asarray(p_values, dtype=float)
    order = np.argsort(values)
    sorted_p = values[order]
    thresholds = (np.arange(1, n + 1) / n) * q
    passing = sorted_p <= thresholds
    result = np.zeros(n, dtype=bool)
    if passing.any():
        # El mayor rango i que cumple p_(i) <= (i/n)*q: TODOS los de rango
        # menor pasan también (es la definición del procedimiento BH), así
        # que no basta con marcar los que cumplen fila a fila.
        max_pass_rank = int(np.max(np.nonzero(passing)[0]))
        result[order[: max_pass_rank + 1]] = True
    return result.tolist()


# =================================================================== señal ==


@dataclass
class Signal:
    """Un candidato a señal: el número, su contraste (si lo tiene) y cómo se enseña.

    `p_value=None` marca una señal DETERMINISTA (hoy solo `load`: umbral de
    calendario, no hipótesis que contrastar) — `select_top_signals` la deja
    fuera de la corrección de Benjamini-Hochberg pero le sigue exigiendo
    pasar `min_effect` antes de entrar en el candidato (la propia condición
    de creación ya lo hace, ver `detect_load_signals`).
    """

    family: str  # 'player' | 'team' | 'rotation' | 'load'
    subject_id: str
    subject_name: str
    metric: str
    recent_value: float
    baseline_value: float
    effect: float
    unit: str
    n_recent: int
    n_baseline: int
    min_effect: float
    weight: float
    p_value: Optional[float]
    headline_template: str
    context: Dict[str, object] = field(default_factory=dict)
    ask_question: str = ""
    extra_note: Optional[str] = None
    headline: str = ""
    confidence: str = ""

    def passes_effect(self) -> bool:
        return abs(self.effect) >= self.min_effect

    @property
    def relevance(self) -> float:
        """Efecto × peso (§4: "por relevancia práctica", no por significación).

        No es una fórmula científica ni comparable bit a bit entre familias
        de señal (un punto porcentual de eFG% y un minuto de rol no son la
        misma unidad) — es, igual que el criterio de `postgame_ppt.
        _rule_based_highlights`, una ordenación razonable para quedarse con
        las que de verdad importan en vez de listarlas todas.
        """
        return abs(self.effect) * max(self.weight, 1e-9)


def _confidence_text(signal: "Signal") -> str:
    """Lenguaje llano, nunca un p-valor en pantalla (§2 del documento)."""
    if signal.p_value is None:
        return "Umbral de carga por calendario, no un contraste estadístico — mira el detalle antes de decidir."
    if signal.p_value < 0.01:
        return f"Cambio muy sostenido en {signal.n_recent} partidos: pasa el filtro estadístico con margen."
    return f"Sostenido en {signal.n_recent} partidos: pasa el filtro estadístico, aunque la muestra sigue siendo corta."


def select_top_signals(candidates: Sequence[Signal], *, max_signals: int = 5, q: float = 0.10) -> List[Signal]:
    """Los `max_signals` candidatos que sobreviven a las dos barreras obligatorias, por relevancia.

    Aplica Benjamini-Hochberg a TODOS los candidatos estadísticos de golpe
    (`p_value is not None`) y, aparte, el tamaño de efecto mínimo de cada uno
    (`Signal.passes_effect`) — las señales deterministas (`load`) solo pasan
    por el segundo filtro, que ya se aplicó al construirlas. Rellena
    `headline`/`confidence` SOLO en los supervivientes (rellenarlo antes
    sería trabajo tirado para los descartados).

    Devuelve **lista vacía** si nada sobrevive — es la respuesta correcta
    ("sin cambios significativos esta semana", §2) y no un error.

    El reparto final es POR RONDAS entre familias, no el top-N crudo de
    `relevance`. Motivo, verificado contra `data/baskonia.db`: `relevance`
    es `|efecto| × peso` y esas unidades NO son comparables entre familias
    (lo dice la propia docstring de `Signal.relevance`). Una señal de
    rotación mide el efecto en puntos porcentuales de reparto de minutos
    (~25) y lo pondera por minutos de pareja (~60): sale un orden de
    magnitud por encima de una de jugador (4 minutos × 25) o de equipo (4
    puntos de eFG% × 32), así que el top-5 crudo salía SIEMPRE con cinco
    tarjetas de rotación —tres de ellas del mismo jugador— y ni una de
    tiro, pérdidas o carga. Por rondas se coge primero la mejor de cada
    familia, luego la segunda de cada una, etc.: dentro de una familia se
    respeta la relevancia, y una familia solo llena huecos ajenos cuando
    las demás ya no tienen candidatos.
    """
    statistical = [c for c in candidates if c.p_value is not None]
    deterministic = [c for c in candidates if c.p_value is None]

    survivors: List[Signal] = [c for c in deterministic if c.passes_effect()]
    if statistical:
        significant = benjamini_hochberg([c.p_value for c in statistical], q=q)
        survivors.extend(c for c, sig in zip(statistical, significant) if sig and c.passes_effect())

    for signal in survivors:
        signal.headline = signal.headline_template.format(**signal.context)
        signal.confidence = _confidence_text(signal)

    survivors.sort(key=lambda c: c.relevance, reverse=True)

    by_family: "OrderedDict[str, List[Signal]]" = OrderedDict()
    for signal in survivors:  # ya ordenados: cada lista queda por relevancia
        by_family.setdefault(signal.family, []).append(signal)

    selected: List[Signal] = []
    while by_family and len(selected) < max_signals:
        for family in list(by_family):
            selected.append(by_family[family].pop(0))
            if not by_family[family]:
                del by_family[family]
            if len(selected) == max_signals:
                break
    return selected


# ============================================================ especificación ==

#: Partidos mínimos en el resto de temporada para que el contraste tenga algo
#: con lo que comparar — por debajo, ni se calcula la métrica (§4: "con K=5 el
#: test es débil por definición", y con menos de 3 partidos de referencia ya
#: no es débil, es inexistente).
MIN_BASELINE_GAMES = 3

#: Aviso de carga por defecto: mismo umbral que `app/screens/estado_equipo.py`
#: usa como valor inicial del control "Aviso" en la ventana de 7 días — no es
#: casualidad, es el mismo criterio en dos sitios, para que la señal semanal
#: no contradiga lo que ya se ve en esa pantalla.
LOAD_ALERT_MINUTES_7D = 140.0

#: Peso fijo de una señal de EQUIPO en el ranking de relevancia (§4: "efecto ×
#: minutos del jugador" para las de jugador). Un cambio de equipo afecta a los
#: cuarenta minutos de partido enteros, así que se pondera como un titular con
#: minutos altos — redondo a propósito, igual criterio que `_HIGH_PERCENTILE`
#: en `assistant/tools/team.py`.
TEAM_SIGNAL_WEIGHT = 32.0


@dataclass(frozen=True)
class _PlayerRateMetric:
    key: str
    label: str
    column: str
    per40: bool
    min_effect: float
    unit: str


#: Métricas de jugador en TASA (§2: "minutos, tiro (con volumen)... pérdidas,
#: faltas, rebote ofensivo"). El tiro va aparte, en `_PLAYER_PROPORTION_
#: METRICS`, porque necesita intentos (proporción), no solo un valor por
#: partido. Umbrales de efecto redondos y en unidades de baloncesto (§4): "4
#: minutos de cambio de rol" es literalmente el ejemplo del documento.
#: Los `label` son SIEMPRE singulares (nunca "los minutos"/"las pérdidas") a
#: propósito: el titular usa "ha subido/bajado" con un solo auxiliar fijo
#: (ver `headline_template` en `detect_player_signals`), y mezclar plural y
#: singular ahí produciría "los minutos ha subido" — mal español.
_PLAYER_RATE_METRICS = [
    _PlayerRateMetric("minutes", "el tiempo en pista", "minutes", False, 4.0, "min/partido"),
    _PlayerRateMetric("tov_per40", "el ritmo de pérdidas", "tov", True, 1.5, "por 40'"),
    _PlayerRateMetric("pf_per40", "el ritmo de faltas cometidas", "pf", True, 1.5, "por 40'"),
    _PlayerRateMetric("oreb_per40", "el rebote ofensivo", "oreb", True, 1.2, "por 40'"),
]


@dataclass(frozen=True)
class _PlayerProportionMetric:
    key: str
    label: str
    makes_column: str
    attempts_column: str
    min_effect_pp: float
    min_attempts_total: int


#: "6 puntos de porcentaje de tiro con 20 intentos" es literalmente el
#: ejemplo del documento (§4) para el triple; los libres se piden menos
#: volumen (`min_attempts_total` más bajo) porque un tirador de rol no llega
#: a 20 libres combinados en cinco partidos con la misma facilidad que a 20
#: triples.
_PLAYER_PROPORTION_METRICS = [
    _PlayerProportionMetric("fg3_pct", "el acierto en triples", "tpm", "tpa", 6.0, 20),
    _PlayerProportionMetric("ft_pct", "el acierto en tiros libres", "ftm", "fta", 8.0, 15),
]


def _split_recent_baseline(log: pd.DataFrame, last_n: int) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """Últimos `last_n` partidos (por `game_date`) contra el resto, de un log YA de una sola entidad."""
    ordered = log.sort_values("game_date")
    return ordered.tail(last_n), ordered.iloc[: max(0, len(ordered) - last_n)]


def _competition_note(recent: pd.DataFrame) -> Optional[str]:
    """"El calendario contamina" (§5): si los partidos recientes son todos de la misma competición, decirlo.

    v1 se limita a mencionarlo en la tarjeta, como pide el documento — ajustar
    por calidad de rival de verdad es v2 y necesita un modelo (§5).
    """
    if "competition" not in recent.columns or recent.empty:
        return None
    competitions = recent["competition"].dropna().unique()
    if len(competitions) == 1:
        return f"Los {len(recent)} partidos recientes son todos de {competitions[0]}: puede ser el rival, no un cambio real."
    return None


def _rate_per_game(df: pd.DataFrame, spec: "_PlayerRateMetric") -> List[float]:
    if spec.column not in df.columns:
        return []
    values = df[spec.column]
    if not spec.per40:
        return values.tolist()
    minutes = df["minutes"]
    per40 = 40.0 * values / minutes.replace(0, np.nan)
    return per40.tolist()


def detect_player_signals(game_log: pd.DataFrame, *, last_n: int = 5) -> List[Signal]:
    """Candidatos de jugador: minutos, tiro con volumen, pérdidas, faltas, rebote ofensivo.

    Args:
        game_log: salida de `queries.team_player_game_log` (una fila por
            jugador y partido JUGADO — `minutes > 0` ya filtrado ahí).
        last_n: K de la comparación (5 por defecto, §4 del documento).

    Returns:
        Candidatos SIN filtrar todavía (eso es trabajo de `select_top_
        signals`, que necesita ver TODOS los p-valores de la semana a la vez
        para corregir por comparaciones múltiples). Vacío si `game_log` lo
        está o si ningún jugador llega a `last_n` partidos recientes + `MIN_
        BASELINE_GAMES` de referencia.
    """
    if game_log.empty:
        return []
    candidates: List[Signal] = []

    for player_id, group in game_log.groupby("player_id"):
        name = str(group["player_name"].iloc[0])
        recent, baseline = _split_recent_baseline(group, last_n)
        if len(recent) < last_n or len(baseline) < MIN_BASELINE_GAMES:
            continue
        weight = float(pd.concat([recent, baseline])["minutes"].mean())
        note = _competition_note(recent)

        for spec in _PLAYER_RATE_METRICS:
            mean_r, mean_b, diff, p_value = two_mean_test(_rate_per_game(recent, spec), _rate_per_game(baseline, spec))
            if math.isnan(mean_r) or math.isnan(mean_b):
                continue
            verb = "subido" if diff >= 0 else "bajado"
            candidates.append(Signal(
                family="player", subject_id=str(player_id), subject_name=name, metric=spec.key,
                recent_value=mean_r, baseline_value=mean_b, effect=diff, unit=spec.unit,
                n_recent=len(recent), n_baseline=len(baseline), min_effect=spec.min_effect,
                weight=weight, p_value=p_value,
                headline_template=(
                    "{label} de {name} ha {verb} de {baseline:.1f} a {recent:.1f} {unit} en los últimos {n_recent} partidos."
                ),
                context={
                    "label": spec.label[0].upper() + spec.label[1:], "name": name, "verb": verb,
                    "baseline": mean_b, "recent": mean_r, "unit": spec.unit, "n_recent": len(recent),
                },
                ask_question=f"¿Qué explica que {spec.label} de {name} haya cambiado esta última semana?",
                extra_note=note,
            ))

        for spec in _PLAYER_PROPORTION_METRICS:
            recent_makes, recent_attempts = float(recent[spec.makes_column].sum()), float(recent[spec.attempts_column].sum())
            baseline_makes, baseline_attempts = (
                float(baseline[spec.makes_column].sum()), float(baseline[spec.attempts_column].sum()),
            )
            total_attempts = recent_attempts + baseline_attempts
            if total_attempts < spec.min_attempts_total or not recent_attempts or not baseline_attempts:
                continue
            diff_pp, p_value = two_proportion_test(recent_makes, recent_attempts, baseline_makes, baseline_attempts)
            recent_pct = 100.0 * recent_makes / recent_attempts
            baseline_pct = 100.0 * baseline_makes / baseline_attempts
            verb = "subido" if diff_pp >= 0 else "bajado"
            candidates.append(Signal(
                family="player", subject_id=str(player_id), subject_name=name, metric=spec.key,
                recent_value=recent_pct, baseline_value=baseline_pct, effect=diff_pp, unit="pp",
                n_recent=len(recent), n_baseline=len(baseline), min_effect=spec.min_effect_pp,
                weight=weight, p_value=p_value,
                headline_template=(
                    "{label} de {name} ha {verb} de {baseline:.0f}% a {recent:.0f}% en los últimos {n_recent} "
                    "partidos, con volumen: {attempts:.0f} intentos recientes."
                ),
                context={
                    "label": spec.label[0].upper() + spec.label[1:], "name": name, "verb": verb,
                    "baseline": baseline_pct, "recent": recent_pct, "n_recent": len(recent),
                    "attempts": recent_attempts,
                },
                ask_question=f"¿Qué explica que {spec.label} de {name} haya cambiado esta última semana?",
                extra_note=note,
            ))
    return candidates


@dataclass(frozen=True)
class _TeamRateMetric:
    key: str
    label: str
    column: str
    min_effect: float
    unit: str


#: "Equipo: los cuatro factores... ritmo" (§2) — el reparto de tiro por zona
#: vive aparte, en `detect_team_zone_signals` (mismo §2, "y reparto de tiro
#: por zona"): la v1 de este módulo lo dejó fuera porque no había un desglose
#: de zona PARTIDO A PARTIDO en ningún sitio; `queries.team_game_zone_counts`
#: ya lo da (extiende `team_shot_counts` con `game_id` en el `GROUP BY`, no es
#: una tabla nueva), así que ya no hace falta repetir `zone_matchup.py` cada
#: semana — aquí no se compara contra la liga (eso es la propuesta 08), se
#: compara el equipo CONSIGO MISMO, últimos K partidos contra el resto.
_TEAM_RATE_METRICS = [
    _TeamRateMetric("efg_pct", "El eFG%", "efg_pct", 5.0, "pp"),
    _TeamRateMetric("tov_pct", "El TOV%", "tov_pct", 3.0, "pp"),
    _TeamRateMetric("orb_pct", "El ORB%", "orb_pct", 4.0, "pp"),
    _TeamRateMetric("ft_rate", "La tasa de tiros libres", "ft_rate", 6.0, "pp"),
    _TeamRateMetric("pace", "El ritmo", "pace", 4.0, "posesiones/partido"),
]


def detect_team_signals(team_game_log: pd.DataFrame, *, team_id: str, team_name: str, last_n: int = 5) -> List[Signal]:
    """Candidatos de equipo: los cuatro factores + ritmo, últimos K partidos contra el resto.

    Args:
        team_game_log: salida de `queries.team_game_advanced_log`.
    """
    if team_game_log.empty:
        return []
    recent, baseline = _split_recent_baseline(team_game_log, last_n)
    if len(recent) < last_n or len(baseline) < MIN_BASELINE_GAMES:
        return []
    note = _competition_note(recent)

    candidates: List[Signal] = []
    for spec in _TEAM_RATE_METRICS:
        if spec.column not in team_game_log.columns:
            continue
        mean_r, mean_b, diff, p_value = two_mean_test(recent[spec.column].tolist(), baseline[spec.column].tolist())
        if math.isnan(mean_r) or math.isnan(mean_b):
            continue
        verb = "subido" if diff >= 0 else "bajado"
        candidates.append(Signal(
            family="team", subject_id=team_id, subject_name=team_name, metric=spec.key,
            recent_value=mean_r, baseline_value=mean_b, effect=diff, unit=spec.unit,
            n_recent=len(recent), n_baseline=len(baseline), min_effect=spec.min_effect,
            weight=TEAM_SIGNAL_WEIGHT, p_value=p_value,
            headline_template="{label} del equipo ha {verb} de {baseline:.1f} a {recent:.1f} en los últimos {n_recent} partidos.",
            context={"label": spec.label, "verb": verb, "baseline": mean_b, "recent": mean_r, "n_recent": len(recent)},
            ask_question=(
                f"¿Qué explica que {spec.label[0].lower() + spec.label[1:]} del equipo haya cambiado "
                "esta última semana?"
            ),
            extra_note=note,
        ))
    return candidates


#: Cambio mínimo, en puntos porcentuales del REPARTO de tiros (qué parte del
#: volumen total sale de esa zona), para que el desplazamiento de una zona
#: valga una señal (§2: "equipo... reparto de tiro por zona", ver
#: `08_donde_castigar_al_rival.md`) — mismo orden de magnitud que
#: `ROTATION_MIN_EFFECT_PP`: menos que esto es ruido de partido a partido
#: (qué tiros caen ese día), no un cambio real de patrón de ataque.
TEAM_ZONE_MIN_EFFECT_PP = 8.0

#: Intentos mínimos EN LA ZONA (recientes + línea base sumados) para someterla
#: al contraste — por debajo, la proporción se mueve de sobra con dos tiros
#: de suerte y el test no tiene nada fiable que decir.
TEAM_ZONE_MIN_ATTEMPTS = 20


def detect_team_zone_signals(
    game_zone_counts: pd.DataFrame, *, team_id: str, team_name: str, last_n: int = 5
) -> List[Signal]:
    """Candidatos de EQUIPO por zona: qué zona ha ganado o perdido peso en el reparto de tiro.

    A diferencia de `detect_team_signals` (contrastes en TASAS del propio
    partido), aquí el contraste es un `two_proportion_test` sobre los tiros:
    "de todos los intentos de la ventana, ¿qué parte salió de esta zona?" es
    una proporción igual que "de los intentos, ¿qué parte entró?" (mismo test
    que `two_proportion_test` en los porcentajes de tiro de jugador) — solo
    cambia qué se cuenta arriba y abajo.

    Args:
        game_zone_counts: salida de `queries.team_game_zone_counts` (una fila
            por partido, zona y si el tiro está localizado).

    Returns:
        Candidatos sin filtrar (igual que el resto de detectores). Vacío si
        `game_zone_counts` lo está, si no hay `last_n + MIN_BASELINE_GAMES`
        partidos con tiros, o si ninguna zona llega a `TEAM_ZONE_MIN_ATTEMPTS`.
    """
    if game_zone_counts.empty:
        return []
    usable = game_zone_counts[(game_zone_counts["located"] == 1) & game_zone_counts["zone_id"].notna()]
    if usable.empty:
        return []
    usable = usable.assign(zone_id=usable["zone_id"].astype(int).map(lambda z: ZONE_MERGES.get(z, z)))

    games = usable[["game_id", "game_date"]].drop_duplicates().sort_values("game_date")
    if len(games) < last_n + MIN_BASELINE_GAMES:
        return []
    recent_game_ids = set(games["game_id"].tail(last_n))
    baseline_game_ids = set(games["game_id"].iloc[: len(games) - last_n])

    recent = usable[usable["game_id"].isin(recent_game_ids)]
    baseline = usable[usable["game_id"].isin(baseline_game_ids)]
    recent_total = float(recent["shots"].sum())
    baseline_total = float(baseline["shots"].sum())
    if not recent_total or not baseline_total:
        return []

    recent_game_meta = usable.loc[usable["game_id"].isin(recent_game_ids), ["game_id", "competition"]].drop_duplicates()
    note = _competition_note(recent_game_meta) if "competition" in usable.columns else None

    # Un `zone_id` fundido por `ZONE_MERGES` puede traer más de una etiqueta
    # distinta (p.ej. si la fuente etiquetó igual dos filas con acentos
    # distintos) — se queda la más frecuente, mismo criterio pragmático que
    # `shot_quality` usa para lo mismo.
    zone_labels = usable.groupby("zone_id")["zone_label"].agg(lambda s: s.value_counts().idxmax())

    candidates: List[Signal] = []
    for zone_id, label in zone_labels.items():
        recent_shots = float(recent.loc[recent["zone_id"] == zone_id, "shots"].sum())
        baseline_shots = float(baseline.loc[baseline["zone_id"] == zone_id, "shots"].sum())
        if recent_shots + baseline_shots < TEAM_ZONE_MIN_ATTEMPTS:
            continue
        diff_pp, p_value = two_proportion_test(recent_shots, recent_total, baseline_shots, baseline_total)
        recent_share = 100.0 * recent_shots / recent_total
        baseline_share = 100.0 * baseline_shots / baseline_total
        verb = "ganado" if diff_pp >= 0 else "perdido"
        candidates.append(Signal(
            family="team", subject_id=team_id, subject_name=team_name, metric=f"zone_share:{int(zone_id)}",
            recent_value=recent_share, baseline_value=baseline_share, effect=diff_pp, unit="pp",
            n_recent=len(recent_game_ids), n_baseline=len(games) - len(recent_game_ids),
            min_effect=TEAM_ZONE_MIN_EFFECT_PP, weight=TEAM_SIGNAL_WEIGHT, p_value=p_value,
            headline_template=(
                "El equipo ha {verb} peso tirando desde {label}: del {baseline:.0f}% al {recent:.0f}% de los "
                "tiros en los últimos {n_recent} partidos."
            ),
            context={
                "label": label, "verb": verb, "baseline": baseline_share, "recent": recent_share,
                "n_recent": len(recent_game_ids),
            },
            ask_question=f"¿Qué explica que el equipo esté tirando más desde {label} esta última semana?",
            extra_note=note,
        ))
    return candidates


#: Mínimo de minutos JUNTOS en la ventana reciente para que una pareja entre
#: en el radar — con menos, un cruce de banquillo de dos minutos calcularía
#: un "peso" que no significa nada.
ROTATION_MIN_RECENT_MINUTES = 10.0

#: Cambio de peso mínimo, en puntos porcentuales de los minutos de tramo
#: trackeados, para que valga la pena avisar (§2: "una pareja... que ha
#: ganado peso").
ROTATION_MIN_EFFECT_PP = 8.0


def detect_rotation_signals(pair_window: pd.DataFrame, *, last_n: int = 5) -> List[Signal]:
    """Candidatos de rotación: parejas que han ganado (o perdido) peso en los últimos K partidos.

    Args:
        pair_window: salida de `queries_assistant.pair_minutes_by_window`.

    El contraste es un `two_proportion_test` sobre los SEGUNDOS de tramo:
    "de los minutos de tramo trackeados en la ventana, ¿qué parte compartió
    esta pareja?" es una proporción igual que "de los intentos, ¿qué parte
    entró?" — mismo test, otra unidad.
    """
    if pair_window.empty:
        return []
    candidates: List[Signal] = []
    for row in pair_window.itertuples(index=False):
        if row.recent_minutes < ROTATION_MIN_RECENT_MINUTES:
            continue
        diff_pp, p_value = two_proportion_test(
            row.recent_seconds, row.recent_total_seconds, row.baseline_seconds, row.baseline_total_seconds,
        )
        verb = "ganado" if diff_pp >= 0 else "perdido"
        candidates.append(Signal(
            family="rotation", subject_id=row.player_ids, subject_name=row.jugadores, metric="pair_share",
            recent_value=row.recent_share, baseline_value=row.baseline_share, effect=diff_pp, unit="pp",
            n_recent=int(row.n_recent_games), n_baseline=int(row.n_baseline_games),
            min_effect=ROTATION_MIN_EFFECT_PP, weight=row.recent_minutes, p_value=p_value,
            headline_template=(
                "La pareja {name} ha {verb} peso en la rotación: del {baseline:.0f}% al {recent:.0f}% de los "
                "minutos de pista en los últimos {n_recent} partidos."
            ),
            context={
                "name": row.jugadores, "verb": verb, "baseline": row.baseline_share,
                "recent": row.recent_share, "n_recent": int(row.n_recent_games),
            },
            ask_question=f"¿Qué explica que {row.jugadores} estén jugando más juntos esta última semana?",
        ))
    return candidates


def detect_load_signals(
    rolling_latest: pd.DataFrame, *, threshold: float = LOAD_ALERT_MINUTES_7D
) -> List[Signal]:
    """Candidatos de carga: jugadores por encima del aviso de minutos en la ventana de 7 días.

    A diferencia de las otras tres familias, ESTA NO ES una comparación
    estadística: es el umbral de calendario de la propuesta 04, que ya vive
    en `app/screens/estado_equipo.py` — aquí solo se reutiliza para que la
    semana lo asome sin que haya que ir a mirar esa pantalla (§2: "Carga: un
    jugador entrando en zona de sobrecarga"). `p_value=None` en el `Signal`
    resultante es justo la marca de "esto no pasó por Benjamini-Hochberg,
    pasó por un umbral fijo" — `select_top_signals` lo respeta.

    Args:
        rolling_latest: UNA fila por jugador, la ventana de 7 días MÁS
            RECIENTE (`queries.rolling_load(..., days=7)` ya filtrada a
            `game_date == rolling_latest['game_date'].max()`, como hace
            `app/screens/estado_equipo.py`).
    """
    if rolling_latest.empty:
        return []
    candidates: List[Signal] = []
    for row in rolling_latest.itertuples(index=False):
        excess = float(row.rolling_minutes) - threshold
        if excess <= 0:
            continue
        candidates.append(Signal(
            family="load", subject_id=str(row.player_id), subject_name=str(row.player_name),
            metric="rolling_minutes_7d", recent_value=float(row.rolling_minutes), baseline_value=threshold,
            effect=excess, unit="min", n_recent=int(row.games_in_window), n_baseline=0,
            min_effect=0.0, weight=float(row.rolling_minutes) / 40.0, p_value=None,
            headline_template=(
                "{name} lleva {recent:.0f} minutos en los últimos 7 días ({n_recent} partidos): por encima "
                "del aviso de carga ({threshold:.0f} min)."
            ),
            context={
                "name": row.player_name, "recent": float(row.rolling_minutes),
                "n_recent": int(row.games_in_window), "threshold": threshold,
            },
            ask_question=f"¿Cómo está la carga de minutos de {row.player_name} esta última semana?",
        ))
    return candidates


# ================================================================= redacción ==
#
# Dos capas, mismo orden de preferencia que `app/reports/postgame_ppt.py`
# (§6 del documento): las señales las detecta el código, SIEMPRE — todo lo de
# arriba ya deja `headline`/`confidence` listos y correctos sin LLM. Esto de
# abajo es opcional y solo pule la prosa; si no hay LLM configurado o falla,
# el `headline` de reglas se queda tal cual, nunca en blanco.


def polish_headlines(client, signals: List[Signal], *, team_name: str) -> List[Signal]:
    """Reescribe `headline` con el LLM si está disponible; si no, deja las reglas tal cual.

    Args:
        client: `assistant.llm.LLMClient` ya construido, o `None` para
            saltarse esta capa entera.
        signals: la salida YA FILTRADA de `select_top_signals` (máximo 5) —
            nunca se le pasan candidatos sin filtrar: el LLM redacta, no
            decide qué es una señal (§6: "el LLM solo las redacta mejor si
            está configurado. Nunca al revés").

    Returns:
        La MISMA lista, con `headline` sustituido donde el LLM respondió algo
        usable. Los números (`context`) no cambian: el LLM no los ve, solo
        recibe el titular de reglas y se le pide que lo pula, no que invente
        cifras nuevas.
    """
    if client is None or not signals:
        return signals
    try:  # pragma: no cover - ver nota en tools/context.py sobre el import de dos formas
        from app.assistant.llm import LLMError
    except ImportError:  # pragma: no cover
        from assistant.llm import LLMError

    import json
    import re

    fence_re = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
    system_prompt = (
        f"Eres el analista de datos del {team_name}. Te paso una lista de titulares YA CALCULADOS sobre "
        "cambios de la última semana (números correctos, no los cambies ni inventes otros). Reescribe cada "
        "titular en español, más natural y directo, en una frase corta (máximo 20 palabras), conservando "
        "TODOS los números y nombres propios tal cual. No añadas explicaciones ni emojis.\n\n"
        'Responde EXCLUSIVAMENTE con JSON válido: {"<índice>": "titular reescrito", ...} — un índice de '
        "texto (\"0\", \"1\"...) por cada titular recibido, en el mismo orden."
    )
    payload = {"titulares": [s.headline for s in signals]}
    message = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    try:
        response = client.chat([message], [], system=system_prompt)
    except LLMError:
        return signals

    cleaned = fence_re.sub("", response.text).strip()
    try:
        rewritten = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return signals
    if not isinstance(rewritten, dict):
        return signals

    for index, signal in enumerate(signals):
        new_headline = rewritten.get(str(index))
        if isinstance(new_headline, str) and new_headline.strip():
            signal.headline = new_headline.strip()
    return signals


def all_candidates(
    *,
    player: Sequence[Signal] = (),
    team: Sequence[Signal] = (),
    rotation: Sequence[Signal] = (),
    load: Sequence[Signal] = (),
) -> List[Signal]:
    """Junta las cuatro familias en una sola lista, para pasar a `select_top_signals`."""
    return list(chain(player, team, rotation, load))
