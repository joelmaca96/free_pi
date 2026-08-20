"""CLI para (re)crear la base de datos de scouting desde su esquema versionado.

Ejecuta `packages/baskonia_core/db/scouting/schema.sql` (DDL + datos semilla)
contra la BD apuntada por `config.DATABASE_URL` (o la que se pase con
`--database-url`).

Uso:
    .venv/Scripts/python.exe tools/init_scouting_db.py
    .venv/Scripts/python.exe tools/init_scouting_db.py --force
"""
import argparse
import sys
from pathlib import Path

# Añade la raíz del proyecto al sys.path para poder importar packages.*.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="URL SQLAlchemy destino (por defecto config.DATABASE_URL).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Borra el esquema existente antes de recrearlo.",
    )
    args = parser.parse_args()

    engine = create_scouting_engine(args.database_url)
    init_scouting_db(engine, force=args.force)
    print(f"Base de datos de scouting inicializada en {engine.url}")


if __name__ == "__main__":
    main()
