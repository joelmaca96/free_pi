"""Adapta el JSON crudo de un partido ACB al contrato común (`ingest.common.raw_game`).

Coordenadas de tiro: OpenACB convierte a coordenadas FIBA de -7.3 a 7.3 m en
el eje horizontal (`05_shot_charts.R`); asumimos la misma convención (X en
[-7.3, 7.3] m, Y en [0, 14] m de media cancha) y la reescalamos al rango
0-500 que usa `court_zones`. Si la fuente real usa otra convención, ajustar
solo `_meters_to_court_scale`. Ver `client.py` para el resto de supuestos
sobre el payload real de `api2.acb.com` (aún sin verificar en vivo).

Lineups: `raw["play_by_play"]` (jugadas de sustitución/canasta) y
`raw["players"][*]["starter"]` (quinteto titular) se reenvían tal cual al
parser común (`ingest.common.raw_game`), que reconstruye los quintetos
jugada a jugada (`ingest.common.lineups.reconstruct_lineups`) si estos datos
vienen en el payload de `fetch_game`; si no vienen, `game.lineups` queda
vacío en vez de inventarse algo.
"""
from typing import Any, Dict

from sqlalchemy.engine import Connection

from ingest.common.raw_game import parse_and_resolve as _parse_and_resolve
from ingest.common.schema_types import NormalizedGame

SOURCE = "acb"


def _meters_to_court_scale(x_m: float, y_m: float) -> tuple:
    """Reescala coordenadas FIBA (X: -7.3..7.3 m, Y: 0..14 m) al rango 0-500."""
    x = (x_m + 7.3) / 14.6 * 500
    y = y_m / 14.0 * 500
    return x, y


def parse_and_resolve(conn: Connection, raw: Dict[str, Any]) -> NormalizedGame:
    """Convierte las coordenadas de tiro de ACB (metros FIBA) y delega en el parser común."""
    converted = dict(raw)
    converted["shots"] = [
        {**shot, "x": _meters_to_court_scale(shot["x_m"], shot["y_m"])[0],
         "y": _meters_to_court_scale(shot["x_m"], shot["y_m"])[1]}
        for shot in raw.get("shots", [])
    ]
    return _parse_and_resolve(conn, converted, SOURCE)
