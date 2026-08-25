"""Capa de lectura de la interfaz sobre la base de datos de scouting.

Único punto de la app que habla SQL — igual que `ingest/common/loader.py` es
el único punto de escritura. Ningún módulo de `app/pages/` construye consultas
sueltas; todo pasa por `db.py` (engine) y `queries.py` (funciones cacheadas).
"""
