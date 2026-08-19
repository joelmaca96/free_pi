"""CLI de consulta: muestra las estadísticas avanzadas ya guardadas en la BD
para un enfrentamiento, sin hacer ninguna petición de red.

Uso:
    python report.py                          # usa los equipos de config.TEAMS
    python report.py vitoria bilbao           # equipos concretos (slugs de BBR)
    python report.py --last-n 3               # forma reciente sobre 3 partidos en vez de 5
    python report.py --export informe.md      # vuelca el mismo informe a un fichero
"""
import argparse
import sys
from typing import IO, List, Optional

from packages.baskonia_core import config
from packages.baskonia_core.db import models
from packages.baskonia_core.insights import per_36, parse_minutes, player_recent_form, validate_data


def _pct(value: Optional[float]) -> str:
    """Formatea una fracción (0-1) como porcentaje, o '-' si no hay dato."""
    return f"{value * 100:.1f}%" if value is not None else "-"


def _num(value: Optional[float]) -> str:
    """Formatea un número con un decimal, o '-' si no hay dato."""
    return f"{value:.1f}" if value is not None else "-"


def _team_games(session, team: models.Team) -> List[models.Game]:
    """Partidos guardados de un equipo, en el orden en que se capturaron."""
    return (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .order_by(models.Game.id)
        .all()
    )


def _team_stats_for_game(session, game_id: int, team_id: int) -> Optional[models.TeamGameStats]:
    return session.query(models.TeamGameStats).filter_by(game_id=game_id, team_id=team_id).first()


def print_team_summary(session, team: models.Team, out: IO[str]) -> None:
    """Imprime el resumen de partidos y ratings guardados de un equipo."""
    print(f"\n=== {team.name} ({team.slug}) ===", file=out)
    games = _team_games(session, team)
    if not games:
        print("  Sin partidos guardados.", file=out)
        return

    header = f"{'Fecha':<20}{'Rival':<28}{'Resultado':<10}{'Pace':>7}{'ORtg':>8}{'DRtg':>8}{'Net':>8}"
    print(header, file=out)
    print("-" * len(header), file=out)
    for game in games:
        is_home = game.home_team_id == team.id
        rival = game.away_team.name if is_home else game.home_team.name
        team_score = game.home_score if is_home else game.away_score
        opp_score = game.away_score if is_home else game.home_score
        result = f"{team_score}-{opp_score}" if team_score is not None else "-"
        stats = _team_stats_for_game(session, game.id, team.id)
        pace = _num(stats.pace) if stats else "-"
        ortg = _num(stats.off_rating) if stats else "-"
        drtg = _num(stats.def_rating) if stats else "-"
        net = _num(stats.net_rating) if stats else "-"
        print(f"{str(game.date):<20}{rival:<28}{result:<10}{pace:>7}{ortg:>8}{drtg:>8}{net:>8}", file=out)


def print_recent_form(session, team: models.Team, last_n: int, out: IO[str]) -> None:
    """Imprime la forma reciente por jugador (medias de los últimos `last_n` partidos)."""
    print(f"\n=== Forma reciente {team.name} (últimos {last_n} partidos jugados) ===", file=out)
    form = player_recent_form(session, team, last_n=last_n)
    if not form:
        print("  Sin datos suficientes.", file=out)
        return

    header = f"{'Jugador':<28}{'PJ':>4}{'MIN':>7}{'PTS':>7}{'PTS/36':>8}{'eFG%':>8}{'TS%':>8}"
    print(header, file=out)
    print("-" * len(header), file=out)
    for row in form:
        print(
            f"{row['player_name']:<28}{row['games']:>4}{row['avg_minutes']:>7.1f}"
            f"{row['avg_pts']:>7.1f}{_num(row['avg_pts_per36']):>8}"
            f"{_pct(row['avg_efg_pct']):>8}{_pct(row['avg_ts_pct']):>8}",
            file=out,
        )


def print_validation_warnings(session, out: IO[str]) -> None:
    """Imprime avisos de calidad de datos (resultado vs box score, minutos faltantes)."""
    print("\n=== Avisos de calidad de datos ===", file=out)
    warnings = validate_data(session)
    if not warnings:
        print("  Sin incidencias detectadas.", file=out)
        return
    for warning in warnings:
        print(f"  ! {warning}", file=out)


