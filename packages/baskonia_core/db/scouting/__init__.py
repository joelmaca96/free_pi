"""Esquema y acceso a datos de la nueva base de datos de scouting.

Reemplaza al modelo de datos de scraping (`packages.baskonia_core.db.models`)
para el caso de uso de scouting (plantilla, partidos, próximos rivales). Ver
`schema.sql` (copia versionada de `local/features/scouting_baskonia_schema.sql`)
y `repository.py` para las consultas que necesita la UI.
"""
from .engine import create_scouting_engine, init_scouting_db, is_initialized
from .repository import ScoutingRepository

__all__ = [
    "create_scouting_engine",
    "init_scouting_db",
    "is_initialized",
    "ScoutingRepository",
]
