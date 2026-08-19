"""Servicios de dominio de baskonia_core.

Agrupa la lógica de negocio extraída de la capa de UI (`app.py`) en la fase F2 de
la migración: calendario, plantilla, enfrentamientos directos y box scores. API y
Streamlit comparten estos servicios en vez de duplicar el comportamiento.

Reexporta también los nombres privados (`_team_games`, `_team_stats_for_game`,
`_rival_of`, `_result_label`, `_player_stats_row`) que `app.py` sigue usando, para
que la migración no cambie el contrato de importación.
"""
from .boxscore import _team_stats_for_game, boxscore_rows
from .calendar import (
    _result_label,
    _rival_of,
    _team_games,
    games_in_window,
    past_games,
    upcoming_games,
)
from .matchup import head_to_head_games
from .roster import _player_stats_row, current_roster, has_roster, team_by_slug

__all__ = [
    "_result_label",
    "_rival_of",
    "_team_games",
    "_team_stats_for_game",
    "_player_stats_row",
    "boxscore_rows",
    "current_roster",
    "games_in_window",
    "has_roster",
    "head_to_head_games",
    "past_games",
    "team_by_slug",
    "upcoming_games",
]
