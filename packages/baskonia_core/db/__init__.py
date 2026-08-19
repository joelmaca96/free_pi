"""Subpaquete de modelo de datos y almacenamiento.

Reexporta `models` (esquema SQLAlchemy) y `storage` (upserts idempotentes)
para que `from packages.baskonia_core.db import models` funcione.
"""
from . import models, storage

__all__ = ["models", "storage"]
