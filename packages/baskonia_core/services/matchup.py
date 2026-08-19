"""Servicios de enfrentamientos directos (head-to-head) entre dos equipos.

Extraído de `app.py` en la fase F2 de la migración.
"""
from datetime import datetime

from ..dates import parse_bbr_date
from ..db import models
from ..insights import season_start_year


def head_to_head_games(
    session,
    team_a: models.Team,
    team_b: models.Team,
    season: "int | None" = None,
    league: "str | None" = None,
):
    """Enfrentamientos directos entre dos equipos, ordenados por fecha.

    No distingue jugado/pendiente (nunca lo hizo): un enfrentamiento ya
    programado de la temporada seleccionada aparece con el marcador sin rellenar.
    """
    query = session.query(models.Game).filter(
        ((models.Game.home_team_id == team_a.id) & (models.Game.away_team_id == team_b.id))
        | ((models.Game.home_team_id == team_b.id) & (models.Game.away_team_id == team_a.id))
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    games = query.all()
    if season is not None:
        games = [g for g in games if season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min)
    return games
