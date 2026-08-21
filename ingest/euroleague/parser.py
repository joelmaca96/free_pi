"""Delegación al parser común para partidos ya adaptados (`ingest.euroleague.adapter`)."""
from typing import Any, Dict

from sqlalchemy.engine import Connection

from ingest.common.raw_game import parse_and_resolve as _parse_and_resolve
from ingest.common.schema_types import NormalizedGame

SOURCE = "euroleague"


def parse_and_resolve(conn: Connection, raw: Dict[str, Any]) -> NormalizedGame:
    return _parse_and_resolve(conn, raw, SOURCE)
