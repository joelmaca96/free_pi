"""Puente de migración — Fase F1.

Único fichero puente de la migración a `packages/baskonia_core`. Reexporta la
API pública del dominio para cualquier referencia externa no migrada.

PUENTE DE MIGRACIÓN — eliminar en F7
"""
from packages.baskonia_core.config import *   # noqa: F401,F403
from packages.baskonia_core.stats import *    # noqa: F401,F403
from packages.baskonia_core.insights import * # noqa: F401,F403
from packages.baskonia_core.db import models, storage  # noqa: F401
