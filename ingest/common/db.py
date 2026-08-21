"""Acceso a la base de datos de scouting para los módulos de ingesta.

Reexporta el engine de `packages.baskonia_core.db.scouting` para que cada
módulo de ingesta (acb/euroleague/baskonia_web) abra la misma BD sin tener
que conocer la ruta de import completa.
"""
from typing import Optional

from sqlalchemy.engine import Engine

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Engine sobre la BD de scouting, creando el esquema si hiciera falta."""
    engine = create_scouting_engine(database_url)
    init_scouting_db(engine)
    return engine
