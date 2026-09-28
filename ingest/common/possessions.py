"""Posesiones por tramo (`lineup_stints.possessions_for`/`possessions_against`).

Hueco "Posesiones por tramo" de `doc/features/propuestas/00_indice.md`: hasta
aquí `lineup_stints` guardaba puntos y no posesiones, así que On/Off, duplas y
RAPM solo podían ir en diferencia por 40 minutos (§5 de la propuesta 07). Este
módulo estima las posesiones de CADA equipo dentro de CADA tramo a partir de
los eventos tipados que ya están en `play_events`, sin llamada HTTP nueva.

FÓRMULA
-------
La estándar de boxscore (Dean Oliver), la MISMA que usan los adaptadores para
`game_advanced_stats` cuando no hay dato oficial (`ingest/acb/adapter.py::
_estimate_possessions`, `ingest/euroleague/adapter.py::_estimate_possessions`)::

    posesiones ≈ FGA + 0.44·FTA − OREB + TOV

aplicada a la ventana del tramo en vez de al partido entero. Tipos de
`play_events` que cuenta (contrato con la ingesta de tiros, ver
`SHOT_EVENT_TYPES`):

- FGA = `fg2_made` + `fg2_missed` + `fg3_made` + `fg3_missed`
- FTA = `ft_made` + `ft_missed`
- OREB = `oreb`, TOV = `turnover`

`possessions_for` son las del equipo del tramo y `possessions_against` las del
rival en la MISMA ventana de tiempo (sus eventos, no los del quinteto).

Tiros libres: factor 0,44 o viajes contados (`ft_mode`)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
El 0,44 es un promedio de liga: aproxima cuántos tiros libres cierran una
posesión (los dos de una falta en tiro sí; el de un 2+1, el técnico o el
primero de una serie, no). Con play-by-play se puede contar el viaje de
verdad, y `ft_mode="trips"` lo hace: los libres del mismo equipo en el mismo
segundo de partido forman un viaje (el reloj está parado mientras se lanzan);
un viaje de 2 o 3 libres cierra UNA posesión, un viaje de un único libre (2+1
o técnico) no cierra ninguna — el 2+1 ya la cerró con la canasta y el técnico
no cambia la posesión. La fuente no distingue técnicos de faltas normales, así
que un técnico de 2 libres (antiguo) contaría como posesión; es marginal.

Por defecto se usa `"factor"` (0,44) a propósito: es la misma fórmula que da
la referencia del partido en Euroliga y en el ACB sin avanzadas oficiales, así
que la comparación por partido (abajo) es de manzanas con manzanas, y con el
reescalado activo el modo apenas cambia el resultado final.

Posesiones de fin de periodo
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
La fórmula cuenta posesiones por el evento que las TERMINA (tiro, pérdida,
libres). Una posesión que muere porque se acaba el cuarto sin tirar no tiene
evento y no se cuenta: como mucho una por equipo y periodo (≈ −4/−5 por
partido). No se inventan eventos para taparlo; el reescalado por partido (más
abajo) lo corrige en el total cuando hay referencia.

FRONTERAS (intervalos semiabiertos)
-----------------------------------
Un tramo cubre `[start_seconds, end_seconds)`: un evento en el segundo exacto
de un cambio va al quinteto que ENTRA, que es el que está en pista cuando se
reanuda el juego (los libres que se lanzan tras un cambio en el mismo segundo
son el caso típico). Dos excepciones, por orden:

1. Un evento con reloj `00:00` (final de periodo: la canasta sobre la bocina,
   los libres de una falta con 0:00) se asigna con `(start, end]`, al quinteto
   que acabó el periodo — el cambio del descanso comparte segundo con esa
   bocina y el semiabierto normal se lo daría al quinteto del cuarto
   siguiente, que no había pisado la pista.
2. Si la regla que toca no encuentra tramo (hueco sin quinteto de cinco, o el
   último segundo del partido), se prueba la otra. Si tampoco, el evento
   queda sin tramo: cuenta en el total del partido, no en ningún tramo.

Ojo con la asimetría inevitable: `ingest/common/lineups.py` reparte los
PUNTOS de un segundo compartido por el orden de la fuente (canasta antes o
después del cambio), y aquí las posesiones se reparten por el reloj. En un
segundo con canasta y cambio a la vez, puntos y posesiones pueden caer en
tramos contiguos distintos; el error es de una posesión entre dos tramos
seguidos y se compensa al agregar (mismo matiz que §5 de la propuesta 12).

TOTAL POR PARTIDO Y REESCALADO
------------------------------
Además del reparto por tramo se calcula el total del partido por equipo con
TODOS sus eventos (incluidos los que no caen en ningún tramo) y se compara con
la referencia de `game_advanced_stats`: `100 · puntos / ortg` del equipo. En
ACB, con `AdvancedStats/match-advanced-stats` disponible, `ortg` es el OFICIAL
de la liga (y por tanto sus posesiones oficiales); en Euroliga y en ACB sin
avanzadas, es la estimación Dean Oliver sobre el boxscore. La discrepancia se
registra en el log (aviso por encima de `DISCREPANCY_WARN_PCT`).

`rescale=True` (por defecto) multiplica las posesiones de los tramos de cada
equipo por `referencia / estimado_del_partido`, de modo que si los tramos
cubren el partido entero suman exactamente la referencia, y un net rating por
100 agregado de todos los tramos de un partido coincide con el `ortg − drtg`
que ya enseña la interfaz. Se reescala contra el estimado del PARTIDO (no la
suma de tramos) para no inflar los tramos cuando hay huecos sin quinteto. Si
no hay referencia, o el factor sale fuera de `RESCALE_BOUNDS` (señal de
play-by-play incompleto o de referencia rota, no de un sesgo de fórmula), no
se reescala y se deja la estimación cruda, con aviso.

NULL, NO 0
----------
Si el partido no tiene NINGÚN evento de tiro tipado (ingestas anteriores a que
existieran, ver `SHOT_EVENT_TYPES`), las dos columnas quedan en NULL en todos
sus tramos: sin tiros la fórmula daría solo `TOV − OREB`, un número falso. Así
`app/assistant/capabilities.py` distingue "no calculado" de "cero posesiones".
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

#: Tiros de campo (contrato con la ingesta de tiros en `play_events`).
FGA_EVENT_TYPES = frozenset({"fg2_made", "fg2_missed", "fg3_made", "fg3_missed"})
#: Tiros libres (mismo contrato).
FTA_EVENT_TYPES = frozenset({"ft_made", "ft_missed"})
#: Cualquiera de estos en un partido = hay tiros tipados y se puede estimar.
SHOT_EVENT_TYPES = FGA_EVENT_TYPES | FTA_EVENT_TYPES
OREB_EVENT_TYPE = "oreb"
TOV_EVENT_TYPE = "turnover"

#: Peso de un tiro libre en la fórmula estándar (ver docstring del módulo).
FT_FACTOR = 0.44
FT_MODES = ("factor", "trips")

#: Factor de reescalado admisible (referencia / estimado). Fuera de aquí la
#: diferencia no es un sesgo de fórmula sino un dato roto: no se reescala.
RESCALE_BOUNDS = (0.75, 1.33)
#: Diferencia relativa (%) entre estimado y referencia a partir de la cual se avisa.
DISCREPANCY_WARN_PCT = 10.0

_END_OF_PERIOD_CLOCKS = {"00:00", "0:00"}


@dataclass
class TeamCounts:
    """Contadores de la fórmula para un equipo en una ventana."""

    fga: float = 0.0
    fta: float = 0.0
    ft_trips: float = 0.0
    oreb: float = 0.0
    tov: float = 0.0

    def possessions(self, ft_mode: str = "factor") -> float:
        ft_term = FT_FACTOR * self.fta if ft_mode == "factor" else self.ft_trips
        return self.fga + ft_term - self.oreb + self.tov


@dataclass
class GamePossessions:
    """Resultado de un partido: totales, referencia y reparto por tramo.

    `stints` es `{stint_id: (possessions_for, possessions_against)}`, vacío si
    `has_shot_events` es False (el llamante escribe NULL).
    """

    game_id: str
    has_shot_events: bool
    estimated: Dict[str, float] = field(default_factory=dict)
    reference: Dict[str, Optional[float]] = field(default_factory=dict)
    scale: Dict[str, float] = field(default_factory=dict)
    stints: Dict[int, Tuple[float, float]] = field(default_factory=dict)

    def discrepancy_pct(self, team_id: str) -> Optional[float]:
        """(estimado − referencia) / referencia, en %; None sin referencia."""
        ref = self.reference.get(team_id)
        if not ref:
            return None
        return 100.0 * (self.estimated.get(team_id, 0.0) - ref) / ref


def _is_end_of_period(game_clock: Optional[str]) -> bool:
    return (game_clock or "").strip() in _END_OF_PERIOD_CLOCKS


def _find_stint(stints: List[tuple], seconds: float, closed_right: bool) -> Optional[int]:
    """`stints` = `[(stint_id, start, end)]` de un equipo. Ver FRONTERAS en el docstring."""
    for stint_id, start, end in stints:
        if closed_right:
            if start < seconds <= end:
                return stint_id
        elif start <= seconds < end:
            return stint_id
    return None


def assign_stint(stints: List[tuple], seconds: float, game_clock: Optional[str]) -> Optional[int]:
    """Tramo (de un equipo) al que pertenece un evento, o None. Regla completa en el docstring."""
    closed_right = _is_end_of_period(game_clock)
    found = _find_stint(stints, seconds, closed_right)
    if found is None:
        found = _find_stint(stints, seconds, not closed_right)
    return found


def _event_weights(events: List[dict], ft_mode: str) -> List[Tuple[dict, str, float]]:
    """`(evento, contador, peso)` para cada evento que entra en la fórmula.

    En `ft_mode="trips"` cada libre pesa `1/n` de su viaje (mismo equipo y
    segundo) si el viaje tiene 2+ libres, y 0 si es un libre suelto: el viaje
    entero suma una posesión, y como todos sus libres comparten segundo caen
    en el mismo tramo.
    """
    trip_sizes: Dict[tuple, int] = {}
    if ft_mode == "trips":
        for event in events:
            if event["event_type"] in FTA_EVENT_TYPES:
                key = (event["team_id"], float(event["seconds"]))
                trip_sizes[key] = trip_sizes.get(key, 0) + 1

    weighted = []
    for event in events:
        etype = event["event_type"]
        if etype in FGA_EVENT_TYPES:
            weighted.append((event, "fga", 1.0))
        elif etype in FTA_EVENT_TYPES:
            weighted.append((event, "fta", 1.0))
            if ft_mode == "trips":
                size = trip_sizes[(event["team_id"], float(event["seconds"]))]
                weighted.append((event, "ft_trips", 1.0 / size if size >= 2 else 0.0))
        elif etype == OREB_EVENT_TYPE:
            weighted.append((event, "oreb", 1.0))
        elif etype == TOV_EVENT_TYPE:
            weighted.append((event, "tov", 1.0))
    return weighted


def compute_game_possessions(
    game_id: str,
    team_ids: Iterable[str],
    events: List[dict],
    stints: List[dict],
    reference: Optional[Dict[str, Optional[float]]] = None,
    rescale: bool = True,
    ft_mode: str = "factor",
) -> GamePossessions:
    """Cálculo puro (sin BD) de las posesiones de un partido.

    Args:
        team_ids: los dos equipos del partido.
        events: dicts con `team_id, seconds, game_clock, event_type` (filas de `play_events`).
        stints: dicts con `id, team_id, start_seconds, end_seconds` (filas de `lineup_stints`).
        reference: `{team_id: posesiones de referencia del partido}` (ver `reference_possessions`).
        rescale: reescalar al total de referencia (ver docstring del módulo).
        ft_mode: `"factor"` (0,44·FTA) o `"trips"` (viajes a la línea contados).
    """
    if ft_mode not in FT_MODES:
        raise ValueError(f"ft_mode desconocido: {ft_mode!r} (usa uno de {FT_MODES})")
    team_ids = list(team_ids)
    reference = reference or {}
    result = GamePossessions(
        game_id=game_id,
        has_shot_events=any(e["event_type"] in SHOT_EVENT_TYPES for e in events),
        reference={t: reference.get(t) for t in team_ids},
    )
    if not result.has_shot_events:
        return result

    by_team: Dict[str, List[tuple]] = {t: [] for t in team_ids}
    for stint in sorted(stints, key=lambda s: (s["start_seconds"], s["id"])):
        by_team.setdefault(stint["team_id"], []).append(
            (stint["id"], float(stint["start_seconds"]), float(stint["end_seconds"]))
        )

    game_counts: Dict[str, TeamCounts] = {t: TeamCounts() for t in team_ids}
    # (stint_id, equipo_que_ataca) -> contadores: cada evento del equipo X se
    # suma al tramo de X que lo contiene (sus posesiones a favor) Y al tramo
    # del rival que lo contiene (sus posesiones en contra).
    window_counts: Dict[tuple, TeamCounts] = {}
    for event, counter, weight in _event_weights(events, ft_mode):
        team = event["team_id"]
        if team not in game_counts:
            continue  # evento de un equipo ajeno al partido: dato roto, no se inventa a quién va
        setattr(game_counts[team], counter, getattr(game_counts[team], counter) + weight)
        seconds = float(event["seconds"])
        for stint_team, team_stints in by_team.items():
            stint_id = assign_stint(team_stints, seconds, event.get("game_clock"))
            if stint_id is None:
                continue
            counts = window_counts.setdefault((stint_id, team), TeamCounts())
            setattr(counts, counter, getattr(counts, counter) + weight)

    for team in team_ids:
        estimated = game_counts[team].possessions(ft_mode)
        result.estimated[team] = estimated
        ref = result.reference.get(team)
        scale = 1.0
        if ref and estimated > 0:
            ratio = ref / estimated
            diff = result.discrepancy_pct(team)
            log = logger.warning if abs(diff) > DISCREPANCY_WARN_PCT else logger.info
            log(
                "posesiones %s/%s: estimadas %.1f vs referencia %.1f (%+.1f%%)",
                game_id, team, estimated, ref, diff,
            )
            if rescale:
                if RESCALE_BOUNDS[0] <= ratio <= RESCALE_BOUNDS[1]:
                    scale = ratio
                else:
                    logger.warning(
                        "posesiones %s/%s: factor %.2f fuera de %s, no se reescala",
                        game_id, team, ratio, RESCALE_BOUNDS,
                    )
        else:
            logger.info("posesiones %s/%s: estimadas %.1f, sin referencia", game_id, team, estimated)
        result.scale[team] = scale

    for team, team_stints in by_team.items():
        opponents = [t for t in team_ids if t != team]
        for stint_id, _, _ in team_stints:
            own = window_counts.get((stint_id, team), TeamCounts()).possessions(ft_mode)
            own *= result.scale.get(team, 1.0)
            against = 0.0
            for opp in opponents:
                raw = window_counts.get((stint_id, opp), TeamCounts()).possessions(ft_mode)
                against += raw * result.scale.get(opp, 1.0)
            # Una ventana corta puede dar negativo (un rebote ofensivo sin el
            # tiro que lo abrió dentro): no existen posesiones negativas.
            result.stints[stint_id] = (round(max(own, 0.0), 2), round(max(against, 0.0), 2))
    return result


def reference_possessions(conn: Connection, game_id: str) -> Dict[str, Optional[float]]:
    """Posesiones de referencia por equipo: `100 · puntos / ortg` de `game_advanced_stats`.

    Oficial en ACB con avanzadas de la liga, Dean Oliver en el resto (ver
    docstring del módulo). `None` para un equipo sin fila, sin `ortg` o con
    `ortg` 0 — no se usa `games.pace` como sustituto porque en ACB oficial es
    un ritmo por 40 minutos (no cuadra con partidos con prórroga).
    """
    game = conn.execute(
        text("SELECT home_team_id, away_team_id, home_score, away_score FROM games WHERE id = :g"),
        {"g": game_id},
    ).first()
    if game is None:
        return {}
    points = {game[0]: game[2], game[1]: game[3]}
    ortg = dict(
        conn.execute(
            text("SELECT team_id, ortg FROM game_advanced_stats WHERE game_id = :g"), {"g": game_id}
        ).all()
    )
    reference: Dict[str, Optional[float]] = {}
    for team, pts in points.items():
        rating = ortg.get(team)
        reference[team] = 100.0 * pts / rating if rating and pts is not None else None
    return reference


def update_stint_possessions(
    conn: Connection,
    game_id: str,
    rescale: bool = True,
    ft_mode: str = "factor",
    dry_run: bool = False,
) -> GamePossessions:
    """Recalcula y escribe las posesiones de todos los tramos de un partido, desde la BD.

    Lee `play_events` y `lineup_stints` YA guardados (no necesita red ni el
    `NormalizedGame`): lo usan igual el loader tras cargar un partido y
    `tools/backfill_stint_possessions.py` sobre una BD ya ingerida.
    Idempotente: sobrescribe siempre las dos columnas, con NULL si el partido
    no tiene tiros tipados.
    """
    game = conn.execute(
        text("SELECT home_team_id, away_team_id FROM games WHERE id = :g"), {"g": game_id}
    ).first()
    team_ids = [game[0], game[1]] if game else []
    events = [
        dict(row._mapping)
        for row in conn.execute(
            text("SELECT team_id, seconds, game_clock, event_type FROM play_events WHERE game_id = :g"),
            {"g": game_id},
        )
    ]
    stints = [
        dict(row._mapping)
        for row in conn.execute(
            text("SELECT id, team_id, start_seconds, end_seconds FROM lineup_stints WHERE game_id = :g"),
            {"g": game_id},
        )
    ]
    result = compute_game_possessions(
        game_id, team_ids, events, stints,
        reference=reference_possessions(conn, game_id), rescale=rescale, ft_mode=ft_mode,
    )
    if dry_run:
        return result
    if not result.has_shot_events:
        conn.execute(
            text("UPDATE lineup_stints SET possessions_for = NULL, possessions_against = NULL WHERE game_id = :g"),
            {"g": game_id},
        )
        return result
    for stint_id, (poss_for, poss_against) in result.stints.items():
        conn.execute(
            text("UPDATE lineup_stints SET possessions_for = :pf, possessions_against = :pa WHERE id = :id"),
            {"pf": poss_for, "pa": poss_against, "id": stint_id},
        )
    return result
