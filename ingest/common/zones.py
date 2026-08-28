"""Coordenadas de tiro en la escala de `court_zones` y clasificación en zonas.

Cada fuente tiene su propio sistema de coordenadas de tiro (ACB en mm,
Euroliga en cm...); el adaptador de cada módulo de ingesta es responsable de
convertir sus coordenadas nativas a la misma convención que usa `court_zones`
(0-500 en ambos ejes, ver seed de `schema.sql`) antes de llamar a
`classify_zone`. Así la clasificación en sí queda dirigida por la tabla, no
por reglas duplicadas por fuente.

La conversión en sí (`to_court_coords`) vive aquí y no en cada adaptador
porque las dos fuentes verificadas dan lo MISMO: distancia al aro +
desplazamiento lateral. Lo único propio de cada una es la unidad. Tenerla
duplicada fue justo lo que dejó pasar dos bugs distintos en el mismo mapa de
tiros (Euroliga con el eje de profundidad invertido, ACB con la escala de
profundidad estirada un 76%); con una sola implementación, calibrarla es un
cambio en un sitio.
"""
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from packages.baskonia_core import court_geometry

# `id` de la fila 'Mate' del seed de `court_zones` (`schema.sql`). NO se llega
# a ella por geometría (`classify_zone`): su rectángulo es un único punto que
# cae dentro de 'Pintura', así que el llamador debe asignarla directamente
# para todo tiro `located=False` (ver `ingest/common/raw_game.py`) en vez de
# pasar sus coordenadas por `classify_zone`. Constante y no una consulta a
# `court_zones.label` en cada tiro: es un id estable del seed, no un dato que
# pueda variar entre bases de datos.
MATE_ZONE_ID = 10

# Calibración de la cancha estilizada de `court_zones` (seed de `schema.sql`).
# OJO: esa cancha NO está a escala real, está comprimida a lo ancho — el
# vértice del arco de triple queda a 285 unidades del aro pero las rectas de
# esquina a solo 195 — así que profundidad y lateral tienen escalas DISTINTAS.
# `app/components/court.py` dibuja la línea de triple como elipse con esas dos
# mismas escalas, para que los tiros caigan donde toca respecto a la línea.
#   - aro:      centro de 'Pintura' x su borde de fondo -> (250, 455)
#   - 6,75 m:   línea de triple -> vértice del arco ('Triple exterior'.y_max = 170)
#   - 6,60 m:   recta de esquina -> x de 'Triple esquina izq.'.x_max = 55
# Si se reajusta ese seed, hay que reajustar estas constantes con él.
_HOOP_X, _HOOP_Y = 250.0, 455.0
_DEPTH_SCALE = (_HOOP_Y - 170.0) / 675.0    # unidades de `court_zones` por cm de profundidad
_LATERAL_SCALE = (_HOOP_X - 55.0) / 660.0   # unidades de `court_zones` por cm lateral


def to_court_coords(lateral_cm: float, depth_cm: float) -> tuple:
    """Tiro en cm relativos al aro -> `(pos_x, pos_y)` en la escala de `court_zones`.

    Args:
        lateral_cm: desplazamiento lateral respecto al centro del aro
            (negativo = izquierda del atacante).
        depth_cm: distancia del aro hacia el centro del campo (0 = canasta).
            Nunca se normaliza contra un máximo: es una distancia real, y el
            máximo observable depende del partido (hay tiros desde más allá
            del medio campo). Un tiro lejano sale del dominio 0-500 y lo
            recorta el gráfico (`clip=True`), que es lo correcto — estirar la
            escala para que quepa movería TODOS los demás tiros de sitio.

    Returns:
        `(pos_x, pos_y)`, con la convención de `court_zones`: y ALTO = cerca
        del aro (por eso la profundidad RESTA).
    """
    return _HOOP_X + lateral_cm * _LATERAL_SCALE, _HOOP_Y - depth_cm * _DEPTH_SCALE


# "Ala izq."/"Ala der." (ver `schema.sql`) mezclaban tiros de 2 largos y
# triples de ala en el mismo rectángulo — `classify_zone` las resuelve con un
# segundo paso, ver ahí. Las 4 zonas de referencia con las que arma la elipse
# son las mismas que usa `app/components/court.py::_three_point_ellipse`.
_WING_BASE_LABELS = {"Ala izq.", "Ala der."}
_ELLIPSE_ZONE_LABELS = ("Pintura", "Triple esquina izq.", "Triple esquina der.", "Triple exterior")


def _three_point_ellipse_from_db(conn: Connection):
    """La elipse de `court_geometry.three_point_ellipse`, leída de `court_zones`.

    Mismos 4 nombres de zona que `_ELLIPSE_ZONE_LABELS` — si a alguno le
    falta la fila (BD sin ese seed todavía) devuelve `None` en vez de
    inventar geometría, igual que la función a la que delega.
    """
    rows = {
        r["label"]: r
        for r in conn.execute(
            text("SELECT label, x_min, x_max, y_min, y_max FROM court_zones WHERE label IN :labels").bindparams(
                bindparam("labels", expanding=True)
            ),
            {"labels": list(_ELLIPSE_ZONE_LABELS)},
        ).mappings()
    }
    if not set(_ELLIPSE_ZONE_LABELS) <= rows.keys():
        return None
    return court_geometry.three_point_ellipse(
        rows["Pintura"], rows["Triple esquina izq."], rows["Triple esquina der."], rows["Triple exterior"]
    )


def classify_zone(conn: Connection, pos_x: float, pos_y: float) -> Optional[int]:
    """Devuelve el `zone_id` cuyo rectángulo contiene `(pos_x, pos_y)`, o `None`.

    "Ala izq."/"Ala der." reciben un segundo paso: en cuanto el primer
    `SELECT` (el rectángulo de siempre) cae en una de las dos, se resuelve el
    lado real con la elipse de triple (`court_geometry`, la MISMA que dibuja
    `app/components/court.py::_court_line_layers`) y se devuelve la sub-zona
    "(2)"/"(3)" que toque (filas 14-17 del seed de `schema.sql`) en vez del
    rectángulo mezclado. Sin esa geometría, o sin esas filas todavía en la BD
    (una que aún no ha pasado por `init_scouting_db()` con este seed), se cae
    al `zone_id` del primer paso — nunca se rompe por falta de las filas
    nuevas.
    """
    row = conn.execute(
        text(
            "SELECT id, label FROM court_zones"
            " WHERE :x BETWEEN x_min AND x_max AND :y BETWEEN y_min AND y_max"
            " LIMIT 1"
        ),
        {"x": pos_x, "y": pos_y},
    ).first()
    if row is None:
        return None
    zone_id, label = row
    if label not in _WING_BASE_LABELS:
        return zone_id

    geom = _three_point_ellipse_from_db(conn)
    if geom is None:
        return zone_id
    boundary = court_geometry.wing_boundary_y(pos_x, geom)
    kind = "2" if boundary is not None and pos_y > boundary else "3"
    sub_row = conn.execute(
        text("SELECT id FROM court_zones WHERE label = :label"),
        {"label": f"{label} ({kind})"},
    ).first()
    return sub_row[0] if sub_row is not None else zone_id
