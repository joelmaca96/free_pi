"""Servicios de plantilla (roster) de un equipo.

Acceso a la plantilla actual y a la fila de forma reciente de un jugador.
Extraído de `app.py` en la fase F2 de la migración.
"""
from ..db import models
from ..insights import player_recent_form


def team_by_slug(session, slug: str) -> "models.Team | None":
    """Devuelve el equipo con el slug dado, o `None` si no existe."""
    return session.query(models.Team).filter_by(slug=slug).first()


def has_roster(session, team: models.Team) -> bool:
    """Indica si el equipo tiene al menos un jugador guardado en la BD."""
    return session.query(models.Player).filter_by(team_id=team.id).first() is not None


def current_roster(session, team: models.Team) -> list:
    """Jugadores de la plantilla actual, ordenados por dorsal.

    Solo la plantilla oficial de baskonia.com (ver
    `scraper/baskonia_official.py`) rellena `photo_url`; un jugador
    capturado únicamente vía BBR (temporadas o rosters anteriores, ya no en
    el equipo) no tiene foto y no se considera "plantilla actual".
    """
    players = (
        session.query(models.Player)
        .filter_by(team_id=team.id)
        .filter(models.Player.photo_url.isnot(None))
        .all()
    )
    players.sort(key=lambda p: int(p.number) if p.number and p.number.isdigit() else 999)
    return players


def _player_stats_row(
    session,
    team: models.Team,
    player: models.Player,
    last_n: int,
    season: "int | None" = None,
    league: "str | None" = None,
) -> "dict | None":
    """Fila de `player_recent_form` para un jugador concreto, o `None` si no tiene partidos."""
    form = player_recent_form(session, team, last_n=last_n, season=season, league=league)
    return next((r for r in form if r["player_name"] == player.name), None)
