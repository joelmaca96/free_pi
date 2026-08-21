"""CLI de la fuente Euroliga.

Uso:
    .venv/Scripts/python.exe -m ingest.euroleague.cli --season 2025
"""
import argparse

from ingest.common.db import get_engine
from ingest.common.logging_utils import configure_logging

from .pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Año de inicio de temporada (p.ej. 2025).")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    configure_logging()
    engine = get_engine(args.database_url)
    summary = run(engine, args.season)
    print(f"Euroliga {args.season}: {len(summary['loaded'])} cargados, {len(summary['failed'])} fallidos")


if __name__ == "__main__":
    main()
