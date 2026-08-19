"""Servicios de box score y estadísticas por partido.

Acceso a las estadísticas avanzadas de un equipo en un partido (`TeamGameStats`)
y a las filas de box score de un equipo en un partido. Extraído de `app.py` en la
fase F2 de la migración.
"""
from ..db import models


def _team_stats_for_game(session, game_id: int, team_id: int):
    return session.query(models.TeamGameStats).filter_by(game_id=game_id, team_id=team_id).first()


def boxscore_rows(session, game_id: int, team_id: int) -> list:
    """Filas de box score de un equipo en un partido, ordenadas por puntos desc."""
    return (
        session.query(models.BoxScore)
        .filter_by(game_id=game_id, team_id=team_id)
        .order_by(models.BoxScore.points.desc())
        .all()
    )
