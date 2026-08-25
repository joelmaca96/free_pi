"""Mapa de tiros: líneas reales de cancha + tiros superpuestos.

Un único hue (verde, identidad del Baskonia) para "anotado"; los fallados se
dibujan como círculo hueco en vez de un segundo hue — evita necesitar un par
categórico nuevo para algo que ya se distingue por relleno/forma, y mantiene
la paleta a un solo color con dos estados (relleno = anotado).

Un tercer estado, aparte de anotado/fallado: los tiros que la fuente NO
localiza (`shots.located = 0`, hoy los mates de ACB, que llegan sin
coordenadas y se colocan en el aro). Pintarlos como un tiro normal más era
atribuirles una precisión que no tienen, y además se apilan todos en el mismo
punto, así que el mapa mostraba un punto donde hay cientos. Van en su propia
capa: UN rombo por pila, con el recuento en el tooltip y en el pie de gráfico
(`shot_chart_caption`). No se dispersan con ruido — inventar coordenadas que
la fuente no da sería peor que la limitación.

Sin dibujo de cancha el mapa no se lee como una cancha — las líneas
(pintura, tiro libre, triple) se derivan de las filas de `court_zones` en
vez de usar constantes fijas independientes, para que encajen exactamente
con los tiros (la ingesta reescala las coordenadas de cada fuente contra ese
mismo seed, ver `ingest/common/zones.py::to_court_coords`) — si mañana se
reajustan las zonas, las líneas se mueven solas con ellas, y hay que
reajustar con ellas la calibración de esa conversión. Las zonas en sí ya no se pintan como cajas de fondo
(quitado a petición expresa: competían visualmente con las líneas reales de
cancha y no aportaban sobre ellas) — siguen sirviendo solo como fuente de
geometría para las líneas.
"""
import altair as alt
import numpy as np
import pandas as pd

_MADE_COLOR = "#008300"   # verde Baskonia — mismo accent que el resto de la interfaz
_MISS_STROKE = "#898781"  # ink muted
_LINE_COLOR = "#5c5a52"   # líneas de cancha — neutro, sin competir con los tiros
_PILE_STROKE = "#ffffff"  # borde del rombo de "sin ubicación": lo despega de los puntos de debajo
_PILE_GLYPH = "◆"         # el mismo símbolo que nombra el pie de gráfico (`caption`)
_DOMAIN = [0, 500]


def _zone(zones: pd.DataFrame, label: str):
    """Fila de `court_zones` por etiqueta, o `None` si no existe (no se inventa geometría)."""
    row = zones.loc[zones["label"] == label]
    return None if row.empty else row.iloc[0]


