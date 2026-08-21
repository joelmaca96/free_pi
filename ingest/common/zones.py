"""Clasificación de tiros en zonas de cancha (`court_zones`).

Cada fuente tiene su propio sistema de coordenadas de tiro (ACB, Euroliga...);
el parser de cada módulo de ingesta es responsable de convertir sus
coordenadas nativas a la misma convención que usa `court_zones` (0-500 en
ambos ejes, ver seed de `schema.sql`) antes de llamar a `classify_zone`. Así
la clasificación en sí queda dirigida por la tabla, no por reglas duplicadas
por fuente.
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection


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
