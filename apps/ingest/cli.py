"""CLI de entrada del pipeline de ingestión (apps/ingest).

Expone los mismos flags que el histórico `python main.py` para que la
documentación, el `cron` y la memoria muscular sigan funcionando durante la
transición de la migración (F4). El puente `main.py` de la raíz delega aquí.
"""
import argparse

from .pipeline import backfill_league, backfill_season, run, scout_team


def main() -> None:
    """Punto de entrada del CLI: parsea flags y delega en pipeline.py."""
    arg_parser = argparse.ArgumentParser(description="Pipeline de scraping de Basketball-Reference")
    arg_parser.add_argument(
        "--refresh-teams",
        action="store_true",
        help="Vuelve a descargar roster y calendario de los equipos aunque ya existan en la base de datos",
    )
    arg_parser.add_argument(
        "--fix-league",
        action="store_true",
        help=(
            "Backfill de Game.league: hace copia de seguridad de la BD, vuelve a descargar el "
            "calendario de los equipos con roster propio (vitoria/bilbao/gran-canaria hoy) y "
            "corrige la competición real de cada partido ya persistido. No ejecuta el resto del "
            "pipeline."
        ),
    )
    arg_parser.add_argument(
        "--backfill-season",
        type=int,
        metavar="AÑO",
        help=(
            "Completa los partidos de una temporada histórica del Baskonia (p.ej. 2025 para "
            "2025-26) en las 4 competiciones (Euroliga, ACB, Copa del Rey, Supercopa) usando "
            "RealGM como fuente principal y BBR/CMS/ACB como backup. Hace copia de seguridad de "
            "la BD antes de escribir. No ejecuta el resto del pipeline."
        ),
    )
    arg_parser.add_argument(
        "--scout-team",
        metavar="EQUIPO",
        help=(
            "Descarga puntualmente y cachea los partidos de un equipo rival (no solo Baskonia) "
            "para evaluar su estado de forma. Acepta nombre o slug (p.ej. 'Real Madrid' o "
            "'real-madrid'). Usa la temporada de --scout-season (por defecto SCOUT_SEASON). "
            "No ejecuta el resto del pipeline."
        ),
    )
    arg_parser.add_argument(
        "--scout-season",
        type=int,
        metavar="AÑO",
        help="Temporada a usar para --scout-team (por defecto, SCOUT_SEASON).",
    )
    args = arg_parser.parse_args()

    if args.fix_league:
        backfill_league()
    elif args.backfill_season is not None:
        backfill_season(args.backfill_season)
    elif args.scout_team:
        from packages.baskonia_core import config

        season = args.scout_season if args.scout_season is not None else config.SCOUT_SEASON
        scout_team(args.scout_team, season)
    else:
        run(refresh_teams=args.refresh_teams)


if __name__ == "__main__":
    main()
