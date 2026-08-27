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

from sqlalchemy import text
from sqlalchemy.engine import Connection

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


def classify_zone(conn: Connection, pos_x: float, pos_y: float) -> Optional[int]:
    """Devuelve el `zone_id` cuyo rectángulo contiene `(pos_x, pos_y)`, o `None`."""
    row = conn.execute(
        text(
            "SELECT id FROM court_zones"
            " WHERE :x BETWEEN x_min AND x_max AND :y BETWEEN y_min AND y_max"
            " LIMIT 1"
        ),
        {"x": pos_x, "y": pos_y},
    ).first()
    return row[0] if row is not None else None
