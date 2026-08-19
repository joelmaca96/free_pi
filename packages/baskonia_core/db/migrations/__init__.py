"""Migraciones de esquema (Alembic) para baskonia_core.

Sustituye a `models._add_missing_columns()`: el esquema pasa a estar versionado
y reproducible en SQLite y en cualquier motor futuro (PostgreSQL). La primera
revisión se genera con `--autogenerate` contra el esquema actual y se marca como
aplicada (`alembic stamp head`) en la BD existente — no se recrean tablas.
"""
