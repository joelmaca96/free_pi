"""Reconstruye los quintetos (lineups) de un partido a partir de su play-by-play.

Equivalente al par `03_variables.R` (qué 5 jugadores están en pista en cada
jugada) + `06_lineup_analysis.R` (minutos/plus-minus por combinación) de
OpenACB, pero fuente-agnóstico: opera sobre un contrato común de eventos
(`PlayByPlayEvent`) que arma cada adapter de fuente a partir de su propio
formato de play-by-play (ver `ingest/acb/adapter.py` /
`ingest/euroleague/adapter.py`).

Algoritmo:
1. Se parte del quinteto titular de cada equipo.
2. Los eventos de sustitución cierran el "tramo" (stint) del quinteto
   saliente (se suman sus segundos en pista al total de esa combinación
   exacta de 5 jugadores) y abren uno nuevo.
3. Los eventos de canasta suman/restan puntos al quinteto que esté en pista
   en ese instante para cada equipo (plus-minus), independientemente de si
   coincide con un cambio de tramo.
4. Al final del partido se cierra el último tramo de cada equipo.

Los quintetos con menos/más de 5 jugadores en pista (datos incompletos: p.ej.
al inicio si aún no se ha visto el quinteto titular completo) se ignoran,
tanto para minutos como para plus-minus, en vez de guardar un "quinteto" que
no es tal.
"""
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from .schema_types import LineupRecord


@dataclass
class PlayByPlayEvent:
    team_id: str
    type: str  # "sub_in" | "sub_out" | "score"
    seconds: float
    player_id: Optional[str] = None
    points: Optional[int] = None


def reconstruct_lineups(
    home_team_id: str,
    away_team_id: str,
    home_starters: List[str],
    away_starters: List[str],
    events: List[PlayByPlayEvent],
    game_end_seconds: Optional[float] = None,
) -> Dict[str, List[LineupRecord]]:
    """Reconstruye los quintetos de ambos equipos a partir de su play-by-play.

    Returns:
        `{team_id: [LineupRecord, ...]}`, agregado por combinación exacta de
        5 jugadores (una fila por combinación distinta usada en el partido).
    """
    on_court = {home_team_id: set(home_starters), away_team_id: set(away_starters)}
    stint_start = {home_team_id: 0.0, away_team_id: 0.0}
    # (team_id, frozenset(5 jugadores)) -> {"seconds": float, "plus_minus": int}
    agg: Dict[tuple, Dict[str, float]] = defaultdict(lambda: {"seconds": 0.0, "plus_minus": 0})

    def close_stint(team_id: str, end_seconds: float) -> None:
        players = on_court[team_id]
        duration = end_seconds - stint_start[team_id]
        if len(players) == 5 and duration > 0:
            key = (team_id, frozenset(players))
            agg[key]["seconds"] += duration
        stint_start[team_id] = end_seconds

    ordered_events = sorted(events, key=lambda e: e.seconds)
    for event in ordered_events:
        if event.type == "score":
            scoring_team = event.team_id
            for team_id in (home_team_id, away_team_id):
                players = on_court[team_id]
                if len(players) != 5:
                    continue
                sign = 1 if team_id == scoring_team else -1
                key = (team_id, frozenset(players))
                agg[key]["plus_minus"] += sign * (event.points or 0)
        elif event.type == "sub_out":
            close_stint(event.team_id, event.seconds)
            on_court[event.team_id].discard(event.player_id)
        elif event.type == "sub_in":
            close_stint(event.team_id, event.seconds)
            on_court[event.team_id].add(event.player_id)
        else:
            raise ValueError(f"tipo de evento no soportado: {event.type!r}")

    end_seconds = game_end_seconds
    if end_seconds is None:
        end_seconds = ordered_events[-1].seconds if ordered_events else 0.0
    close_stint(home_team_id, end_seconds)
    close_stint(away_team_id, end_seconds)

    result: Dict[str, List[LineupRecord]] = {home_team_id: [], away_team_id: []}
    for (team_id, players), totals in agg.items():
        if totals["seconds"] <= 0:
            continue
        result[team_id].append(
            LineupRecord(
                player_ids=sorted(players),
                minutes=round(totals["seconds"] / 60.0, 1),
                plus_minus=int(totals["plus_minus"]),
            )
        )
    return result
