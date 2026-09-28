"""Plan de rotación contra el rival: sus ventanas débiles × nuestros mejores quintetos.

Propuesta 15 (`doc/features/propuestas/15_plan_de_rotacion.md`), el cruce que
dejaron apuntado la 12 (RAPM y constructor) y la 13 (patrón de rotación). La
13 dice CUÁNDO flojea el rival —cuando sienta a un titular, o en su peor tramo
de reloj— y la 12 sabe puntuar quintetos; juntas contestan la pregunta del
cuerpo técnico: "en los minutos 8-12, cuando se sienta su base, ¿qué sacamos?".

**Contra quién se proyecta.** No contra un rival medio: contra los cinco que el
rival suele tener en pista EN ESA VENTANA (los de más proporción en pista en
esos minutos, `rotation_patterns.minute_shares`). El RAPM es aditivo y la
regresión ya incluye a los rivales, así que la diferencia esperada de nuestro
quinteto `L` contra su quinteto `R` es

    Σ RAPM(L) − Σ RAPM(R) ± ventaja de campo

con el mismo ajuste (los dos equipos en la misma temporada y regresión).

**Lo que el modelo aditivo NO puede hacer**, dicho aquí para no venderlo: el
ORDEN de nuestros quintetos es el mismo contra cualquier rival (restar una
constante no reordena). Lo que cambia de una ventana a otra es el margen
esperado y, sobre todo, **la distancia entre lo que solemos tener en pista en
esos minutos y lo mejor disponible**: esa diferencia es la decisión de
rotación que el plan pone encima de la mesa ("adelanta el descanso de X para
tener a Y y Z en pista cuando se sienta su base").

Lógica pura sobre `pandas`, sin Streamlit ni SQLAlchemy (regla del paquete).
Reutiliza el cálculo de `rotation_patterns` (ventanas, rendimiento por
bloque) y de `impact` (candidatos, constructor, lo observado).
"""
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import impact
    from app.analytics import rotation_patterns as rp
except ImportError:  # pragma: no cover
    from analytics import impact
    from analytics import rotation_patterns as rp

#: Cuántos de los jugadores de más minutos del rival aportan ventanas de
#: descanso (los mismos tres que describe el resumen de la propuesta 13).
KEY_PLAYERS = 3

#: Cuántos de sus peores bloques de reloj entran como ventana además de los
#: descansos. Dos: el peor suele ser evidente y el segundo a menudo es el
#: mismo valle visto desde otro bloque; más ya es ruido.
WORST_BLOCKS = 2

#: Tope de ventanas del plan: un plan de partido con diez ventanas no se
#: ejecuta desde el banquillo.
MAX_WINDOWS = 5

#: Minutos mínimos del rival en la ventana (sumando partidos) para citar su
#: diferencia por 40 en ella. Por debajo el número existe pero no se enseña
#: en las frases: dos minutos de ventana en cinco partidos son diez minutos.
MIN_WINDOW_MINUTES = 20.0

#: Minutos mínimos con el equipo propio para entrar por defecto entre los
#: candidatos: mismo corte que el constructor de quintetos
#: (`components.impact.BUILDER_DEFAULT_MIN_MINUTES`), por el mismo motivo.
DEFAULT_MIN_MINUTES = 100.0

#: ...pero a principio de temporada (tres partidos) ni los titulares llegan a
#: 100 minutos y el plan saldría vacío: el corte nunca pasa de esta fracción
#: de los minutos del jugador más usado del equipo.
DEFAULT_MIN_SHARE_OF_TOP = 0.25

#: Mejora mínima (por 40) sobre nuestra rotación habitual para proponer un
#: cambio. Por debajo, el plan dice que lo habitual ya vale: medio punto por
#: 40 en una ventana de cuatro minutos es menos de una canasta en el partido.
MIN_GAIN = 1.0


# ------------------------------------------------------------------ piezas --