def _line_layer(points: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(points)
        .mark_line(color=_LINE_COLOR, strokeWidth=1.6)
        .encode(
            x=alt.X("x:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
            y=alt.Y("y:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
            order="order:Q",
        )
    )


def _court_line_layers(zones: pd.DataFrame) -> list:
    """Pintura, círculo de tiro libre, canasta y línea de triple, derivadas de `court_zones`.

    Se omite cada pieza cuya zona de referencia no exista en `zones` (mismo
    criterio que el resto de la interfaz: un hueco de datos no es un error,
    se omite la pieza en vez de dibujar una cancha inventada).
    """
    layers = []

    paint = _zone(zones, "Pintura")
    if paint is None:
        return layers

    hoop_x = (paint["x_min"] + paint["x_max"]) / 2
    hoop_y = paint["y_max"]  # borde de la pintura más cercano a la línea de fondo

    # ---- Pintura: rectángulo sin relleno ----
    key_pts = pd.DataFrame(
        {
            "x": [paint["x_min"], paint["x_max"], paint["x_max"], paint["x_min"], paint["x_min"]],
            "y": [paint["y_min"], paint["y_min"], paint["y_max"], paint["y_max"], paint["y_min"]],
            "order": range(5),
        }
    )
    layers.append(_line_layer(key_pts))

    # ---- Círculo de tiro libre, en el borde de la pintura más lejano a la canasta ----
    radius_ft = (paint["x_max"] - paint["x_min"]) / 2
    theta = np.linspace(0, 2 * np.pi, 61)
    ft_pts = pd.DataFrame(
        {
            "x": hoop_x + radius_ft * np.sin(theta),
            "y": paint["y_min"] + radius_ft * np.cos(theta),
            "order": range(len(theta)),
        }
    )
    layers.append(_line_layer(ft_pts))

    # ---- Canasta: marca pequeña en el centro de la pintura, junto a línea de fondo ----
    rim_pts = pd.DataFrame({"x": [hoop_x], "y": [hoop_y]})
    layers.append(
        alt.Chart(rim_pts)
        .mark_point(shape="circle", filled=False, size=50, color=_LINE_COLOR, strokeWidth=1.6)
        .encode(x=alt.X("x:Q", scale=alt.Scale(domain=_DOMAIN), axis=None), y=alt.Y("y:Q", scale=alt.Scale(domain=_DOMAIN), axis=None))
    )

    # ---- Línea de triple: dos tramos rectos (esquinas) + arco, todo derivado de zonas ----
    corner_l = _zone(zones, "Triple esquina izq.")
    corner_r = _zone(zones, "Triple esquina der.")
    beyond = _zone(zones, "Triple exterior")
    if corner_l is None or corner_r is None or beyond is None:
        return layers

    # El arco es una ELIPSE, no una circunferencia: la cancha estilizada de
    # `court_zones` no está a escala real (está comprimida a lo ancho — el
    # vértice del arco queda a 285 unidades del aro pero las rectas de esquina
    # a solo 195), así que la profundidad y el lateral tienen escalas
    # distintas. Los tiros se reescalan con esas mismas dos escalas en los
    # adaptadores (ver `ingest/euroleague/adapter.py::_rescale_shot_coords`);
    # dibujar aquí una circunferencia dejaba triples reales de ala pintados
    # DENTRO de la línea. Los dos semiejes salen de `court_zones`: el vertical
    # del vértice del arco ("Triple exterior"), y el horizontal de exigir que
    # la elipse pase por la esquina de la zona de triple de esquina — que es
    # justo lo que esa zona significa: dónde la recta de esquina corta el arco.
    radius_y = hoop_y - beyond["y_max"]
    x_left, x_right = corner_l["x_max"], corner_r["x_min"]
    break_dy = hoop_y - corner_l["y_min"]  # profundidad del corte recta-arco
    if radius_y <= 0 or not 0 <= break_dy < radius_y:
        # Geometría inconsistente (zonas reeditadas a mano de forma incompatible) — se omite
        # la línea de triple entera antes que dibujar un arco que no cierra.
        return layers
    radius_x = abs(hoop_x - x_left) / np.sqrt(1 - (break_dy / radius_y) ** 2)
    if radius_x <= 0:
        return layers

    arc_x = np.linspace(x_left, x_right, 80)
    arc_y = hoop_y - radius_y * np.sqrt(np.clip(1 - ((arc_x - hoop_x) / radius_x) ** 2, 0, None))

    three_pt_pts = pd.DataFrame(
        {
            "x": [x_left, x_left, *arc_x, x_right, x_right],
            "y": [corner_l["y_max"], arc_y[0], *arc_y, arc_y[-1], corner_r["y_max"]],
        }
    )
    three_pt_pts["order"] = range(len(three_pt_pts))
    layers.append(_line_layer(three_pt_pts))

    return layers


def _split_by_location(shots: pd.DataFrame) -> tuple:
    """Separa los tiros con coordenadas medidas de los que no las tienen.

    Sin columna `located` (consulta antigua, o un DataFrame construido a mano)
    se asume que todos están localizados: es como se comportaba el gráfico
    antes de que existiera la columna.
    """
    if "located" not in shots.columns:
        return shots, shots.iloc[0:0]
    return shots[shots["located"] == 1], shots[shots["located"] == 0]


def _unlocated_layers(unlocated: pd.DataFrame) -> list:
    """Un rombo por pila de tiros sin ubicación medida, con su recuento.

    Se agrupan por coordenada porque TODOS caen en el mismo punto (el aro):
    una capa de puntos normal pintaría cientos de marcas indistinguibles
    encima unas de otras, y el gráfico daría a entender que ahí hay un tiro.
    El tamaño del rombo es fijo, no proporcional al recuento — sin leyenda,
    un área variable no se lee como una cantidad; el número va en el tooltip
    y en el pie de gráfico.
    """
    if unlocated.empty:
        return []
    piles = (
        unlocated.groupby(["pos_x", "pos_y"], as_index=False)
        .agg(shots_count=("made", "size"), made_count=("made", "sum"))
    )
    return [
        alt.Chart(piles)
        .mark_point(shape="diamond", filled=True, size=180, color=_MADE_COLOR,
                    stroke=_PILE_STROKE, strokeWidth=1.4, opacity=1, clip=True)
        .encode(
            x=alt.X("pos_x:Q", scale=alt.Scale(domain=_DOMAIN)),
            y=alt.Y("pos_y:Q", scale=alt.Scale(domain=_DOMAIN)),
            tooltip=[
                alt.Tooltip("shots_count:Q", title="Sin ubicación exacta"),
                alt.Tooltip("made_count:Q", title="Anotados"),
            ],
        )
    ]


def shot_chart_caption(shots: pd.DataFrame) -> str:
    """Pie de gráfico de `shot_chart` (volumen, acierto y avisos de cobertura).

    Vive aquí, y no en cada página, porque las tres que dibujan el mapa
    necesitan exactamente el mismo texto — y porque el aviso de los tiros sin
    ubicación tiene que nombrar el símbolo con el que los pinta `shot_chart`.
    """
    _, unlocated = _split_by_location(shots)
    caption = (
        f"{len(shots)} tiros de campo · {100 * shots['made'].mean():.0f}% anotados · "
        "tiros libres excluidos (sin coordenadas en origen)"
    )
    if not unlocated.empty:
        caption += (
            f" · {len(unlocated)} sin ubicación exacta en origen (mates), "
            f"agrupados en el aro como {_PILE_GLYPH}"
        )
    return caption


def shot_chart(shots: pd.DataFrame, zones: pd.DataFrame) -> alt.LayerChart:
    """Construye el gráfico de tiros de un partido (equipo ya filtrado en la consulta).

    Args:
        shots: salida de `queries.game_shots` (`pos_x`, `pos_y`, `made`, ...).
        zones: salida de `queries.court_zones` (`label`, `x_min/x_max/y_min/y_max`).

    Returns:
        Gráfico Altair en capas (líneas de cancha + fallados + anotados) con
        ancho y alto FIJOS (360x360, dominio cuadrado — ver nota al final de
        la función), listo para `st.altair_chart(...)` **sin**
        `use_container_width=True` (rompe el aspect ratio, ver esa nota). Si
        `shots` está vacío, la capa de puntos simplemente no dibuja nada —
        no es un caso especial que haya que manejar aparte.
    """
    located, unlocated = _split_by_location(shots)

    # `clip=True`: un tiro desde el medio campo (o desde la propia pista) cae
    # fuera del dominio 0-500 y Vega, por defecto, lo pinta igual FUERA del
    # área del gráfico, encima del resto de la página. Se recorta al campo.
    made = located[located["made"] == 1]
    missed = located[located["made"] == 0]

    made_layer = (
        alt.Chart(made)
        .mark_point(filled=True, size=90, color=_MADE_COLOR, opacity=0.85, clip=True)
        .encode(
            x=alt.X("pos_x:Q", scale=alt.Scale(domain=_DOMAIN)),
            y=alt.Y("pos_y:Q", scale=alt.Scale(domain=_DOMAIN)),
            tooltip=[alt.Tooltip("player_name:N", title="Jugador")],
        )
    )
    missed_layer = (
        alt.Chart(missed)
        .mark_point(filled=False, size=90, color=_MISS_STROKE, strokeWidth=1.6, opacity=0.85, clip=True)
        .encode(
            x=alt.X("pos_x:Q", scale=alt.Scale(domain=_DOMAIN)),
            y=alt.Y("pos_y:Q", scale=alt.Scale(domain=_DOMAIN)),
            tooltip=[alt.Tooltip("player_name:N", title="Jugador")],
        )
    )

    layers = [*_court_line_layers(zones), missed_layer, made_layer, *_unlocated_layers(unlocated)]
    chart = layers[0]
    for layer in layers[1:]:
        chart = chart + layer
    # Ancho FIJO igual a la altura, a propósito — el dominio (0-500 en ambos
    # ejes) es un cuadrado; dejar que `st.altair_chart(..., use_container_width=True)`
    # estire el ancho al contenedor (sin tocar la altura) aplana la cancha en
    # cualquier sitio más ancho que ~360px, muy visible dentro del modal de
    # detalle de jugador (`components/player_dialog.py`, más ancho que el resto
    # de la app). Los tres sitios que llaman a esta función NO deben pasar
    # `use_container_width=True` para este gráfico en concreto (si no, Streamlit
    # vuelve a sobreescribir este ancho).
    return chart.properties(height=360, width=360).configure_view(strokeWidth=0)
