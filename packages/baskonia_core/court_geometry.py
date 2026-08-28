"""Geometría de la línea de triple derivada de `court_zones`, compartida.

Vive en `baskonia_core` (no en `app/` ni en `ingest/`) porque las DOS mitades
del proyecto la necesitan y antes cada una la calculaba por su cuenta:
- `ingest/common/zones.py::classify_zone` la usa para decidir, tiro a tiro, si
  un punto dentro de "Ala izq."/"Ala der." (ver `schema.sql`) es un 2 largo o
  un triple de ala — esas dos zonas mezclaban ambos en el mismo rectángulo.
- `app/components/court.py` la usa para DIBUJAR esa misma línea (el arco de
  `_court_line_layers`) y para partir el fondo de esas dos zonas por ella
  (`_wing_split_layers`/`zone_heatmap`).

Tenerla en dos sitios fue justo lo que dejó pasar dos bugs de calibración
distintos en el mapa de tiros (ver el docstring de
`ingest/common/zones.py::to_court_coords`) — con una sola implementación,
ajustarla es un cambio en un sitio, y la línea que dibuja la interfaz es
EXACTAMENTE la misma con la que se clasifican los tiros en la ingesta.

Solo `math` de la librería estándar a propósito, nada de `numpy`: el
pipeline base de `ingest/` (`ingest/requirements.txt`) no lo trae — solo lo
arrastra la fuente Euroliga, opcional (`ingest/euroleague/requirements.txt`)
— así que `classify_zone` (que sí es de la ruta común, no solo Euroliga) no
puede depender de él. `app/components/court.py`, que sí necesita dibujar un
array entero de puntos del arco, vectoriza por su cuenta con `numpy` a partir
de la misma fórmula escalar de aquí (ver ese módulo).
"""
import math
from typing import Mapping, Optional, Tuple

# (hoop_x, hoop_y, radius_x, radius_y, x_left, x_right) de la elipse de triple.
Ellipse = Tuple[float, float, float, float, float, float]


def three_point_ellipse(
    paint: Mapping[str, float],
    corner_l: Mapping[str, float],
    corner_r: Mapping[str, float],
    beyond: Mapping[str, float],
) -> Optional[Ellipse]:
    """Elipse de la línea de triple a partir de 4 filas de `court_zones`.

    Cada argumento acepta cualquier objeto indexable por nombre de columna
    (`x_min`, `x_max`, `y_min`, `y_max`) — una `pandas.Series` (fila de un
    DataFrame de `court_zones`, ver `app/components/court.py`) o un mapping
    de SQLAlchemy (`Row.mappings()`, ver `ingest/common/zones.py`) sirven
    igual, así que esta función no depende de ninguno de los dos.

    El arco es una ELIPSE, no una circunferencia: la cancha estilizada de
    `court_zones` no está a escala real (está comprimida a lo ancho — el
    vértice del arco queda a 285 unidades del aro pero las rectas de esquina
    a solo 195), así que la profundidad y el lateral tienen escalas
    distintas. Los dos semiejes salen de `court_zones`: el vertical del
    vértice del arco ("Triple exterior"), y el horizontal de exigir que la
    elipse pase por la esquina de la zona de triple de esquina — que es
    justo lo que esa zona significa: dónde la recta de esquina corta el arco.

    Returns:
        `(hoop_x, hoop_y, radius_x, radius_y, x_left, x_right)`, o `None` si
        la geometría de `court_zones` falta o es inconsistente (zonas
        reeditadas a mano de forma incompatible) — nunca se inventa una
        elipse a partir de datos que no cierran.
    """
    hoop_x = (paint["x_min"] + paint["x_max"]) / 2
    hoop_y = paint["y_max"]  # borde de la pintura más cercano a la línea de fondo
    radius_y = hoop_y - beyond["y_max"]
    x_left, x_right = corner_l["x_max"], corner_r["x_min"]
    break_dy = hoop_y - corner_l["y_min"]  # profundidad del corte recta-arco
    if radius_y <= 0 or not 0 <= break_dy < radius_y:
        return None
    radius_x = abs(hoop_x - x_left) / math.sqrt(1 - (break_dy / radius_y) ** 2)
    if radius_x <= 0:
        return None
    return hoop_x, hoop_y, radius_x, radius_y, x_left, x_right


def wing_boundary_y(x: float, geom: Ellipse) -> Optional[float]:
    """Y de la línea de triple en `x` (escalar), según la elipse de `geom`.

    `None` donde `x` cae fuera del alcance lateral de la elipse (más allá del
    punto de ruptura de esquina): ahí no hay frontera 2/3 que trazar porque
    la columna entera cae en triple (la esquina real: a esa distancia
    lateral, hasta el punto más cercano al aro ya cae fuera del arco) — quien
    llama decide qué hacer con ese `None` (ver `ingest/common/zones.py` y el
    equivalente vectorizado de `app/components/court.py::_wing_bands`).
    """
    hoop_x, hoop_y, radius_x, radius_y, _, _ = geom
    if abs(x - hoop_x) >= radius_x:
        return None
    # `max(..., 0.0)` antes de la raíz: guarda solo contra ruido de coma
    # flotante en el borde EXACTO del dominio, no una segunda comprobación de
    # "dentro/fuera" — esa ya la hace la condición de arriba.
    val = max(1 - ((x - hoop_x) / radius_x) ** 2, 0.0)
    return hoop_y - radius_y * math.sqrt(val)
