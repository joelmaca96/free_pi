"""CLI de la fuente ACB.

Uso:
    .venv/Scripts/python.exe -m ingest.acb.cli --season 2025
    .venv/Scripts/python.exe -m ingest.acb.cli --season 2026 --upcoming-only
"""
import argparse

from ingest.common.db import get_engine
from ingest.common.logging_utils import configure_logging

from .pipeline import run, run_upcoming


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Año de inicio de temporada (p.ej. 2025).")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--upcoming-only", action="store_true",
        help="Solo refresca el calendario futuro (upcoming_matchups), sin backfill de partidos "
        "jugados — para una temporada que aún no ha empezado (p.ej. 2026 = 2026-2027) es lo único "
        "que hay que pedir, run() no encontraría ningún partido finalizado todavía.",
    )
    args = parser.parse_args()

    configure_logging()
    engine = get_engine(args.database_url)

    if args.upcoming_only:
        summary = run_upcoming(engine, args.season)
        print(f"ACB {args.season} (calendario futuro): {summary['upcoming']} partidos cargados")
        return

    summary = run(engine, args.season)
    print(f"ACB {args.season}: {len(summary['loaded'])} cargados, {len(summary['failed'])} fallidos")


if __name__ == "__main__":
    main()
