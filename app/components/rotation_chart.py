"""Timeline de rotaciones con el margen del marcador detrás.

El gráfico que ninguna web de estadística puede construir, porque ninguna
tiene los tramos de quinteto: una fila por jugador con sus barras en pista y,
**de fondo, en el mismo eje de tiempo**, cómo iba el marcador. El entrenador
no tiene que cruzar dos pantallas para ver qué combinación coincide con la
subida o con la bajada — coinciden en el papel.

Tres decisiones de diseño que no son cosméticas:

- **Las barras van en tinta neutra, no en verde Baskonia.** El verde está
  reservado aquí para el margen a favor: una barra verde encima de una mancha
  verde no se lee, y el color tiene que significar UNA cosa por gráfico. El
  equipo se distingue por el título del gráfico, no por el color de la barra
  (el del rival va un tono más claro, `_RIVAL_BAR`).
- **El margen se dibuja como escalera** (`interpolate="step-after"`), no como
  línea. Entre dos eventos el marcador no sube poco a poco: se queda quieto y
  salta. Una línea recta entre dos puntos inventa una progresión que no
  existe — es justo lo que hace `score_progression` en el esquema y por lo
  que no se usa como fuente (ver `queries.game_score_steps`).
- **Los ejes Y son independientes** (`resolve_scale(y="independent")`): uno
  es ordinal (jugadores) y otro cuantitativo (puntos de margen), y no hay
  forma de compartirlos. El precio es que cada capa cuantitativa tiene que
  fijar su dominio EXPLÍCITAMENTE y el mismo, o el cero del margen y la línea
  de cero acabarían a distinta altura. De ahí `_margin_domain`.

Las marcas de parcial (`runs`) son rectángulos de altura completa: el parcial
es una ventana de TIEMPO, no un valor, así que ocupa una franja del eje X
entera y deja ver por debajo qué barras la cruzan.
"""
from typing import List, Optional

import altair as alt
import pandas as pd

_OWN_BAR = "#3f3d38"    # tinta oscura — "este jugador está en pista"
_RIVAL_BAR = "#8f8d86"  # el mismo gris apagado del resto de la interfaz
_POS = "#008300"        # margen a favor (verde Baskonia)
_NEG = "#c0392b"        # margen en contra
_GRID = "#c9c7bf"       # líneas de cuarto, mismo tono tenue que las zonas de `court.py`
# Faltas (propuesta 06, `doc/features/propuestas/06_gestion_de_faltas.md`
# §2c): un ámbar propio, ni el verde de "a favor" ni el rojo de "en contra"
# — una falta no es buena ni mala en sí misma, es un aviso. El bonus usa el
# MISMO color con otro trazo (raya en vez de marca) porque es la misma cosa
# acumulada: la 4.ª marca de ese color en el cuarto.
_FOUL_MARK = "#b5651d"
_BONUS_LINE = "#b5651d"

#: Un cuarto son 10 minutos y una prórroga 5. Se usan solo para pintar las
#: líneas divisorias del eje: el dato ya viene situado en segundos absolutos.
_QUARTER_MIN = 10
_OVERTIME_MIN = 5


