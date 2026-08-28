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
reajustar con ellas la calibración de esa conversión.

Las zonas de `court_zones` también se dibujan como rectángulos de fondo
(`_zone_layers`) — se habían quitado en su día porque competían visualmente
con las líneas reales de cancha; esta vez van con trazo discontinuo muy
tenue (`_ZONE_STROKE`, más claro que `_LINE_COLOR`) y como la capa MÁS AL
FONDO, por debajo de las líneas de cancha y de los tiros, para que sigan
sirviendo de referencia (qué zona es cada una de la tabla de acierto de
`zone_breakdown`) sin robarle protagonismo a lo importante. "Pintura" se
omite del rectángulo: coincide exactamente con la caja de la pintura que ya
dibuja `_court_line_layers`, así que repetirla ahí solo duplicaba trazo.
"""
import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# Mismo arranque que `app/data/db.py`/`app/assistant/__init__.py`: `app/` no
# vive en la raíz del repo, así que hay que añadir la raíz a `sys.path` para
# poder importar `packages.*` (aquí, la elipse de triple compartida con
# `ingest/common/zones.py`, ver `court_geometry`). Este módulo puede ser el
# PRIMERO en tocar `packages.*` si algo lo importa antes que `data.db` (ver
# `components/player_dialog.py`), así que no basta con confiar en que otro
# módulo ya lo haya hecho.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packages.baskonia_core import court_geometry  # noqa: E402

_MADE_COLOR = "#008300"   # verde Baskonia — mismo accent que el resto de la interfaz
_MISS_STROKE = "#898781"  # ink muted
_LINE_COLOR = "#5c5a52"   # líneas de cancha — neutro, sin competir con los tiros
_ZONE_STROKE = "#c9c7bf"  # rectángulos de zona: bastante más claro que `_LINE_COLOR`, van de fondo
_ZONE_LABEL = "#a6a49c"   # etiqueta de zona: mismo tono apagado, algo más marcado para poder leerse
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


def _zone_layers(zones: pd.DataFrame) -> list:
    """Rectángulos de fondo de cada zona de `court_zones`, con su etiqueta.

    "Pintura" se omite: su rectángulo es idéntico a la caja de pintura que ya
    dibuja `_court_line_layers`, así que incluirla aquí solo duplicaba trazo
    encima de sí misma. Una zona degenerada (`x_min == x_max` o `y_min ==
    y_max` — hoy "Mate", un punto pegado al aro, no un área) tampoco tiene
    rectángulo que dibujar y se omite igual, sin que sea un error.

    "Ala izq."/"Ala der." también se omiten de este rectángulo simple CUANDO
    hay geometría de triple con la que partirlas (`_wing_split_layers`
    delega en `_three_point_ellipse`, la misma elipse que dibuja la línea
    real): el hueco lo rellenan sus dos polígonos en vez del rectángulo
    único. Sin esa geometría (`court_zones` incompleto) caen al mismo
    rectángulo sin partir de siempre — no un caso especial, solo que
    `_wing_split_layers` no tiene nada que devolver.
    """
    wing_layers = _wing_split_layers(zones)
    excluded = {"Pintura", "Ala izq.", "Ala der."} if wing_layers else {"Pintura"}
    boxes = zones.loc[
        (~zones["label"].isin(excluded)) & (zones["x_min"] != zones["x_max"]) & (zones["y_min"] != zones["y_max"])
    ]

    layers = []
    if not boxes.empty:
        rect_layer = (
            alt.Chart(boxes)
            .mark_rect(fillOpacity=0, stroke=_ZONE_STROKE, strokeWidth=1, strokeDash=[3, 3])
            .encode(
                x=alt.X("x_min:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                x2="x_max:Q",
                y=alt.Y("y_min:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                y2="y_max:Q",
            )
        )
        label_layer = (
            alt.Chart(
                boxes.assign(cx=(boxes["x_min"] + boxes["x_max"]) / 2, cy=(boxes["y_min"] + boxes["y_max"]) / 2)
            )
            .mark_text(fontSize=8, color=_ZONE_LABEL)
            .encode(
                x=alt.X("cx:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                y=alt.Y("cy:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                text="label:N",
            )
        )
        layers = [rect_layer, label_layer]
    return layers + wing_layers


def _three_point_ellipse(zones: pd.DataFrame):
    """Geometría de la elipse de triple (radios/centro), o `None` si falta.

    Delega el cálculo en `packages.baskonia_core.court_geometry` — la MISMA
    función que usa `ingest/common/zones.py::classify_zone` para decidir 2/3
    dentro de "Ala izq./der.", así que la línea que dibuja la interfaz y la
    que clasifica los tiros en la ingesta no pueden divergir (ver el
    docstring de ese módulo). Esta función solo resuelve las 4 filas de
    `court_zones` que hacen falta y traduce "falta una" a `None` — el resto
    de criterios de "geometría inconsistente" viven en `court_geometry`.
    """
    paint = _zone(zones, "Pintura")
    corner_l = _zone(zones, "Triple esquina izq.")
    corner_r = _zone(zones, "Triple esquina der.")
    beyond = _zone(zones, "Triple exterior")
    if paint is None or corner_l is None or corner_r is None or beyond is None:
        return None
    return court_geometry.three_point_ellipse(paint, corner_l, corner_r, beyond)


# ("Ala izq."/"Ala der." en `court_zones`) -> etiquetas de sus dos mitades.
_WING_ZONE_LABELS = (
    ("Ala izq.", "Ala izq. (2)", "Ala izq. (3)"),
    ("Ala der.", "Ala der. (2)", "Ala der. (3)"),
)


def _wing_boundary_y(x_vals: np.ndarray, geom: tuple) -> np.ndarray:
    """Versión vectorizada (numpy) de `court_geometry.wing_boundary_y`.

    Esa función es escalar a propósito (la usa `ingest/common/zones.py`, que
    no puede depender de `numpy` — ver el docstring de `court_geometry`);
    aquí hace falta la misma fórmula sobre un array entero de puntos del
    arco de una sola vez (decenas de veces por gráfico), así que se
    vectoriza por separado en vez de llamarla en un bucle. MISMA fórmula que
    esa función — si se retoca una, hay que retocar la otra.

    `nan` donde `x` cae fuera del alcance lateral de la elipse (más allá del
    punto de ruptura de esquina) — ahí no hay frontera 2/3 que trazar porque
    la columna entera es triple. `_wing_bands` trata ese `nan` como "la
    frontera coincide con el borde de la zona más lejano al aro".
    """
    hoop_x, hoop_y, radius_x, radius_y, _, _ = geom
    y = np.full_like(x_vals, np.nan, dtype=float)
    inside = np.abs(x_vals - hoop_x) < radius_x
    y[inside] = hoop_y - radius_y * np.sqrt(1 - ((x_vals[inside] - hoop_x) / radius_x) ** 2)
    return y


def _wing_bands(zone_row: pd.Series, geom: tuple) -> list:
    """Parte el rectángulo de una zona de ala en hasta dos bandas (2 y 3).

    Devuelve una lista de `(kind, xs, y_low, y_high)` con `kind` en `{"2",
    "3"}` — una banda por lado que de verdad tiene área dentro de este
    rectángulo (el lado sin área, p.ej. una esquina donde toda la columna es
    triple, se omite entero en vez de devolver una franja de alto cero). Es
    la pieza común de `_wing_polygons` (contorno discontinuo del fondo de
    `shot_chart`) y de `_wing_area_layers` (relleno coloreado de
    `zone_heatmap`) — ambas dibujan la MISMA frontera, solo cambia si la
    pintan como contorno o como área.

    La frontera se muestrea en `x` (no es una recta: sigue la elipse de
    `geom`) y se recorta al propio rectángulo (`np.clip`) para que la
    columna donde la elipse pasa por encima o por debajo de la zona salga
    como "toda 2" o "toda 3" en vez de un valor fuera de rango.
    """
    x0, x1 = float(zone_row["x_min"]), float(zone_row["x_max"])
    y0, y1 = float(zone_row["y_min"]), float(zone_row["y_max"])  # y1 = borde más cercano al aro
    xs = np.linspace(x0, x1, 60)
    boundary = _wing_boundary_y(xs, geom)
    boundary = np.where(np.isnan(boundary), y1, boundary)
    boundary = np.clip(boundary, y0, y1)

    bands = []
    if boundary.min() < y1 - 1e-6:  # hay algo de "2" (frontera por debajo del borde cercano al aro)
        bands.append(("2", xs, boundary, np.full_like(xs, y1)))
    if boundary.max() > y0 + 1e-6:  # hay algo de "3" (frontera por encima del borde lejano)
        bands.append(("3", xs, np.full_like(xs, y0), boundary))
    return bands


def _wing_polygons(zone_row: pd.Series, geom: tuple) -> list:
    """Contorno cerrado (para dibujar) de cada banda de `_wing_bands`.

    Devuelve `(kind, poly_x, poly_y)` — el mismo par de x/y que espera
    `_line_layer`, trazando el perímetro de la banda una sola vez (sin
    autointersección) para que `mark_line` lo pinte como un polígono
    discontinuo en vez de una maraña de segmentos.
    """
    shapes = []
    for kind, xs, y_low, y_high in _wing_bands(zone_row, geom):
        poly_x = list(xs) + list(xs[::-1]) + [xs[0]]
        poly_y = list(y_high) + list(y_low[::-1]) + [y_high[0]]
        shapes.append((kind, poly_x, poly_y))
    return shapes


def _wing_split_layers(zones: pd.DataFrame) -> list:
    """"Ala izq."/"Ala der.", partidas por la línea real de triple.

    Antes eran un único rectángulo que mezclaba tiros de 2 largos y triples
    de ala — pedido explícito: que el propio FONDO del mapa de puntos ya
    distinga de un vistazo qué parte de esa banda es de 2 y cuál de 3, en vez
    de dejarlo solo a la línea fina de `_court_line_layers` cruzando por
    encima sin más marca. Los puntos individuales del mapa
    (`made_layer`/`missed_layer`) ya eran fieles a sus coordenadas reales
    antes de este cambio — esto solo hace visible, en el propio fondo, dónde
    cae esa frontera.

    Dibuja el CONTORNO (discontinuo, sin relleno) para el fondo de
    `shot_chart` — `zone_heatmap` pinta la MISMA frontera pero rellena por
    acierto (`_wing_area_layers`, que reutiliza `_wing_bands`) porque desde
    que `ingest/common/zones.py::classify_zone` resuelve estas dos zonas
    contra la elipse real, `zone_df` ya trae "Ala izq. (2)"/"Ala izq. (3)"
    (e igual a la derecha) como filas propias con su propio acierto.

    Devuelve `[]` si falta la geometría de la elipse (`_three_point_ellipse`)
    o alguna de las dos zonas de ala en `zones` — `_zone_layers` cae entonces
    al rectángulo único de siempre para esas dos zonas, sin romper nada.
    """
    geom = _three_point_ellipse(zones)
    if geom is None:
        return []

    layers = []
    for label, label_2, label_3 in _WING_ZONE_LABELS:
        zone_row = _zone(zones, label)
        if zone_row is None:
            continue
        for kind, poly_x, poly_y in _wing_polygons(zone_row, geom):
            pts = pd.DataFrame({"x": poly_x, "y": poly_y, "order": range(len(poly_x))})
            layers.append(
                alt.Chart(pts)
                .mark_line(color=_ZONE_STROKE, strokeWidth=1, strokeDash=[3, 3])
                .encode(
                    x=alt.X("x:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    y=alt.Y("y:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    order="order:Q",
                )
            )
            # Centroide aproximado (media de los vértices, sin el punto de
            # cierre repetido) como ancla de la etiqueta — de sobra para un
            # texto de 8px dentro de una cuña, no hace falta el centroide
            # exacto del polígono.
            label_pts = pd.DataFrame(
                {
                    "cx": [float(np.mean(poly_x[:-1]))],
                    "cy": [float(np.mean(poly_y[:-1]))],
                    "label": [label_2 if kind == "2" else label_3],
                }
            )
            layers.append(
                alt.Chart(label_pts)
                .mark_text(fontSize=8, color=_ZONE_LABEL)
                .encode(
                    x=alt.X("cx:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    y=alt.Y("cy:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    text="label:N",
                )
            )
    return layers


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

    # ---- Línea de triple: dos tramos rectos (esquinas) + arco ----
    # El arco es una ELIPSE, no una circunferencia (`_three_point_ellipse`,
    # que `_wing_split_layers` también reutiliza para partir "Ala izq./der."
    # por esta misma línea): la cancha estilizada de `court_zones` no está a
    # escala real (está comprimida a lo ancho — el vértice del arco queda a
    # 285 unidades del aro pero las rectas de esquina a solo 195), así que la
    # profundidad y el lateral tienen escalas distintas. Los tiros se
    # reescalan con esas mismas dos escalas en los adaptadores (ver
    # `ingest/euroleague/adapter.py::_rescale_shot_coords`); dibujar aquí una
    # circunferencia dejaba triples reales de ala pintados DENTRO de la línea.
    geom = _three_point_ellipse(zones)
    if geom is None:
        # Geometría ausente o inconsistente (zonas de referencia que faltan,
        # o reeditadas a mano de forma incompatible) — se omite la línea de
        # triple entera antes que dibujar un arco que no cierra.
        return layers
    hoop_x_g, hoop_y_g, radius_x, radius_y, x_left, x_right = geom
    corner_l = _zone(zones, "Triple esquina izq.")
    corner_r = _zone(zones, "Triple esquina der.")

    arc_x = np.linspace(x_left, x_right, 80)
    arc_y = hoop_y_g - radius_y * np.sqrt(np.clip(1 - ((arc_x - hoop_x_g) / radius_x) ** 2, 0, None))

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


def zone_breakdown(zone_df: pd.DataFrame, total_shots: int, scope: str = "team") -> None:
    """Expander con la tabla de acierto/volumen por zona, bajo `shot_chart`.

    Nació en "Próximo rival" (equipo rival) y se comparte aquí para "Estado
    del equipo" (equipo propio) y el modal de jugador — las tres pantallas
    necesitan exactamente el mismo aviso de cobertura y la misma advertencia
    sobre la geometría de las zonas (rectángulos, no el arco real de triple:
    ver el comentario sobre `CREATE TABLE court_zones` en `schema.sql`), así
    que vive en un solo sitio en vez de tres copias que puedan divergir.

    "Ala izq."/"Ala der." salían antes como una sola fila que mezclaba tiros
    de 2 largos y triples de ala (rectángulo, no el arco real) — desde que
    `ingest/common/zones.py::classify_zone` las resuelve contra la elipse de
    triple real (`packages.baskonia_core.court_geometry`), `zone_df` trae
    "Ala izq. (2)"/"Ala izq. (3)" (e igual a la derecha) como filas propias,
    así que esta tabla ya no necesita advertir de esa mezcla.

    No dibuja nada si `zone_df` está vacío — el mapa de puntos de arriba ya
    cubre ese caso (`shot_chart` no necesita zonas para pintarse).

    Args:
        zone_df: salida de `queries.team_zone_profile` o
            `queries.player_zone_profile` (`zone_label, fg_pct, volume`).
        total_shots: tiros de campo totales del ámbito (equipo o jugador),
            para calcular qué parte cae dentro de alguna zona — `len(...)`
            del `shots_df` que ya se le pasó a `shot_chart`.
        scope: `"team"` (por defecto) o `"player"` — solo cambia la última
            nota, que en equipo aclara que la tabla mezcla a todos los
            jugadores (el mapa de arriba sí se puede filtrar por jugador en
            algunas pantallas; la tabla no).
    """
    if zone_df.empty:
        return
    covered = int(zone_df["volume"].sum())
    coverage_pct = 100 * covered / total_shots if total_shots else 0.0
    with st.expander(f"Acierto por zona de cancha (cubre {coverage_pct:.0f}% de los tiros)"):
        caveat = (
            "Pintura, Ala izq./der. (2)/(3), Triple esquina, Triple exterior y Mate no mezclan "
            "2 y 3 puntos — ninguna zona de esta tabla lo hace ya. Sigue siendo una malla de "
            "rectángulos (aproximada salvo en Ala izq./der., partidas por el arco real), no una "
            "lectura milimétrica: para eso está el mapa de arriba."
        )
        if coverage_pct < 80:
            st.warning(
                f"Solo {covered} de {total_shots} tiros ({coverage_pct:.0f}%) caen dentro de "
                f"alguna zona de `court_zones` — sigue quedando fuera una parte apreciable. {caveat}"
            )
        else:
            st.caption(f"{covered} de {total_shots} tiros ({coverage_pct:.0f}%) con zona asignada. {caveat}")
        st.dataframe(
            zone_df,
            hide_index=True,
            width="stretch",
            # `made` (añadida para `zone_heatmap`, ver su docstring) queda
            # fuera de esta tabla a propósito — aquí ya bastaba con el %, no
            # hace falta duplicar el dato en dos formatos.
            column_order=["zone_label", "fg_pct", "volume"],
            column_config={
                "zone_label": st.column_config.TextColumn("Zona"),
                "fg_pct": st.column_config.NumberColumn("% acierto", format="%.1f"),
                "volume": st.column_config.NumberColumn("Tiros"),
            },
        )
        note = (
            " Agregado por equipo — no distingue jugador, a diferencia del mapa."
            if scope == "team"
            else ""
        )
        st.caption(
            "% ponderado por volumen (no la media simple de los porcentajes de cada partido)."
            f"{note}"
        )


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _lerp_color(c1: tuple, c2: tuple, t: float) -> str:
    t = min(max(t, 0.0), 1.0)
    r, g, b = (round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


_HEAT_LOW = _hex_to_rgb("#c62828")   # rojo — peor acierto DE ESTE gráfico, no un umbral absoluto
_HEAT_MID = _hex_to_rgb("#f2c94c")   # amarillo — acierto intermedio
_HEAT_HIGH = _hex_to_rgb(_MADE_COLOR)  # verde Baskonia — mejor acierto DE ESTE gráfico
_HEAT_EMPTY = "#e6e4dc"  # zona sin tiros — gris cálido neutro, no entra en la escala


def _heat_color(pct, lo: float, mid: float, hi: float) -> str:
    """Color de una zona en `zone_heatmap`: rojo→amarillo→verde, relativo a ESTE gráfico.

    No es una escala absoluta (no hay un "45% es bueno/malo" universal sin
    comparar contra la media de la liga por zona, dato que no tenemos) — es
    relativa al propio reparto de `fg_pct` entre las zonas que se están
    pintando: la peor de ESTE gráfico sale roja, la mejor verde, aunque
    ambas fueran buenas o malas comparadas con otro equipo. `zone_heatmap`
    dice esto en su pie de gráfico para que no se lea como un baremo fijo.
    """
    if pd.isna(pct):
        return _HEAT_EMPTY
    if lo >= hi:  # todas las zonas con dato empatan — no hay "peor/mejor" que marcar
        return _lerp_color(_HEAT_MID, _HEAT_MID, 0)
    if pct <= mid:
        span = mid - lo
        return _lerp_color(_HEAT_LOW, _HEAT_MID, 0 if span <= 0 else (pct - lo) / span)
    span = hi - mid
    return _lerp_color(_HEAT_MID, _HEAT_HIGH, 0 if span <= 0 else (pct - mid) / span)


def _wing_area_layers(zones: pd.DataFrame, zone_df: pd.DataFrame, lo: float, mid: float, hi: float) -> list:
    """Relleno coloreado de "Ala izq./der." partidas por la línea de triple, para `zone_heatmap`.

    Homólogo de `_wing_split_layers` (fondo de `shot_chart`) pero coloreado
    por `fg_pct` en vez de un contorno discontinuo — reutiliza las mismas
    bandas de `_wing_bands` (la MISMA frontera en los dos mapas) y las pinta
    con `mark_area` (relleno entre `y_low`/`y_high`), no `mark_rect`: un
    rectángulo no puede partirse por una curva sin que las dos mitades se
    solapen, `mark_area` sí admite un borde que sigue la elipse.

    `lo`/`mid`/`hi` son los mismos límites de color que usa el resto del
    mapa (calculados por `zone_heatmap` sobre TODO `zone_df`, incluidas estas
    bandas) — para que el rojo/verde de "Ala izq. (2)" sea comparable con el
    de cualquier otra zona del mismo gráfico, no una escala aparte.

    Cada banda busca su fila en `zone_df` por `zone_label` ("Ala izq. (2)",
    etc. — las produce `queries.team_zone_profile`/`player_zone_profile` en
    cuanto `shots.zone_id` está clasificado contra esa geometría, ver
    `ingest/common/zones.py::classify_zone`). Sin esa fila (tiros aún sin
    reclasificar, o sin volumen esa banda) sale en gris "Sin tiros", igual
    que cualquier otra zona del mapa sin dato — nunca como un hueco vacío.
    """
    geom = _three_point_ellipse(zones)
    if geom is None:
        return []

    layers = []
    for base_label, label_2, label_3 in _WING_ZONE_LABELS:
        zone_row = _zone(zones, base_label)
        if zone_row is None:
            continue
        for kind, xs, y_low, y_high in _wing_bands(zone_row, geom):
            sub_label = label_2 if kind == "2" else label_3
            match = zone_df.loc[zone_df["zone_label"] == sub_label]
            has_data = not match.empty
            fg_pct = float(match["fg_pct"].iloc[0]) if has_data else float("nan")
            line1 = f"{int(match['made'].iloc[0])} / {int(match['volume'].iloc[0])}" if has_data else "Sin tiros"
            line2 = f"{fg_pct:.1f}%" if has_data else ""

            band = pd.DataFrame({"x": xs, "y_low": y_low, "y_high": y_high})
            band["label"], band["line1"], band["line2"] = sub_label, line1, line2
            layers.append(
                alt.Chart(band)
                .mark_area(stroke="#ffffff", strokeWidth=1.5, color=_heat_color(fg_pct, lo, mid, hi))
                .encode(
                    x=alt.X("x:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    y=alt.Y("y_low:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    y2="y_high:Q",
                    tooltip=[
                        alt.Tooltip("label:N", title="Zona"),
                        alt.Tooltip("line1:N", title="Aciertos/Intentos"),
                        alt.Tooltip("line2:N", title="% acierto"),
                    ],
                )
            )
            label_pts = pd.DataFrame(
                {
                    "cx": [float(np.mean(xs))],
                    "cy": [float(np.mean((y_low + y_high) / 2))],
                    "line1": [line1],
                    "line2": [line2],
                }
            )
            layers.append(
                alt.Chart(label_pts)
                .mark_text(fontSize=9, fontWeight="bold", color="#1a1a1a", dy=-6)
                .encode(
                    x=alt.X("cx:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    y=alt.Y("cy:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    text="line1:N",
                )
            )
            layers.append(
                alt.Chart(label_pts)
                .mark_text(fontSize=8, color="#1a1a1a", dy=6)
                .encode(
                    x=alt.X("cx:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    y=alt.Y("cy:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                    text="line2:N",
                )
            )
    return layers


def zone_heatmap(zone_df: pd.DataFrame, zones: pd.DataFrame) -> alt.LayerChart:
    """Cancha coloreada por acierto de zona, con la fracción escrita encima de cada una.

    Complementa a `shot_chart` (nube de tiros individuales) en vez de
    sustituirlo — de un vistazo dice QUÉ zonas van bien/mal, algo que un
    mapa de puntos no comunica sin contarlos a ojo. Pedido explícitamente
    como una vista más, a añadir junto al mapa existente, no en su lugar.

    Args:
        zone_df: salida de `queries.team_zone_profile` o
            `queries.player_zone_profile` (`zone_label, fg_pct, volume,
            made`).
        zones: salida de `queries.court_zones` (geometría de cada zona).

    Returns:
        Gráfico Altair en capas (zonas coloreadas + fracción/% encima +
        líneas reales de cancha) con ancho y alto FIJOS (mismo criterio que
        `shot_chart` — el dominio es cuadrado, `use_container_width=True` lo
        aplana). Si `zone_df` está vacío, TODAS las zonas salen en gris
        neutro ("sin tiros") — no es un caso especial, es el mismo camino
        que una zona cualquiera sin dato.

    "Mate" queda fuera: su geometría es un punto degenerado (ver
    `court_zones`), no hay rectángulo que colorear — sus mates SÍ cuentan en
    `zone_breakdown` (tabla), solo no tienen sitio en ESTE mapa.

    "Ala izq."/"Ala der." (el rectángulo único, sin partir) también quedan
    fuera CUANDO hay geometría de triple con la que partirlas: las sustituyen
    sus dos bandas coloreadas de `_wing_area_layers` ("Ala izq. (2)"/"Ala
    izq. (3)", etc.) — mismo criterio de reemplazo que `shot_chart` aplica a
    su propio fondo (`_zone_layers`/`_wing_split_layers`). Sin esa geometría
    caen al rectángulo único de siempre, sin romper nada.
    """
    with_data = zone_df["fg_pct"].dropna() if not zone_df.empty else zone_df.get("fg_pct", pd.Series(dtype=float))
    lo, mid, hi = (with_data.min(), with_data.median(), with_data.max()) if not with_data.empty else (0, 0, 0)
    wing_layers = _wing_area_layers(zones, zone_df, lo, mid, hi)

    excluded = {"Mate", "Ala izq.", "Ala der."} if wing_layers else {"Mate"}
    boxes = zones.loc[
        (~zones["label"].isin(excluded)) & (zones["x_min"] != zones["x_max"]) & (zones["y_min"] != zones["y_max"])
    ].merge(zone_df, left_on="label", right_on="zone_label", how="left")
    if boxes.empty and not wing_layers:
        return alt.LayerChart()

    layers = [*_court_line_layers(zones), *wing_layers]
    if not boxes.empty:
        boxes = boxes.assign(
            fill_color=boxes["fg_pct"].apply(lambda p: _heat_color(p, lo, mid, hi)),
            line1=boxes.apply(
                lambda r: "Sin tiros" if pd.isna(r["fg_pct"]) else f"{int(r['made'])} / {int(r['volume'])}", axis=1
            ),
            line2=boxes["fg_pct"].apply(lambda p: "" if pd.isna(p) else f"{p:.1f}%"),
            cx=(boxes["x_min"] + boxes["x_max"]) / 2,
            cy=(boxes["y_min"] + boxes["y_max"]) / 2,
        )
        rect_layer = (
            alt.Chart(boxes)
            .mark_rect(stroke="#ffffff", strokeWidth=1.5)
            .encode(
                x=alt.X("x_min:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                x2="x_max:Q",
                y=alt.Y("y_min:Q", scale=alt.Scale(domain=_DOMAIN), axis=None),
                y2="y_max:Q",
                color=alt.Color("fill_color:N", scale=None),
                tooltip=[
                    alt.Tooltip("label:N", title="Zona"),
                    alt.Tooltip("line1:N", title="Aciertos/Intentos"),
                    alt.Tooltip("line2:N", title="% acierto"),
                ],
            )
        )
        label1_layer = (
            alt.Chart(boxes)
            .mark_text(fontSize=11, fontWeight="bold", color="#1a1a1a", dy=-7)
            .encode(x=alt.X("cx:Q", scale=alt.Scale(domain=_DOMAIN), axis=None), y=alt.Y("cy:Q", scale=alt.Scale(domain=_DOMAIN), axis=None), text="line1:N")
        )
        label2_layer = (
            alt.Chart(boxes)
            .mark_text(fontSize=10, color="#1a1a1a", dy=7)
            .encode(x=alt.X("cx:Q", scale=alt.Scale(domain=_DOMAIN), axis=None), y=alt.Y("cy:Q", scale=alt.Scale(domain=_DOMAIN), axis=None), text="line2:N")
        )
        # `rect_layer` va PRIMERO (fondo) para que las líneas reales de cancha y
        # las bandas de ala se dibujen encima; las etiquetas de zona normal,
        # las ÚLTIMAS, para que ganen a cualquier cosa que quede debajo.
        layers = [rect_layer, *_court_line_layers(zones), *wing_layers, label1_layer, label2_layer]

    chart = layers[0]
    for layer in layers[1:]:
        chart = chart + layer
    # Mismas dimensiones fijas que `shot_chart` — ver esa función para el porqué.
    return chart.properties(height=360, width=360).configure_view(strokeWidth=0)


def zone_heatmap_caption(zone_df: pd.DataFrame) -> str:
    """Pie de gráfico de `zone_heatmap` — deja claro que el color es relativo, no un baremo."""
    if zone_df.empty:
        return "Sin tiros con zona registrados — todo el mapa sale en gris."
    return (
        "Color relativo a las propias zonas de este gráfico (roja = la de peor acierto AQUÍ, "
        "verde = la de mejor, no un baremo fijo tipo \"45% es bueno\") — compara zonas entre sí, "
        "no contra otro equipo o jugador."
    )


def shot_chart(shots: pd.DataFrame, zones: pd.DataFrame) -> alt.LayerChart:
    """Construye el gráfico de tiros de un partido (equipo ya filtrado en la consulta).

    Args:
        shots: salida de `queries.game_shots` (`pos_x`, `pos_y`, `made`, ...).
        zones: salida de `queries.court_zones` (`label`, `x_min/x_max/y_min/y_max`).

    Returns:
        Gráfico Altair en capas (zonas de fondo + líneas de cancha + fallados
        + anotados) con ancho y alto FIJOS (360x360, dominio cuadrado — ver
        nota al final de la función), listo para `st.altair_chart(...)` **sin**
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

    layers = [*_zone_layers(zones), *_court_line_layers(zones), missed_layer, made_layer, *_unlocated_layers(unlocated)]
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
