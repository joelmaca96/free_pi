"""Cliente sobre la librería `euroleague_api`.

Métodos y columnas **verificados en vivo** (2026-08-20, `euroleague-api`
instalado, temporada 2025 / gamecode 7) contra el paquete real instalado en
el entorno — no son un supuesto, ver `adapter.py` para las columnas exactas
de cada DataFrame. `Schedule.get_schedule` (no `get_season_schedule`) y
`BoxScoreData.get_players_boxscore_stats` (no `get_player_boxscore_stats_data`)
fueron los dos nombres que había adivinado mal en la primera versión de este
módulo y que rompían en ejecución real; corregidos aquí.

`competition_code` es `"E"` (Euroliga) o `"U"` (Eurocup).

Import perezoso (`_lazy_import`): que `euroleague_api` no esté instalado no
debe romper el resto del proyecto (tests de `parser`/`loader` no lo
necesitan, solo trabajan sobre el contrato interno ya adaptado).
"""
import os
from typing import Any


def _lazy_import():
    try:
        from euroleague_api.boxscore_data import BoxScoreData
        from euroleague_api.game_metadata import GameMetadata
        from euroleague_api.play_by_play_data import PlayByPlay
        from euroleague_api.schedule import Schedule
        from euroleague_api.shot_data import ShotData
    except ImportError as exc:  # pragma: no cover - depende de un extra opcional
        raise ImportError(
            "euroleague_api no está instalado. Instálalo con "
            "`pip install -r ingest/euroleague/requirements.txt`."
        ) from exc
    return BoxScoreData, GameMetadata, PlayByPlay, Schedule, ShotData


DEFAULT_COMPETITION_CODE = os.getenv("EUROLEAGUE_COMPETITION_CODE", "E")  # E=Euroliga, U=Eurocup


class EuroleagueClient:
    """Envoltorio fino sobre `euroleague_api`; cada método devuelve un DataFrame."""

    def __init__(self, competition_code: str = DEFAULT_COMPETITION_CODE):
        BoxScoreData, GameMetadata, PlayByPlay, Schedule, ShotData = _lazy_import()
        self.competition_code = competition_code
        self._schedule = Schedule(competition_code)
        self._boxscore = BoxScoreData(competition_code)
        self._shots = ShotData(competition_code)
        self._metadata = GameMetadata(competition_code)
        self._play_by_play = PlayByPlay(competition_code)

    def fetch_season_game_codes(self, season: int) -> Any:
        """Calendario de la temporada (DataFrame); se filtran los partidos jugados en `pipeline.py`."""
        return self._schedule.get_schedule(season)

    def fetch_game_metadata(self, season: int, game_code: int) -> Any:
        return self._metadata.get_game_metadata(season, game_code)

    def fetch_game_boxscore(self, season: int, game_code: int) -> Any:
        return self._boxscore.get_players_boxscore_stats(season, game_code)

    def fetch_game_shot_data(self, season: int, game_code: int) -> Any:
        return self._shots.get_game_shot_data(season, game_code)

    def fetch_game_play_by_play(self, season: int, game_code: int) -> Any:
        """Play-by-play completo del partido (DataFrame); ver `adapter.py` para las columnas asumidas."""
        return self._play_by_play.get_game_play_by_play_data(season, game_code)