def print_boxscore(session, game: models.Game, team: models.Team, out: IO[str]) -> None:
    """Imprime el box score (PTS, REB, AST, eFG%, TS%, PTS/36) de un equipo en un partido."""
    rows = (
        session.query(models.BoxScore)
        .filter_by(game_id=game.id, team_id=team.id)
        .order_by(models.BoxScore.points.desc())
        .all()
    )
    print(f"  -- {team.name} --", file=out)
    if not rows:
        print("    Sin box score guardado.", file=out)
        return

    print(f"    {'Jugador':<28}{'MIN':>6}{'PTS':>5}{'REB':>5}{'AST':>5}{'PTS/36':>8}{'eFG%':>8}{'TS%':>8}", file=out)
    for row in rows:
        pts = row.points if row.points is not None else "-"
        reb = row.rebounds if row.rebounds is not None else "-"
        ast = row.assists if row.assists is not None else "-"
        pts_per36 = per_36(row.points, parse_minutes(row.minutes))
        print(
            f"    {row.player_name:<28}{row.minutes or '-':>6}{pts!s:>5}{reb!s:>5}{ast!s:>5}"
            f"{_num(pts_per36):>8}{_pct(row.efg_pct):>8}{_pct(row.ts_pct):>8}",
            file=out,
        )


def print_head_to_head(session, team_a: models.Team, team_b: models.Team, out: IO[str]) -> None:
    """Imprime los enfrentamientos directos entre dos equipos, con su box score."""
    games = (
        session.query(models.Game)
        .filter(
            ((models.Game.home_team_id == team_a.id) & (models.Game.away_team_id == team_b.id))
            | ((models.Game.home_team_id == team_b.id) & (models.Game.away_team_id == team_a.id))
        )
        .order_by(models.Game.id)
        .all()
    )
    print(f"\n=== Enfrentamientos directos: {team_a.name} vs {team_b.name} ===", file=out)
    if not games:
        print("  Sin enfrentamientos directos guardados.", file=out)
        return

    for game in games:
        if game.home_score is None:
            print(f"\n{game.date} — {game.home_team.name} vs {game.away_team.name} (pendiente)", file=out)
            continue
        print(
            f"\n{game.date} — {game.home_team.name} {game.home_score} - {game.away_score} {game.away_team.name}",
            file=out,
        )
        stats_home = _team_stats_for_game(session, game.id, game.home_team_id)
        stats_away = _team_stats_for_game(session, game.id, game.away_team_id)
        if stats_home and stats_away:
            print(
                f"  Pace: {_num(stats_home.pace)} | "
                f"{game.home_team.name} ORtg/DRtg/Net: "
                f"{_num(stats_home.off_rating)}/{_num(stats_home.def_rating)}/{_num(stats_home.net_rating)} | "
                f"{game.away_team.name} ORtg/DRtg/Net: "
                f"{_num(stats_away.off_rating)}/{_num(stats_away.def_rating)}/{_num(stats_away.net_rating)}",
                file=out,
            )
        print_boxscore(session, game, game.home_team, out)
        print_boxscore(session, game, game.away_team, out)


def main() -> None:
    """Punto de entrada del CLI."""
    parser = argparse.ArgumentParser(description="Informe de estadísticas guardadas para un enfrentamiento")
    parser.add_argument(
        "teams",
        nargs="*",
        help="Slugs de BBR de los dos equipos a comparar (por defecto, los de config.TEAMS)",
    )
    parser.add_argument(
        "--last-n",
        type=int,
        default=5,
        help="Número de partidos recientes para calcular la forma de cada jugador (default: 5)",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Vuelca el mismo informe a un fichero de texto en vez de (además de) mostrarlo en pantalla",
    )
    args = parser.parse_args()

    team_slugs = args.teams or list(config.TEAMS)
    if len(team_slugs) < 2:
        print("Se necesitan al menos dos equipos (o configura TEAMS en .env).", file=sys.stderr)
        sys.exit(1)

    Session = models.init_db()
    session = Session()
    try:
        team_a = session.query(models.Team).filter_by(slug=team_slugs[0]).first()
        team_b = session.query(models.Team).filter_by(slug=team_slugs[1]).first()
        if team_a is None or team_b is None:
            print(
                "No se encontraron uno o ambos equipos en la base de datos. Ejecuta antes 'python main.py'.",
                file=sys.stderr,
            )
            sys.exit(1)

        out = open(args.export, "w", encoding="utf-8") if args.export else sys.stdout
        try:
            print_team_summary(session, team_a, out)
            print_recent_form(session, team_a, args.last_n, out)
            print_team_summary(session, team_b, out)
            print_recent_form(session, team_b, args.last_n, out)
            print_head_to_head(session, team_a, team_b, out)
            print_validation_warnings(session, out)
        finally:
            if args.export:
                out.close()
                print(f"Informe exportado a {args.export}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
