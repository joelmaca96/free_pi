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

TRAMOS (2026-08-24). Además de la agregación por combinación, se devuelven
ahora los tramos SIN agregar: cada uno con su instante de apertura y cierre,
sus puntos a favor y en contra, y el marcador con el que entró a pista.

El dato ya se calculaba aquí dentro —`close_stint` siempre supo dónde empezaba
y terminaba cada tramo— y se tiraba a la basura al agregar. Sin él no se puede
contestar "¿cuál es el mejor quinteto para los últimos minutos?", que es la
tercera pregunta del encargo del asistente: los quintetos agregados por
partido no se pueden recortar por tiempo, y "clutch" no es solo tiempo, es
tiempo *con el partido apretado* (ver `local/features/005-chatbot/01_design.md`
§2.3 y §10.2, y la tabla `lineup_stints` en `schema.sql`).
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .schema_types import LineupRecord, StintRecord


@dataclass
class PlayByPlayEvent:
    team_id: str
    type: str  # "sub_in" | "sub_out" | "score"
    seconds: float
    player_id: Optional[str] = None
    points: Optional[int] = None


@dataclass
class LineupReconstruction:
    """Lo que sale de un play-by-play: quintetos agregados y tramos sin agregar.

    Las dos vistas del mismo recorrido de eventos. `by_team` es lo que
    siempre se ha guardado en `lineups` (una fila por combinación de cinco,
    con el total del partido); `stints` es el detalle con reloj y marcador,
    que es lo único con lo que se puede recortar una ventana de tiempo.
    """

    by_team: Dict[str, List[LineupRecord]] = field(default_factory=dict)
    stints: List[StintRecord] = field(default_factory=list)


def reconstruct_lineups(
    home_team_id: str,
    away_team_id: str,
    home_starters: List[str],
    away_starters: List[str],
    events: List[PlayByPlayEvent],
    game_end_seconds: Optional[float] = None,
) -> LineupReconstruction:
    """Reconstruye los quintetos y los tramos de ambos equipos desde su play-by-play.

    Returns:
        `LineupReconstruction`: `by_team` con una fila por combinación exacta
        de 5 jugadores y equipo, y `stints` con un registro por tramo continuo
        en pista (los que llegan a 5 jugadores y duran algo).
    """
    on_court = {home_team_id: set(home_starters), away_team_id: set(away_starters)}
    stint_start = {home_team_id: 0.0, away_team_id: 0.0}
    # Marcador acumulado por equipo. Se lleva en el MISMO recorrido de eventos
    # que ya llevaba el plus/minus: no hace falta una segunda pasada.
    score = {home_team_id: 0, away_team_id: 0}
    # Puntos del tramo abierto y marcador con el que se abrió.
    stint_points = {home_team_id: [0, 0], away_team_id: [0, 0]}
    stint_margin = {home_team_id: 0, away_team_id: 0}
    # (team_id, frozenset(5 jugadores)) -> {"seconds": float, "plus_minus": int}
    agg: Dict[tuple, Dict[str, float]] = defaultdict(lambda: {"seconds": 0.0, "plus_minus": 0})
    stints: List[StintRecord] = []

    def close_stint(team_id: str, end_seconds: float) -> None:
        players = on_court[team_id]
        duration = end_seconds - stint_start[team_id]
        if len(players) == 5 and duration > 0:
            key = (team_id, frozenset(players))
            agg[key]["seconds"] += duration
            points_for, points_against = stint_points[team_id]
            stints.append(
                StintRecord(
                    team_id=team_id,
                    player_ids=sorted(players),
                    start_seconds=stint_start[team_id],
                    end_seconds=end_seconds,
                    points_for=points_for,
                    points_against=points_against,
                    margin_start=stint_margin[team_id],
                )
            )
        stint_start[team_id] = end_seconds
        stint_points[team_id] = [0, 0]
        # El tramo que se abre lo hace con el marcador de AHORA: es lo que
        # convierte "últimos minutos" en "clutch".
        opponent = away_team_id if team_id == home_team_id else home_team_id
        stint_margin[team_id] = score[team_id] - score[opponent]

    ordered_events = sorted(events, key=lambda e: e.seconds)
    for event in ordered_events:
        if event.type == "score":
            scoring_team = event.team_id
            points = event.points or 0
            score[scoring_team] = score.get(scoring_team, 0) + points
            for team_id in (home_team_id, away_team_id):
                players = on_court[team_id]
                if len(players) != 5:
                    continue
                sign = 1 if team_id == scoring_team else -1
                key = (team_id, frozenset(players))
                agg[key]["plus_minus"] += sign * points
                stint_points[team_id][0 if sign > 0 else 1] += points
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

    by_team: Dict[str, List[LineupRecord]] = {home_team_id: [], away_team_id: []}
    for (team_id, players), totals in agg.items():
        if totals["seconds"] <= 0:
            continue
        by_team[team_id].append(
            LineupRecord(
                player_ids=sorted(players),
                minutes=round(totals["seconds"] / 60.0, 1),
                plus_minus=int(totals["plus_minus"]),
                team_id=team_id,
            )
        )
    return LineupReconstruction(by_team=by_team, stints=stints)
