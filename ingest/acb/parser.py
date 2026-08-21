"""Delegación al parser común para partidos ya adaptados (`ingest.acb.adapter`).

Las coordenadas de tiro (mm reales de acb.com -> escala 0-500 de
`court_zones`) ya vienen convertidas desde `adapter.py` - no hay conversión
que hacer aquí (a diferencia de una versión anterior de este archivo, que
asumía un contrato con `x_m`/`y_m` para una fuente aún no verificada).
"""
from typing import Any, Dict

from sqlalchemy.engine import Connection

from ingest.common.raw_game import parse_and_resolve as _parse_and_resolve
from ingest.common.schema_types import NormalizedGame

SOURCE = "acb"


def parse_and_resolve(conn: Connection, raw: Dict[str, Any]) -> NormalizedGame:
    return _parse_and_resolve(conn, raw, SOURCE)