def home_term(home_advantage: float, is_home: Optional[bool]) -> float:
    """Ventaja de campo desde NUESTRO punto de vista: +h en casa, −h fuera, 0 si no se sabe.

    El ajuste (`impact.fit_rapm`) estima `h` como puntos por 40 a favor del
    local, así que jugando fuera la tenemos en contra con el mismo tamaño.
    """
    if is_home is None or home_advantage is None or not math.isfinite(float(home_advantage)):
        return 0.0
    return float(home_advantage) if is_home else -float(home_advantage)


def lineup_value(rapm: pd.DataFrame, players: Iterable[str]) -> float:
    """Suma del RAPM de un grupo de jugadores (sin fila = 0, jugador medio, como en `impact.best_lineups`)."""
    value = dict(zip(rapm["player_id"], rapm["rapm"])) if not rapm.empty else {}
    return float(sum(value.get(p, 0.0) for p in players))


def project_vs_five(
    rapm: pd.DataFrame, ours: Iterable[str], theirs: Iterable[str], *, home_advantage: float = 0.0,
    is_home: Optional[bool] = None,
) -> float:
    """Diferencia esperada por 40 de nuestro quinteto contra un quinteto concreto del rival."""
    return lineup_value(rapm, ours) - lineup_value(rapm, theirs) + home_term(home_advantage, is_home)


def default_candidates(
    segments: pd.DataFrame, team_id: str, min_minutes: float = DEFAULT_MIN_MINUTES
) -> List[str]:
    """Jugadores del equipo con minutos suficientes en el ajuste, de más a menos minutos.

    Corte = `min(min_minutes, DEFAULT_MIN_SHARE_OF_TOP × minutos del más
    usado)`: con la temporada avanzada manda `min_minutes` (fuera los de
    minutos de la basura); en sus primeros partidos, la fracción.
    """
    minutes = impact.team_player_minutes(segments, team_id)
    if minutes.empty:
        return []
    cut = min(min_minutes, DEFAULT_MIN_SHARE_OF_TOP * float(minutes.max()))
    return [p for p in minutes.index if minutes[p] >= cut]


