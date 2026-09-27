"""CLI de la fuente Euroliga.

Uso:
    .venv/Scripts/python.exe -m ingest.euroleague.cli --season 2025
    .venv/Scripts/python.exe -m ingest.euroleague.cli --season 2026 --upcoming-only
    .venv/Scripts/python.exe -m ingest.euroleague.cli --season 2025 --roster-only
"""
import argparse

from ingest.common.db import get_engine
from ingest.common.logging_utils import configure_logging

from .pipeline import run, run_upcoming
from .roster import run as run_roster


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Año de inicio de temporada (p.ej. 2025).")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--upcoming-only", action="store_true",
        help="Solo refresca el calendario futuro (upcoming_matchups) y los escudos, sin backfill de "
        "partidos jugados — para una temporada que aún no ha empezado (p.ej. 2026 = 2026-2027) es lo "
        "único que hay que pedir, run() no encontraría ningún partido jugado todavía.",
    )
    parser.add_argument(
        "--roster-only", action="store_true",
        help="Solo la ficha de jugador (altura, peso, nacimiento, nacionalidad y foto), sin tocar "
        "partidos — ver ingest/euroleague/roster.py.",
    )
    parser.add_argument(
        "--skip-photos", action="store_true",
        help="Con --roster-only: no descargar los headshots (la primera pasada son varios cientos "
        "de peticiones al CDN).",
    )
    args = parser.parse_args()

    configure_logging()
    engine = get_engine(args.database_url)

    if args.roster_only:
        summary = run_roster(engine, args.season, download_photos=not args.skip_photos)
        print(
            f"Euroliga {args.season} (ficha): {summary['players']} jugadores actualizados de "
            f"{summary['clubs']} clubes, {summary['photos']} fotos nuevas"
        )
        return

    if args.upcoming_only:
        summary = run_upcoming(engine, args.season)
        print(f"Euroliga {args.season} (calendario futuro): {summary['upcoming']} partidos cargados")
        return

    summary = run(engine, args.season)
    print(f"Euroliga {args.season}: {len(summary['loaded'])} cargados, {len(summary['failed'])} fallidos")


if __name__ == "__main__":
    main()
