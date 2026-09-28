"""Contexto de cada tiro de campo derivado del play-by-play tipado, agnóstico de fuente.

Tres banderas por tiro (`shots.is_fastbreak`/`is_second_chance`/
`is_off_turnover`), con el MISMO significado para las dos fuentes: el
contexto de la POSESIÓN en la que se tira, anotado o fallado.

- **Segunda oportunidad**: la posesión del tirador incluye ya un rebote
  ofensivo de su equipo.
- **Tras pérdida**: la posesión empezó con una pérdida del rival (con o sin
  robo registrado).
- **Contraataque**: el tiro llega como mucho `FASTBREAK_WINDOW_SECONDS`
  segundos después de que la posesión empezase con un rebote defensivo, un
  robo o una pérdida del rival. Es una aproximación por reloj: la fuente no
  dice si el balón cruzó en ventaja numérica, solo cuándo pasó cada cosa.

Euroliga publica sus propias banderas en `ShotData` (`FASTBREAK`/
`SECOND_CHANCE`/`POINTS_OFF_TURNOVER`), pero son de PUNTOS: verificado en vivo
(2026-09-28, 16 partidos de la temporada 2025, 2.462 filas) que solo aparecen
a 1 en tiros ANOTADOS — un fallo lleva siempre 0, que ahí significa "no dio
puntos", no "no era contraataque". Por eso el adapter de Euroliga solo pasa
las banderas de la fuente para los tiros anotados y deja las de los fallados
en `None`; `fill_missing_shot_context` rellena ÚNICAMENTE lo que llega en
`None`, así que la bandera de la fuente, cuando existe, siempre gana. ACB no
publica ninguna de las tres: todas salen de aquí.

Validación en vivo (2026-09-28): derivando las tres banderas con este módulo
sobre esos 16 partidos de Euroliga, ignorando las de la fuente, y
comparándolas con ellas en las 958 canastas de campo: segunda oportunidad
coincide en el 99,5% (precisión 98%, recall 97%), tras pérdida en el 99,0%
(97%/98%) y contraataque en el 92,5% (55%/81%, ver
`FASTBREAK_WINDOW_SECONDS`: es la única de las tres que en la fuente es un
juicio del anotador y no un hecho del acta).

Entrada: los `play_events` del contrato común (`ingest/common/raw_game.py`),
con ids EXTERNOS, que desde 2026-09-28 incluyen los tiros
(`fg2_made`/`fg2_missed`/`fg3_made`/`fg3_missed`/`ft_made`/`ft_missed`).
Se leen en orden cronológico (ordenación estable por segundo: dentro del
mismo segundo manda el orden de la fuente, que es el del acta).

La máquina de posesiones es deliberadamente simple (una posesión = un equipo
+ cómo y cuándo empezó + si ya hubo rebote ofensivo):

- `dreb`/`steal` del equipo X abren posesión de X; `turnover` de X abre
  posesión del rival (salvo que el robo del rival en ese mismo segundo ya la
  haya abierto — el acta da las dos filas).
- Canasta de campo o tiro libre anotado de X abren posesión del rival
  ("tras canasta", nunca contraataque ni tras pérdida). Un tiro libre de X
  en el MISMO segundo en que X acaba de anotar (el adicional de un 2+1, o el
  segundo libre de una serie) devuelve la posesión a X con el estado que
  tenía: sin esto, un 2+1 fallado y cogido en ataque perdería el "tras
  pérdida" original.
- `oreb` de X marca la posesión en curso de X como segunda oportunidad.
- Un tiro de un equipo que NO tiene la posesión en curso (jugada que el acta
  no registra: rebote de equipo, fuera de banda...) abre una posesión
  "desconocida" para él: sin banderas, que es lo prudente.
- Cada cambio de periodo reinicia el estado.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ingest.common.game_clock import game_clock_to_seconds

#: Segundos máximos entre el inicio de la posesión (rebote defensivo, robo o
#: pérdida rival) y el tiro para llamarlo contraataque. Calibrado en vivo
#: (2026-09-28) contra la bandera `FASTBREAK` de Euroliga en 958 canastas de
#: campo de 16 partidos: 6 s es el umbral con mejor equilibrio (F1 0,65:
#: recoge el 81% de sus contraataques, con un 55% de precisión); 8 s sube el
#: recall al 87% pero duplica los positivos (precisión 44%), 3 s maximiza la
#: coincidencia bruta (94,3%) pero se deja la mitad. `FASTBREAK` es un
#: juicio del anotador, no una regla de reloj, así que ningún umbral lo
#: replica — ver doc/features/ingestor/01_estado.md, sección del 2026-09-28.
FASTBREAK_WINDOW_SECONDS = 6.0

#: Tipos de `play_events` que son tiros de campo, con si entró o no.
FIELD_GOAL_EVENT_TYPES = {
    "fg2_made": True, "fg2_missed": False, "fg3_made": True, "fg3_missed": False,
}
#: Tiros libres, con si entró o no.
FREE_THROW_EVENT_TYPES = {"ft_made": True, "ft_missed": False}

#: Claves de contexto que se rellenan en cada tiro del contrato común.
CONTEXT_KEYS = ("is_fastbreak", "is_second_chance", "is_off_turnover")

_TRANSITION_STARTS = {"dreb", "steal", "turnover"}
_TURNOVER_STARTS = {"steal", "turnover"}


@dataclass
class _Possession:
    team_id: str
    start_seconds: float
    start_type: str  # 'dreb'|'steal'|'turnover'|'made'|'unknown'
    had_oreb: bool = False


def _event_seconds(event: dict) -> Optional[float]:
    try:
        return game_clock_to_seconds(event["quarter"], event["clock"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def derive_shot_context(
    events: Sequence[dict],
    team_ids: Tuple[str, str],
    fastbreak_window: float = FASTBREAK_WINDOW_SECONDS,
) -> List[Optional[Dict[str, bool]]]:
    """Banderas de contexto de cada tiro de campo de `events`.

    Args:
        events: `play_events` del contrato común (`team_id`, `quarter`,
            `clock`, `event_type`), en el orden de la fuente.
        team_ids: los dos equipos del partido (mismo espacio de ids que
            `events[i]["team_id"]`), para saber quién es "el rival".
        fastbreak_window: ver `FASTBREAK_WINDOW_SECONDS`.

    Returns:
        Lista alineada con `events`: para cada tiro de campo, un dict con las
        tres claves de `CONTEXT_KEYS` (bool); `None` para cualquier otro
        evento o para un tiro sin reloj interpretable.
    """
    home, away = team_ids
    rival = {home: away, away: home}
    result: List[Optional[Dict[str, bool]]] = [None] * len(events)

    timed = [(i, _event_seconds(e)) for i, e in enumerate(events)]
    timed = sorted((pair for pair in timed if pair[1] is not None), key=lambda pair: pair[1])

    current: Optional[_Possession] = None
    previous: Optional[_Possession] = None
    period = None

    def open_possession(team_id: str, seconds: float, start_type: str) -> None:
        nonlocal current, previous
        previous = current
        current = _Possession(team_id, seconds, start_type)

    for index, seconds in timed:
        event = events[index]
        team_id = event.get("team_id")
        event_type = event.get("event_type")
        if event.get("quarter") != period:
            period = event.get("quarter")
            current = previous = None
        if team_id not in rival:
            continue

        if event_type in ("dreb", "steal"):
            open_possession(team_id, seconds, event_type)
        elif event_type == "turnover":
            beneficiary = rival[team_id]
            already_open = (
                current is not None and current.team_id == beneficiary
                and current.start_type == "steal" and current.start_seconds == seconds
            )
            if not already_open:
                open_possession(beneficiary, seconds, "turnover")
        elif event_type == "oreb":
            if current is None or current.team_id != team_id:
                open_possession(team_id, seconds, "unknown")
            current.had_oreb = True
        elif event_type in FIELD_GOAL_EVENT_TYPES:
            if current is None or current.team_id != team_id:
                open_possession(team_id, seconds, "unknown")
            elapsed = seconds - current.start_seconds
            result[index] = {
                "is_fastbreak": current.start_type in _TRANSITION_STARTS and elapsed <= fastbreak_window,
                "is_second_chance": current.had_oreb,
                "is_off_turnover": current.start_type in _TURNOVER_STARTS,
            }
            if FIELD_GOAL_EVENT_TYPES[event_type]:
                open_possession(rival[team_id], seconds, "made")
        elif event_type in FREE_THROW_EVENT_TYPES:
            own_score_just_now = (
                current is not None and current.team_id != team_id and current.start_type == "made"
                and current.start_seconds == seconds and previous is not None and previous.team_id == team_id
            )
            if own_score_just_now:
                current, previous = previous, None
            elif current is None or current.team_id != team_id:
                open_possession(team_id, seconds, "unknown")
            if FREE_THROW_EVENT_TYPES[event_type]:
                open_possession(rival[team_id], seconds, "made")
    return result


def _shot_key(team_id, player_id, quarter, clock, made) -> tuple:
    return (str(team_id), str(player_id) if player_id is not None else None, quarter, clock, bool(made))


def fill_missing_shot_context(
    shots: Iterable[dict],
    events: Sequence[dict],
    team_ids: Tuple[str, str],
    fastbreak_window: float = FASTBREAK_WINDOW_SECONDS,
) -> List[dict]:
    """Copia de `shots` con las banderas de contexto que falten, derivadas de `events`.

    Solo toca claves de `CONTEXT_KEYS` que vengan ausentes o en `None`: la
    bandera que ya trae la fuente (Euroliga, tiros anotados) se respeta
    siempre. Cada tiro se casa con su evento de tiro por (equipo, jugador,
    cuarto, reloj, anotado) — verificado en vivo que en ACB los 150 tiros de
    campo de `MatchShots` del partido 104465 casan 1 a 1 con los de
    `PlayByPlay` por esa clave —, consumiendo los eventos en orden para que
    dos tiros idénticos en el mismo segundo (tapón + palmeo fallado) no se
    lleven la misma fila. Un tiro sin `quarter`/`clock`, o sin evento con el
    que casar, se queda como estaba.
    """
    shots = [dict(shot) for shot in shots]
    if not events or not any(
        shot.get(key) is None for shot in shots for key in CONTEXT_KEYS
    ):
        return shots

    contexts = derive_shot_context(events, team_ids, fastbreak_window)
    pending: Dict[tuple, List[Dict[str, bool]]] = {}
    for event, context in zip(events, contexts):
        if context is None:
            continue
        key = _shot_key(
            event.get("team_id"), event.get("player_id"), event.get("quarter"), event.get("clock"),
            FIELD_GOAL_EVENT_TYPES[event["event_type"]],
        )
        pending.setdefault(key, []).append(context)

    for shot in shots:
        if not shot.get("quarter") or not shot.get("clock"):
            continue
        key = _shot_key(shot.get("team_id"), shot.get("player_id"), shot["quarter"], shot["clock"], shot.get("made"))
        queue = pending.get(key)
        if not queue:
            continue
        context = queue.pop(0)
        for flag in CONTEXT_KEYS:
            if shot.get(flag) is None:
                shot[flag] = context[flag]
    return shots
