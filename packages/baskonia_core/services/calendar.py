"""Servicios de calendario de un equipo.

Acceso al calendario de partidos (jugados y pendientes) de un equipo, con los
filtros globales de temporada y competición. Extraído de `app.py` en la fase F2
de la migración: es lógica de negocio que API y Streamlit comparten.
"""
from datetime import datetime, timedelta

from ..dates import parse_bbr_date
from ..db import models
from ..insights import season_start_year


def _team_games(session, team: models.Team, season: "int | None" = None, league: "str | None" = None):
    """Todos los partidos de un equipo (jugados y pendientes), ordenados por fecha.

    Punto único de acceso al calendario de un equipo: aquí se aplican los dos
    filtros globales de la app. `league` es una columna real (`Game.league`), así
    que se filtra en la propia consulta; `season` es derivada de la fecha (ver
    `insights.season_start_year`), así que se filtra en Python tras traer las
    filas. `None` en cualquiera de los dos = sin filtrar por ese eje.
    """
    query = session.query(models.Game).filter(
        (models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id)
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    games = query.all()
    if season is not None:
        games = [g for g in games if season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min)
    return games


def _rival_of(game: models.Game, team: models.Team) -> models.Team:
    """Devuelve el equipo rival de `team` en un partido dado."""
    return game.away_team if game.home_team_id == team.id else game.home_team


def _result_label(game: models.Game, team: models.Team) -> str:
    """Resultado del partido desde el punto de vista de `team`, o 'pendiente' si no se ha jugado."""
    is_home = game.home_team_id == team.id
    team_score = game.home_score if is_home else game.away_score
    opp_score = game.away_score if is_home else game.home_score
    if team_score is None:
        return "pendiente"
    return f"{team_score}-{opp_score}"


def past_games(session, team: models.Team, season: "int | None" = None, league: "str | None" = None) -> list:
    """Partidos ya jugados de un equipo (con resultado), del más reciente al más antiguo.

    `season`/`league` acotan a la temporada/competición seleccionadas; `None` en
    cualquiera de los dos no filtra por ese eje.
    """
    query = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .filter(models.Game.home_score.isnot(None))
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    games = query.all()
    if season is not None:
        games = [g for g in games if season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min, reverse=True)
    return games


def upcoming_games(session, team: models.Team) -> list:
    """Próximos partidos de un equipo (sin resultado todavía), del más cercano al más lejano.

    Un partido sin resultado no siempre es "próximo": BBR deja para siempre
    sin resultado la fila de un partido aplazado, aunque se haya jugado más
    adelante en otra fecha como fila aparte. Se excluyen las fechas ya
    pasadas (por si hay algún caso similar sin anotar), para no mezclar
    partidos de la temporada ya cerrada con los realmente pendientes.
    """
    games = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .filter(models.Game.home_score.is_(None))
        .all()
    )
    today = datetime.now()
    games = [g for g in games if (parse_bbr_date(g.date) or datetime.max) >= today]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.max)
    return games


def games_in_window(
    session, team: models.Team, window_days: int, reference_date: "datetime | None" = None
) -> list:
    """Partidos ya JUGADOS de `team` dentro de los últimos `window_days`
    días respecto a `reference_date` (por defecto, ahora).

    Se llama a `past_games` sin temporada ni competición a propósito: la ventana
    de días ya es un filtro temporal más preciso, y la carga física de un jugador
    es transversal a la competición (un partido de ACB y otro de Euroliga en la
    misma semana cansan igual).
    """
    reference_date = reference_date or datetime.now()
    cutoff = reference_date - timedelta(days=window_days)
    return [
        g for g in past_games(session, team)
        if cutoff <= (parse_bbr_date(g.date) or datetime.min) <= reference_date
    ]
