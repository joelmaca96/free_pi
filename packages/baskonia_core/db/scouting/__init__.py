"""Esquema y acceso a la base de datos de scouting.

Ver `schema.sql` para el DDL completo (equipos, jugadores, partidos,
estadísticas avanzadas, tiros, quintetos, próximos rivales) y `engine.py`
para la creación del engine SQLAlchemy y la carga del esquema.
"""
from .engine import create_scouting_engine, init_scouting_db, is_initialized

__all__ = [
    "create_scouting_engine",
    "init_scouting_db",
    "is_initialized",
]
