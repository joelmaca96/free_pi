"""Puente de migración de `config` — Fase F1.

La configuración real vive en `packages/baskonia_core/config.py`. Este fichero
solo reexporta sus nombres para que cualquier referencia externa no migrada
(`import config`) siga funcionando.

Hasta ahora este fichero era una **copia literal** del módulo del dominio, no
un puente: las dos definiciones podían divergir en silencio y, al no llevar el
marcador de abajo, la limpieza de F7 no lo habría detectado.

PUENTE DE MIGRACIÓN — eliminar en F7
"""
from packages.baskonia_core.config import *  # noqa: F401,F403