def window_presence(shares: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    """Proporción media en pista de cada jugador en los minutos `start..end` (ambos incluidos).

    Returns:
        `player_id, player_name, share`, de más a menos presente.
    """
    columns = ["player_id", "player_name", "share"]
    if shares.empty:
        return pd.DataFrame(columns=columns)
    inside = shares[(shares["minute"] >= start) & (shares["minute"] <= end)]
    out = inside.groupby(["player_id", "player_name"], as_index=False)["share"].mean()
    return out.sort_values(["share", "player_id"], ascending=[False, True]).reset_index(drop=True)[columns]


def typical_five(
    shares: pd.DataFrame, start: int, end: int, *, exclude: Iterable[str] = (), among: Optional[Iterable[str]] = None
) -> Tuple[Tuple[str, ...], float]:
    """Los cinco más presentes en la ventana y su presencia media.

    Args:
        exclude: quien no puede estar (el jugador cuyo descanso define la
            ventana: la premisa es que está sentado, aunque algún partido
            no lo esté).
        among: si se da, solo estos jugadores (nuestros disponibles).

    Returns:
        (tupla de ids, de más a menos presente; presencia media de los cinco,
        0-1). Tupla vacía si no hay cinco jugadores con minutos en la ventana.
    """
    presence = window_presence(shares, start, end)
    presence = presence[~presence["player_id"].isin(set(exclude)) & (presence["share"] > 0)]
    if among is not None:
        presence = presence[presence["player_id"].isin(set(among))]
    if len(presence) < 5:
        return (), 0.0
    top = presence.head(5)
    return tuple(top["player_id"]), float(top["share"].mean())


def window_performance(minute_blocks: pd.DataFrame, start: int, end: int) -> Tuple[float, float]:
    """Minutos y diferencia por 40 del equipo en los minutos `start..end`.

    Args:
        minute_blocks: `rotation_patterns.block_performance(rows, block_minutes=1)`
            (un bloque por minuto de reloj): así la ventana suma exactamente
            sus minutos, con el mismo reparto de puntos por tiempo que la
            diferencia por tramo de la propuesta 13.

    Returns:
        (minutos del equipo sumando partidos, +/- por 40; `nan` sin minutos).
    """
    inside = minute_blocks[(minute_blocks["start_minute"] >= start) & (minute_blocks["start_minute"] <= end)]
    minutes = float(inside["minutes"].sum())
    points = float(inside["plus_minus"].sum())
    return minutes, (40.0 * points / minutes if minutes > 0 else float("nan"))


def _overlap(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)


# --------------------------------------------------------------- ventanas --


def attack_windows(
    rival_rows: pd.DataFrame,
    rival_on_off: Optional[pd.DataFrame] = None,
    *,
    key_players: int = KEY_PLAYERS,
    worst_blocks: int = WORST_BLOCKS,
    max_windows: int = MAX_WINDOWS,
) -> List[dict]:
    """Ventanas del partido donde atacar al rival, con su quinteto habitual en cada una.

    Dos fuentes, en este orden de prioridad:

    1. **Descansos habituales** de sus `key_players` jugadores de más minutos
       (`rotation_patterns.player_rotation_table`).
    2. **Sus peores bloques de reloj** con diferencia negativa y muestra
       suficiente (`rotation_patterns.block_performance`), salvo los que ya
       caen en su mayor parte dentro de un descanso de la lista (sería la
       misma ventana dos veces).

    Args:
        rival_rows: `queries.team_stint_rows` del rival (una fila por tramo y
            jugador).
        rival_on_off: `queries_assistant.player_on_off` del rival (opcional):
            añade a cada descanso cómo le va al equipo sin ese jugador en la
            temporada (solo si es fiable).

    Returns:
        Lista (en orden de reloj) de dicts: `kind` ("descanso" o "tramo"),
        `start_minute`, `end_minute`, `label` ("8-12"), `player_id`/`player_name`
        (del descanso, `None` en un tramo), `rival_minutes`, `rival_per_40`
        (diferencia del rival en esa ventana de reloj), `off_per_40` (On/Off
        del que descansa, o `None`), `rival_five` (tupla de ids) y
        `rival_five_share` (presencia media de esos cinco en la ventana).
        Vacía si no hay tramos.
    """
    if rival_rows.empty:
        return []
    shares = rp.minute_shares(rival_rows)
    rotation = rp.player_rotation_table(shares)
    minute_blocks = rp.block_performance(rival_rows, block_minutes=1)
    onoff = (
        rival_on_off.set_index("player_id") if rival_on_off is not None and not rival_on_off.empty else None
    )

    picked: List[dict] = []
    for rec in rotation.head(key_players).to_dict("records"):
        for start, end in rec["rest_windows"]:
            off = None
            if onoff is not None and rec["player_id"] in onoff.index:
                row = onoff.loc[rec["player_id"]]
                if bool(row.get("reliable", False)) and pd.notna(row["off_per_40"]):
                    off = float(row["off_per_40"])
            picked.append({
                "kind": "descanso", "start_minute": int(start), "end_minute": int(end),
                "player_id": rec["player_id"], "player_name": rec["player_name"], "off_per_40": off,
            })

    blocks = rp.block_performance(rival_rows)
    solid = blocks[blocks["minutes"] >= rp.MIN_BLOCK_MINUTES].dropna(subset=["per_40"])
    for rec in solid[solid["per_40"] < 0].sort_values("per_40").head(worst_blocks).to_dict("records"):
        start = int(rec["start_minute"])
        end = min(start + rp.BLOCK_MINUTES - 1, rp.MINUTES)
        span = end - start + 1
        if any(_overlap((start, end), (w["start_minute"], w["end_minute"])) * 2 >= span for w in picked):
            continue
        picked.append({
            "kind": "tramo", "start_minute": start, "end_minute": end,
            "player_id": None, "player_name": None, "off_per_40": None,
        })

    windows = []
    for window in picked[:max_windows]:
        start, end = window["start_minute"], window["end_minute"]
        exclude = [window["player_id"]] if window["player_id"] else []
        five, presence = typical_five(shares, start, end, exclude=exclude)
        minutes, per_40 = window_performance(minute_blocks, start, end)
        windows.append({
            **window,
            "label": f"{start}-{end}" if start != end else f"{start}",
            "rival_minutes": minutes,
            "rival_per_40": per_40,
            "rival_five": five,
            "rival_five_share": presence,
        })
    return sorted(windows, key=lambda w: (w["start_minute"], w["end_minute"]))


# -------------------------------------------------------------------- plan --


def build_plan(
    rival_rows: pd.DataFrame,
    own_rows: pd.DataFrame,
    fit: dict,
    segments: pd.DataFrame,
    own_team_id: str,
    candidates: Optional[Iterable[str]] = None,
    *,
    rival_on_off: Optional[pd.DataFrame] = None,
    is_home: Optional[bool] = None,
    positions: Optional[Dict[str, Optional[str]]] = None,
    required_positions: Optional[Dict[str, int]] = None,
    top: int = 3,
    key_players: int = KEY_PLAYERS,
    max_windows: int = MAX_WINDOWS,
) -> List[dict]:
    """El plan entero: para cada ventana del rival, nuestros mejores quintetos contra su quinteto habitual.

    Args:
        rival_rows / own_rows: `queries.team_stint_rows` de cada equipo en la
            MISMA temporada que `fit` (la de scouting del rival). `own_rows`
            puede venir vacío: entonces no hay "rotación habitual" propia con
            la que comparar, pero el plan sale igual.
        fit / segments: `queries_assistant.season_impact(...)["fit"/"segments"]`
            de esa temporada; el RAPM de los dos equipos sale del mismo ajuste.
        candidates: nuestros disponibles. `None` = `default_candidates`.
        is_home: si jugamos en casa (suma la ventaja de campo del ajuste),
            fuera (la resta) o `None` (no se aplica).
        positions / required_positions: se pasan tal cual a
            `impact.best_lineups` ("al menos un base y un pívot").
        top: quintetos propuestos por ventana.

    Returns:
        Las ventanas de `attack_windows`, cada una con además:
        `rival_five_rapm` (suma del RAPM de su quinteto habitual),
        `own_usual` (tupla: nuestros cinco disponibles más presentes en esos
        minutos, o `()`), `own_usual_margin` (su diferencia proyectada contra
        el quinteto rival, o `nan`) y `lineups`: DataFrame con `players`,
        `projected_margin` (por 40, contra su quinteto, con campo),
        `observed_minutes`, `observed_per_40` y `gain_vs_usual` (`nan` sin
        rotación habitual).
    """
    windows = attack_windows(rival_rows, rival_on_off, key_players=key_players, max_windows=max_windows)
    if not windows:
        return []
    rapm = fit["players"]
    home = home_term(fit.get("home_advantage", float("nan")), is_home)
    pool = list(candidates) if candidates is not None else default_candidates(segments, own_team_id)
    observed = impact.observed_lineups(segments, own_team_id)
    # El orden de nuestros quintetos no depende del rival (modelo aditivo, ver
    # docstring del módulo): se enumeran UNA vez y cada ventana solo resta su
    # quinteto rival.
    best = impact.best_lineups(
        rapm, pool, observed=observed, positions=positions, required_positions=required_positions, top=top
    )
    own_shares = rp.minute_shares(own_rows) if not own_rows.empty else pd.DataFrame()

    plan = []
    for window in windows:
        rival_value = lineup_value(rapm, window["rival_five"])
        usual, _ = (
            typical_five(own_shares, window["start_minute"], window["end_minute"], among=pool)
            if not own_shares.empty else ((), 0.0)
        )
        usual_margin = lineup_value(rapm, usual) - rival_value + home if usual else float("nan")
        lineups = best.copy()
        lineups["projected_margin"] = lineups["projected_per_40"].astype(float) - rival_value + home
        lineups["gain_vs_usual"] = lineups["projected_margin"] - usual_margin
        plan.append({
            **window,
            "rival_five_rapm": rival_value,
            "own_usual": usual,
            "own_usual_margin": usual_margin,
            "lineups": lineups[
                ["players", "projected_margin", "observed_minutes", "observed_per_40", "gain_vs_usual"]
            ].reset_index(drop=True),
        })
    return plan


# ------------------------------------------------------------------ frases --


def _names(players: Sequence[str], names: Dict[str, str], sep: str = " · ") -> str:
    return sep.join(names.get(p, p) for p in players)


def _window_intro(window: dict, rival_name: str) -> str:
    if window["kind"] == "descanso":
        text = f"Minutos {window['label']}, cuando descansa {window['player_name']}"
    else:
        text = f"Minutos {window['label']}, uno de sus peores tramos de reloj"
    if window["rival_minutes"] >= MIN_WINDOW_MINUTES and pd.notna(window["rival_per_40"]):
        text += f": {rival_name} hace {window['rival_per_40']:+.1f} por 40 en esa ventana"
        if window.get("off_per_40") is not None:
            text += f" (sin él, {window['off_per_40']:+.1f} en la temporada)"
    return text + "."


def _compact_line(window: dict, names: Dict[str, str]) -> str:
    """Versión de una línea para una diapositiva: ventana, motivo, quinteto y cifras."""
    reason = f"descansa {window['player_name']}" if window["kind"] == "descanso" else "tramo flojo"
    if window["rival_minutes"] >= MIN_WINDOW_MINUTES and pd.notna(window["rival_per_40"]):
        reason += f", {window['rival_per_40']:+.1f} por 40"
    text = f"Min {window['label']} ({reason})"
    lineups = window["lineups"]
    if lineups.empty or not window["rival_five"]:
        return text + "."
    first = lineups.iloc[0]
    text += f": {_names(first['players'], names)}, {first['projected_margin']:+.1f} contra su quinteto"
    if pd.notna(first["gain_vs_usual"]) and float(first["gain_vs_usual"]) >= MIN_GAIN:
        text += f" ({float(first['gain_vs_usual']):+.1f} sobre lo habitual)"
    return text + "."


def plan_insights(
    rival_name: str, plan: Sequence[dict], names: Dict[str, str], *, compact: bool = False
) -> List[str]:
    """Una frase por ventana, lista para la pizarra (sin LLM, como `rotation_patterns.rotation_insights`).

    Args:
        names: `{player_id: nombre}` de los dos equipos (p.ej.
            `season_impact(...)["names"]`).
        compact: una línea corta por ventana (para el dossier `.pptx`, donde
            la frase completa no cabe en una diapositiva de viñetas).
    """
    if compact:
        return [_compact_line(window, names) for window in plan]
    lines = []
    for window in plan:
        text = _window_intro(window, rival_name)
        if window["rival_five"]:
            text += f" Su quinteto habitual ahí: {_names(window['rival_five'], names, ', ')}."
        lineups = window["lineups"]
        if lineups.empty or not window["rival_five"]:
            lines.append(text)
            continue
        first = lineups.iloc[0]
        text += (
            f" Nuestro mejor quinteto disponible: {_names(first['players'], names)}, "
            f"{first['projected_margin']:+.1f} por 40 proyectado contra ellos"
        )
        minutes = float(first["observed_minutes"])
        if minutes >= 10 and pd.notna(first["observed_per_40"]):
            text += f" ({minutes:.0f} min reales juntos, {first['observed_per_40']:+.1f} por 40)."
        else:
            text += " (apenas han jugado juntos: idea para probar)."
        usual = window["own_usual"]
        if usual and pd.notna(window["own_usual_margin"]):
            gain = float(first["gain_vs_usual"])
            if tuple(sorted(usual)) == tuple(first["players"]) or gain < MIN_GAIN:
                text += (
                    f" Lo que solemos tener en pista en esos minutos ya es de lo mejor "
                    f"({window['own_usual_margin']:+.1f})."
                )
            else:
                text += (
                    f" Lo que solemos tener en pista en esos minutos ({_names(usual, names)}) proyecta "
                    f"{window['own_usual_margin']:+.1f}: el cambio vale {gain:+.1f} por 40."
                )
        lines.append(text)
    return lines
