"""Patrón de rotación de un equipo: quién está en pista en cada minuto, y qué pasa entonces.

Propuesta 13 (`doc/features/propuestas/13_patron_de_rotacion.md`). La vista de
rotaciones de "Partidos anteriores" enseña UN partido; preparando el
siguiente, la pregunta es la costumbre: "¿cuándo sienta a su base?", "¿con qué
quinteto sale?", "¿quién cierra los partidos apretados?", "¿en qué minutos se
le cae el marcador?". Todo sale de `lineup_stints` agregando partido a partido
por minuto de reloj, sin ningún dato nuevo.

Convenciones:

- **Minuto de partido 1..40**, tiempo reglamentario. Las prórrogas se
  descartan: son pocas y su rotación no es la habitual (faltas, cansancio).
- **Proporción en pista** de un jugador en el minuto `m` = segundos que
  estuvo en pista en ese minuto, sumados en todos los partidos, entre
  60 × partidos del equipo. Un partido en que no jugó cuenta como 0: si se
  prepara un partido, la baja habitual también es información.
- **Diferencia por bloque de minutos**: los puntos de cada tramo se reparten
  entre los bloques que toca en proporción al tiempo. `lineup_stints` no
  guarda el instante de cada canasta dentro del tramo; los tramos duran unos
  pocos minutos, así que el reparto es una aproximación honesta y se dice.

Lógica pura sobre `pandas`, sin Streamlit ni SQLAlchemy (regla del paquete).
"""
import math
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

REGULATION_SECONDS = 2400.0
MINUTES = 40

#: Por debajo de esta proporción en pista, el minuto cuenta como "descansa
#: habitualmente". 0,35 y no 0,5: un titular que se sienta ese minuto dos de
#: cada tres partidos ya es un patrón aprovechable para quien prepara el
#: partido, y a 0,5 las ventanas de los jugadores de más minutos casi
#: desaparecen.
REST_THRESHOLD = 0.35

#: Ventanas de descanso más cortas que esto no se enseñan: un minuto suelto
#: por debajo del umbral es ruido de cuándo cae el cambio.
MIN_REST_WINDOW = 2

#: Partido "apretado" para la lista de cerradores: margen absoluto a falta de
#: `CLOSING_FROM_MINUTE` minutos.
CLOSE_MARGIN = 8
CLOSING_FROM_MINUTE = 35

#: Tamaño de los bloques de minutos para la diferencia por tramo del partido.
BLOCK_MINUTES = 4

#: Minutos mínimos del EQUIPO en un bloque (sumando todos los partidos) para
#: nombrarlo como mejor o peor tramo en el resumen.
MIN_BLOCK_MINUTES = 60.0


def _unique_stints(stint_rows: pd.DataFrame) -> pd.DataFrame:
    """Una fila por tramo (sin jugadores), recortada al tiempo reglamentario."""
    stints = stint_rows.drop_duplicates("stint_id")[
        ["stint_id", "game_id", "start_seconds", "end_seconds", "points_for", "points_against", "margin_start"]
    ].copy()
    return stints[stints["start_seconds"] < REGULATION_SECONDS]


