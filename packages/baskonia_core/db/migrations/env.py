"""Entorno de ejecución de Alembic para baskonia_core.

Configura Alembic para que autogenere migraciones contra el esquema real de
`packages.baskonia_core.db.models` y use la misma URL que el resto del proyecto
(`config.DATABASE_URL`), de modo que la primera revisión se genera contra el
esquema actual y se marca como aplicada (`alembic stamp head`) en la BD
existente sin recrear tablas.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from packages.baskonia_core import config as core_config
from packages.baskonia_core.db import models

# Alembic Config object: acceso a los valores de alembic.ini.
config = context.config

# Interpretar el fichero de logging de alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# La URL se toma de la configuración del dominio (nunca hardcodeada).
config.set_main_option("sqlalchemy.url", core_config.DATABASE_URL)

# Metadata objetivo para --autogenerate.
target_metadata = models.Base.metadata


def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo 'offline' (genera SQL sin conectar)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Ejecuta migraciones en modo 'online' (conecta al engine)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