def clock_label(seconds: float) -> str:
    """Segundo absoluto -> `'Q3 04:12'`, con el reloj hacia atrás como en el acta.

    Mismo formato que `play_events.game_clock`, para que un tooltip del
    gráfico y la lista de eventos de debajo se puedan leer juntos sin
    traducir mentalmente de un formato al otro.
    """
    seconds = max(float(seconds), 0.0)
    if seconds <= 2400:
        quarter, elapsed = int(seconds // 600) + 1, seconds % 600
        period, length = f"Q{min(quarter, 4)}", 600
        if seconds == 2400:
            period, elapsed = "Q4", 600
    else:
        overtime = int((seconds - 2400) // 300) + 1
        period, elapsed, length = f"OT{overtime}", (seconds - 2400) % 300, 300
        if (seconds - 2400) % 300 == 0:
            period, elapsed = f"OT{overtime - 1}", 300
    remaining = max(length - elapsed, 0)
    return f"{period} {int(remaining // 60):02d}:{int(remaining % 60):02d}"


def _quarter_marks(end_minutes: float) -> List[float]:
    """Minutos en los que cambia el cuarto (10, 20, 30 y las prórrogas)."""
    marks = [m for m in (_QUARTER_MIN, 2 * _QUARTER_MIN, 3 * _QUARTER_MIN) if m < end_minutes]
    minute = 4 * _QUARTER_MIN
    while minute < end_minutes:
        marks.append(minute)
        minute += _OVERTIME_MIN
    return marks


def _player_order(stints: pd.DataFrame) -> List[str]:
    """Jugadores de arriba abajo: primero los que salen antes (el quinteto inicial).

    Es el orden en el que el entrenador lee su propia rotación. A igualdad de
    entrada manda quien más minutos juega, para que los titulares no queden
    mezclados con un jugador que entró un segundo en el primer cuarto.
    """
    played = stints.assign(seconds=stints["end_seconds"] - stints["start_seconds"])
    per_player = played.groupby("player_name").agg(
        first_seconds=("start_seconds", "min"),
        seconds=("seconds", "sum"),
    )
    per_player = per_player.sort_values(["first_seconds", "seconds"], ascending=[True, False])
    return per_player.index.tolist()


def _margin_domain(steps: pd.DataFrame) -> List[float]:
    """Dominio simétrico del eje de margen, para que el 0 quede en el centro.

    Simétrico a propósito: con un dominio ajustado a los datos, un partido
    ganado de 20 pinta el cero pegado abajo y otro perdido de 20 lo pinta
    pegado arriba, y las dos manchas se leen igual aunque cuenten lo
    contrario. Con el cero siempre en el centro, "por encima" y "por debajo"
    significan lo mismo en todos los partidos.
    """
    top = float(steps["margin"].abs().max()) if not steps.empty else 0.0
    return [-max(top, 5.0), max(top, 5.0)]


def _margin_layers(steps: pd.DataFrame, domain: List[float], x_scale: alt.Scale) -> List[alt.Chart]:
    """Escalera del margen: mancha verde por encima de 0, roja por debajo, y la línea de 0."""
    # Formato largo con una fila por signo: así las dos manchas son UNA capa
    # con un `color` categórico, y comparten escala Y sin depender de que
    # `resolve_scale` las trate igual.
    wide = steps.assign(minute=steps["seconds"] / 60.0)
    long = pd.concat(
        [
            wide.assign(valor=wide["margin"].clip(lower=0), signo="A favor"),
            wide.assign(valor=wide["margin"].clip(upper=0), signo="En contra"),
        ],
        ignore_index=True,
    )
    long["cero"] = 0.0

    area = (
        alt.Chart(long)
        .mark_area(interpolate="step-after", opacity=0.22, line=False)
        .encode(
            x=alt.X("minute:Q", title="Minuto de partido", scale=x_scale),
            y=alt.Y("valor:Q", scale=alt.Scale(domain=domain), axis=alt.Axis(title="Margen", orient="right")),
            y2=alt.Y2("cero:Q"),
            color=alt.Color(
                "signo:N",
                scale=alt.Scale(domain=["A favor", "En contra"], range=[_POS, _NEG]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("quarter:N", title="Cuarto"),
                alt.Tooltip("game_clock:N", title="Reloj"),
                alt.Tooltip("score_for:Q", title="A favor"),
                alt.Tooltip("score_against:Q", title="En contra"),
                alt.Tooltip("margin:Q", title="Margen"),
            ],
        )
    )
    zero = (
        alt.Chart(pd.DataFrame({"cero": [0.0]}))
        .mark_rule(color=_GRID, strokeWidth=1)
        .encode(y=alt.Y("cero:Q", scale=alt.Scale(domain=domain), axis=None))
    )
    return [area, zero]


def _run_layer(runs: pd.DataFrame, x_scale: alt.Scale) -> alt.Chart:
    """Franjas de los parciales detectados, de altura completa.

    Sin encoding de Y a propósito: un parcial es una ventana de tiempo, no un
    valor, así que ocupa todo el alto y deja ver qué barras lo cruzan.
    """
    data = runs.assign(
        minute_start=runs["start_seconds"] / 60.0,
        minute_end=runs["end_seconds"] / 60.0,
        minutos=(runs["duration_s"] / 60.0).round(1),
    )
    return (
        alt.Chart(data)
        .mark_rect(opacity=0.13)
        .encode(
            x=alt.X("minute_start:Q", title="Minuto de partido", scale=x_scale),
            x2=alt.X2("minute_end:Q"),
            color=alt.Color(
                "direction:N",
                scale=alt.Scale(domain=["favor", "contra"], range=[_POS, _NEG]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Parcial"),
                alt.Tooltip("minutos:Q", title="Duración (min)"),
                alt.Tooltip("swing:Q", title="Swing"),
            ],
        )
    )


def _stint_layer(stints: pd.DataFrame, order: List[str], bar_color: str, x_scale: alt.Scale) -> alt.Chart:
    """Barras de tramo en pista, una fila por jugador."""
    data = stints.assign(
        minute_start=stints["start_seconds"] / 60.0,
        minute_end=stints["end_seconds"] / 60.0,
        minutos=((stints["end_seconds"] - stints["start_seconds"]) / 60.0).round(1),
        entra=stints["start_seconds"].map(clock_label),
        sale=stints["end_seconds"].map(clock_label),
        plus_minus=stints["points_for"] - stints["points_against"],
    )
    return (
        alt.Chart(data)
        .mark_bar(color=bar_color, height=11, cornerRadius=2)
        .encode(
            x=alt.X("minute_start:Q", title="Minuto de partido", scale=x_scale),
            x2=alt.X2("minute_end:Q"),
            y=alt.Y("player_name:N", sort=order, title=None, axis=alt.Axis(labelLimit=160)),
            tooltip=[
                alt.Tooltip("player_name:N", title="Jugador"),
                alt.Tooltip("entra:N", title="Entra"),
                alt.Tooltip("sale:N", title="Sale"),
                alt.Tooltip("minutos:Q", title="Minutos"),
                alt.Tooltip("plus_minus:Q", title="+/- del tramo"),
            ],
        )
    )


def _foul_marks_layer(fouls: pd.DataFrame, order: List[str], x_scale: alt.Scale) -> alt.Chart:
    """Una marca por falta personal, sobre la fila de su jugador.

    Es el "cuándo" que ningún boxscore da (§1 de la propuesta 06): un
    `mark_tick` corto, no un punto, para que no se confunda con el final de
    una barra de tramo cuando cae justo encima.
    """
    data = fouls.assign(minute=fouls["seconds"] / 60.0, reloj=fouls["quarter"] + " " + fouls["game_clock"])
    return (
        alt.Chart(data)
        .mark_tick(color=_FOUL_MARK, thickness=2, size=16)
        .encode(
            x=alt.X("minute:Q", scale=x_scale),
            y=alt.Y("player_name:N", sort=order, title=None),
            tooltip=[
                alt.Tooltip("player_name:N", title="Jugador"),
                alt.Tooltip("reloj:N", title="Falta"),
            ],
        )
    )


def _bonus_layer(bonus: pd.DataFrame, x_scale: alt.Scale) -> alt.Chart:
    """Raya vertical en el minuto en que el equipo entra en bonus, por cuarto (§4 de la propuesta 06)."""
    data = bonus.assign(minute=bonus["bonus_seconds"] / 60.0)
    return (
        alt.Chart(data)
        .mark_rule(color=_BONUS_LINE, strokeDash=[2, 2], strokeWidth=1.5)
        .encode(
            x=alt.X("minute:Q", scale=x_scale),
            tooltip=[
                alt.Tooltip("quarter:N", title="Cuarto"),
                alt.Tooltip("bonus_clock:N", title="Entra en bonus"),
            ],
        )
    )


def rotation_chart(
    stints: pd.DataFrame,
    steps: pd.DataFrame,
    runs: Optional[pd.DataFrame] = None,
    *,
    is_own_team: bool = True,
    title: Optional[str] = None,
    fouls: Optional[pd.DataFrame] = None,
    bonus: Optional[pd.DataFrame] = None,
) -> alt.LayerChart:
    """Timeline de rotaciones + margen de fondo + franjas de parcial + faltas.

    Args:
        stints: `queries.game_stints` del equipo que se pinta (una fila por
            tramo y jugador). Puede venir vacío: el partido se ingirió antes
            de la fase 3 y entonces solo se pinta el marcador.
        steps: `queries.game_score_steps` orientado a ESE equipo — el signo
            del margen es desde su punto de vista. Puede venir vacío (sin
            play-by-play tipado) y entonces solo se pintan las barras.
        runs: `queries.game_runs`, opcional. Sin él el gráfico sigue siendo
            legible; con él, señala dónde mirar.
        is_own_team: solo cambia el color de las barras (Baskonia en tinta,
            rival en gris) — el resto del gráfico es idéntico, que es lo que
            permite compararlos de un vistazo.
        fouls: `queries_assistant.foul_timeline` ya filtrado a
            `event_type == 'foul_personal'` y a ESTE equipo (propuesta 06,
            §2c). Sin tramos (`stints` vacío) no hay fila donde ponerla, así
            que se ignora en ese caso aunque venga rellena.
        bonus: `queries_assistant.foul_bonus_minutes` ya filtrado a ESTE
            equipo. Opcional — sin él el gráfico sigue siendo legible.
    """
    layers: List[alt.Chart] = []
    end_minutes = 40.0
    if steps is not None and not steps.empty:
        end_minutes = max(end_minutes, float(steps["seconds"].max()) / 60.0)
    if stints is not None and not stints.empty:
        end_minutes = max(end_minutes, float(stints["end_seconds"].max()) / 60.0)

    # El dominio del eje X se fija EN CADA CAPA, no en el `alt.layer(...)`:
    # una encoding de capa padre solo la heredan los hijos que no declaran ese
    # canal, y aquí lo declaran todos. Sin dominio explícito, la capa del
    # margen (que llega al final del partido) y la de barras (que puede acabar
    # antes) se ajustarían cada una a lo suyo y el eje quedaría descuadrado.
    x_scale = alt.Scale(domain=[0, end_minutes], nice=False)

    if steps is not None and not steps.empty:
        layers += _margin_layers(steps, _margin_domain(steps), x_scale)
    if runs is not None and not runs.empty:
        layers.append(_run_layer(runs, x_scale))
    if bonus is not None and not bonus.empty:
        layers.append(_bonus_layer(bonus, x_scale))

    marks = _quarter_marks(end_minutes)
    if marks:
        layers.append(
            alt.Chart(pd.DataFrame({"minute": marks}))
            .mark_rule(color=_GRID, strokeDash=[4, 4])
            .encode(x=alt.X("minute:Q", title="Minuto de partido", scale=x_scale))
        )

    order: List[str] = []
    if stints is not None and not stints.empty:
        order = _player_order(stints)
        layers.append(
            _stint_layer(stints, order, _OWN_BAR if is_own_team else _RIVAL_BAR, x_scale)
        )
        if fouls is not None and not fouls.empty:
            own_fouls = fouls[fouls["player_name"].isin(order)]
            if not own_fouls.empty:
                layers.append(_foul_marks_layer(own_fouls, order, x_scale))

    return (
        alt.layer(*layers)
        .resolve_scale(y="independent", color="independent")
        .properties(height=max(22 * len(order) + 40, 200), title=title or "")
    )


#: Cómo se lee cada `play_events.event_type` en la lista de un parcial. En
#: castellano y en el vocabulario del banquillo, no el del esquema: el
#: entrenador no busca `oreb`, busca "rebote ofensivo".
EVENT_LABELS = {
    "turnover": "Pérdida",
    "steal": "Robo",
    "oreb": "Rebote ofensivo",
    "dreb": "Rebote defensivo",
    "assist": "Asistencia",
    "block": "Tapón",
    "foul_personal": "Falta cometida",
    "foul_drawn": "Falta recibida",
}


def event_label(event_type: str) -> str:
    """Nombre legible de un tipo de evento; el propio código si es uno nuevo."""
    return EVENT_LABELS.get(event_type, event_type)