def _minute_overlaps(start: float, end: float) -> List[Tuple[int, float]]:
    """`[(minuto 1..40, segundos dentro de ese minuto)]` de un intervalo."""
    start, end = max(0.0, start), min(REGULATION_SECONDS, end)
    out = []
    minute = int(start // 60)
    while minute * 60 < end and minute < MINUTES:
        overlap = min(end, (minute + 1) * 60) - max(start, minute * 60)
        if overlap > 0:
            out.append((minute + 1, overlap))
        minute += 1
    return out


def minute_shares(stint_rows: pd.DataFrame) -> pd.DataFrame:
    """Proporción de cada minuto de partido que cada jugador pasa en pista.

    Args:
        stint_rows: una fila por (tramo, jugador) de UN equipo, con
            `stint_id, game_id, start_seconds, end_seconds, player_id,
            player_name` (salida de `queries.team_stint_rows`).

    Returns:
        `player_id, player_name, minute, share` con las 40 filas de cada
        jugador (0 donde nunca estuvo). Vacío si no hay tramos.
    """
    columns = ["player_id", "player_name", "minute", "share"]
    if stint_rows.empty:
        return pd.DataFrame(columns=columns)
    n_games = stint_rows["game_id"].nunique()
    seconds: Dict[Tuple[str, int], float] = {}
    for rec in stint_rows[["player_id", "start_seconds", "end_seconds"]].to_dict("records"):
        for minute, overlap in _minute_overlaps(rec["start_seconds"], rec["end_seconds"]):
            key = (rec["player_id"], minute)
            seconds[key] = seconds.get(key, 0.0) + overlap
    names = dict(stint_rows.drop_duplicates("player_id")[["player_id", "player_name"]].to_numpy())
    rows = [
        {
            "player_id": pid,
            "player_name": name,
            "minute": minute,
            "share": min(1.0, seconds.get((pid, minute), 0.0) / (60.0 * n_games)),
        }
        for pid, name in names.items()
        for minute in range(1, MINUTES + 1)
    ]
    return pd.DataFrame(rows, columns=columns)


def player_rotation_table(shares: pd.DataFrame) -> pd.DataFrame:
    """Resumen por jugador: minutos por partido y ventanas de descanso habituales.

    Returns:
        `player_id, player_name, minutes_per_game, rest_windows` (lista de
        `(desde, hasta)` en minutos de partido, ambos incluidos), ordenado
        por minutos.
    """
    columns = ["player_id", "player_name", "minutes_per_game", "rest_windows"]
    if shares.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for (pid, name), group in shares.groupby(["player_id", "player_name"], sort=False):
        series = group.sort_values("minute")["share"].tolist()
        rows.append({
            "player_id": pid,
            "player_name": name,
            "minutes_per_game": float(sum(series)),
            "rest_windows": rest_windows(series),
        })
    return pd.DataFrame(rows, columns=columns).sort_values("minutes_per_game", ascending=False).reset_index(drop=True)


def rest_windows(
    series: Sequence[float], threshold: float = REST_THRESHOLD, min_length: int = MIN_REST_WINDOW
) -> List[Tuple[int, int]]:
    """Ventanas en que el jugador suele estar sentado, DENTRO de su franja de juego.

    Solo cuentan los minutos entre el primero y el último en que el jugador
    está en pista al menos `threshold`: fuera de ahí no es un descanso, es
    que sale desde el banquillo o que no cierra. Un suplente que entra en el
    minuto 6 no "descansa" del 1 al 5.
    """
    playing = [i for i, share in enumerate(series) if share >= threshold]
    if not playing:
        return []
    first, last = playing[0], playing[-1]
    windows, start = [], None
    for i in range(first, last + 1):
        if series[i] < threshold:
            start = i if start is None else start
        elif start is not None:
            if i - start >= min_length:
                windows.append((start + 1, i))
            start = None
    return windows


def starting_lineups(stint_rows: pd.DataFrame) -> pd.DataFrame:
    """Quintetos iniciales usados y en cuántos partidos.

    Returns:
        `players` (tupla de nombres ordenada), `games`, `share`, de más a
        menos usado. Un partido cuyo primer tramo no arranca en el segundo 0
        (hueco en la reconstrucción) no cuenta.
    """
    columns = ["players", "games", "share"]
    if stint_rows.empty:
        return pd.DataFrame(columns=columns)
    first = stint_rows[stint_rows["start_seconds"] <= 1.0]
    n_games = stint_rows["game_id"].nunique()
    per_game = (
        first.sort_values("player_name").groupby("game_id")["player_name"].agg(tuple)
    )
    per_game = per_game[per_game.map(len) == 5]
    if per_game.empty:
        return pd.DataFrame(columns=columns)
    counts = per_game.value_counts().rename_axis("players").reset_index(name="games")
    counts["share"] = counts["games"] / n_games
    return counts[columns]


def _margin_at(stints: pd.DataFrame, seconds: float) -> Dict[str, int]:
    """Margen del equipo (a favor − en contra) por partido en el instante `seconds`.

    Se interpola dentro del tramo que cubre ese instante —mismo reparto
    proporcional que `block_performance`—: el tramo sabe con qué margen entró
    y cuántos puntos hubo dentro, no en qué segundo cayó cada uno.
    """
    out = {}
    covering = stints[(stints["start_seconds"] <= seconds) & (stints["end_seconds"] > seconds)]
    for rec in covering.to_dict("records"):
        duration = rec["end_seconds"] - rec["start_seconds"]
        frac = (seconds - rec["start_seconds"]) / duration if duration > 0 else 0.0
        out[rec["game_id"]] = rec["margin_start"] + frac * (rec["points_for"] - rec["points_against"])
    return out


def closing_players(
    stint_rows: pd.DataFrame, from_minute: int = CLOSING_FROM_MINUTE, close_margin: int = CLOSE_MARGIN
) -> Tuple[pd.DataFrame, int]:
    """Quién está en pista en el final de los partidos apretados.

    Returns:
        (`player_id, player_name, share` ordenado, nº de partidos apretados).
        `share` = proporción de los minutos finales de esos partidos que el
        jugador pasó en pista.
    """
    columns = ["player_id", "player_name", "share"]
    if stint_rows.empty:
        return pd.DataFrame(columns=columns), 0
    from_seconds = from_minute * 60.0
    margins = _margin_at(_unique_stints(stint_rows), from_seconds)
    close_games = [g for g, m in margins.items() if abs(m) <= close_margin]
    if not close_games:
        return pd.DataFrame(columns=columns), 0
    rows = stint_rows[stint_rows["game_id"].isin(close_games)].copy()
    rows["on"] = (
        rows["end_seconds"].clip(upper=REGULATION_SECONDS) - rows["start_seconds"].clip(lower=from_seconds)
    ).clip(lower=0.0)
    total = (REGULATION_SECONDS - from_seconds) * len(close_games)
    agg = rows.groupby(["player_id", "player_name"], as_index=False)["on"].sum()
    agg["share"] = agg["on"] / total
    agg = agg[agg["share"] > 0].sort_values("share", ascending=False).reset_index(drop=True)
    return agg[columns], len(close_games)


def block_performance(stint_rows: pd.DataFrame, block_minutes: int = BLOCK_MINUTES) -> pd.DataFrame:
    """Diferencia del equipo por 40 minutos en cada bloque de minutos del partido.

    Returns:
        `block` ("1-4", "5-8"...), `start_minute`, `minutes` (del equipo,
        sumando partidos), `plus_minus`, `per_40`.
    """
    columns = ["block", "start_minute", "minutes", "plus_minus", "per_40"]
    if stint_rows.empty:
        return pd.DataFrame(columns=columns)
    n_blocks = math.ceil(MINUTES / block_minutes)
    seconds = [0.0] * n_blocks
    points = [0.0] * n_blocks
    for rec in _unique_stints(stint_rows).to_dict("records"):
        start, end = rec["start_seconds"], min(rec["end_seconds"], REGULATION_SECONDS)
        duration = rec["end_seconds"] - rec["start_seconds"]
        if duration <= 0:
            continue
        pm = rec["points_for"] - rec["points_against"]
        for b in range(n_blocks):
            lo, hi = b * block_minutes * 60.0, min((b + 1) * block_minutes * 60.0, REGULATION_SECONDS)
            overlap = min(end, hi) - max(start, lo)
            if overlap > 0:
                seconds[b] += overlap
                points[b] += pm * overlap / duration
    rows = []
    for b in range(n_blocks):
        first = b * block_minutes + 1
        last = min((b + 1) * block_minutes, MINUTES)
        minutes = seconds[b] / 60.0
        rows.append({
            "block": f"{first}-{last}",
            "start_minute": first,
            "minutes": minutes,
            "plus_minus": points[b],
            "per_40": 40.0 * points[b] / minutes if minutes > 0 else float("nan"),
        })
    return pd.DataFrame(rows, columns=columns)


def _windows_text(windows: Sequence[Tuple[int, int]]) -> str:
    return " y ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in windows)


def rotation_insights(
    team_name: str,
    rotation: pd.DataFrame,
    starters: pd.DataFrame,
    closers: pd.DataFrame,
    close_games: int,
    blocks: pd.DataFrame,
    on_off: Optional[pd.DataFrame] = None,
    *,
    n_games: int,
    key_players: int = 3,
) -> List[str]:
    """Frases cortas y accionables para el cuerpo técnico, sin LLM.

    Args:
        on_off: `queries_assistant.player_on_off` del mismo equipo y temporada
            (opcional): si está, cada ventana de descanso va acompañada de
            cómo le va al equipo sin ese jugador.
        key_players: cuántos de los jugadores con más minutos se describen.
    """
    lines: List[str] = []
    if not starters.empty:
        top = starters.iloc[0]
        lines.append(
            f"Quinteto inicial más probable: {', '.join(top['players'])} "
            f"(de salida en {int(top['games'])} de {n_games} partidos)."
        )

    onoff_by_id = on_off.set_index("player_id") if on_off is not None and not on_off.empty else None
    for rec in rotation.head(key_players).to_dict("records"):
        windows = list(rec["rest_windows"])
        if not windows:
            lines.append(
                f"{rec['player_name']} ({rec['minutes_per_game']:.0f} min/partido) no tiene un descanso fijo: "
                "no hay un tramo en que se siente habitualmente."
            )
            continue
        text = (
            f"{rec['player_name']} ({rec['minutes_per_game']:.0f} min/partido) suele descansar en los "
            f"minutos {_windows_text(windows)}."
        )
        if onoff_by_id is not None and rec["player_id"] in onoff_by_id.index:
            row = onoff_by_id.loc[rec["player_id"]]
            if bool(row.get("reliable", False)) and pd.notna(row["off_per_40"]) and pd.notna(row["on_per_40"]):
                text += (
                    f" Sin él, {team_name} hace {row['off_per_40']:+.1f} por 40 min "
                    f"(con él, {row['on_per_40']:+.1f})."
                )
        lines.append(text)

    if not closers.empty and close_games > 0:
        names = closers.head(5)["player_name"].tolist()
        lines.append(
            f"En los {close_games} finales apretados (±{CLOSE_MARGIN} a falta de {MINUTES - CLOSING_FROM_MINUTE} min), "
            f"los que más cierran: {', '.join(names)}."
        )

    solid = blocks[blocks["minutes"] >= MIN_BLOCK_MINUTES].dropna(subset=["per_40"]) if not blocks.empty else blocks
    if len(solid) >= 3:
        worst = solid.loc[solid["per_40"].idxmin()]
        best = solid.loc[solid["per_40"].idxmax()]
        if worst["per_40"] < 0:
            lines.append(
                f"Su peor tramo del partido: minutos {worst['block']} ({worst['per_40']:+.1f} por 40). "
                "Es la ventana donde apretar."
            )
        lines.append(f"Su mejor tramo: minutos {best['block']} ({best['per_40']:+.1f} por 40).")
    return lines
