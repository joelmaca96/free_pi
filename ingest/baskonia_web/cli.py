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
    parser.add_argument(
        "--skip-photos", action="store_true",
        help="No descargar las fotos de los jugadores a disco, solo actualizar players/dorsales/bio.",
    )
    args = parser.parse_args()

    configure_logging()
    engine = get_engine(args.database_url)
    summary = run(engine, download_photos=not args.skip_photos)
    print(f"baskonia.com: {len(summary['active'])} activos, {len(summary['deactivated'])} desactivados")


if __name__ == "__main__":
    main()
