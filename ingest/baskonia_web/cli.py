"""CLI de la fuente de plantilla/fotos de baskonia.com.

Uso:
    .venv/Scripts/python.exe -m ingest.baskonia_web.cli
"""
import argparse

from ingest.common.db import get_engine
from ingest.common.logging_utils import configure_logging

from .pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    configure_logging()
    engine = get_engine(args.database_url)
    summary = run(engine)
    print(f"baskonia.com: {len(summary['active'])} activos, {len(summary['deactivated'])} desactivados")


if __name__ == "__main__":
    main()
