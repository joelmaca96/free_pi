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

CLUBES/ESCUDOS (`fetch_clubs`, 2026-08-24): `euroleague_api` no envuelve
ningún endpoint de clubes (solo calendario/boxscore/tiros/metadata/pbp, ver
arriba) - se pide directo al mismo backend que usa la librería por debajo
(`EuroLeagueData.BASE_URL = "https://api-live.euroleague.net"`, verificado
leyendo el código instalado). Encontrado probando en vivo variantes de ruta
plausibles a partir de ese base URL (sin documentación oficial):
`GET /v2/competitions/{code}/seasons/{code}{season}/clubs` devuelve
`{"data": [...]}`, un objeto por club con `code` (identificador corto
estable, el mismo que usan `homecode`/`awaycode` del calendario y
`CodeTeamA`/`CodeTeamB` del boxscore - NO cambia con el patrocinador, a
diferencia del id numérico de ACB), `name` (con patrocinador, p.ej. "Kosner
Baskonia Vitoria-Gasteiz" en 2026-2027) e `images.crest` (URL del escudo,
confirmada en vivo con varios clubes, `Content-Type: image/png`). Sin
`robots.txt` en ese host (404) - es una API JSON, no una página scrapeada.
"""
import os
from typing import Any, Dict, List

import requests


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
CLUBS_BASE_URL = os.getenv("EUROLEAGUE_API_BASE_URL", "https://api-live.euroleague.net")


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

    def fetch_clubs(self, season: int) -> List[Dict[str, Any]]:
        """Clubes de la temporada (código/nombre/escudo) - ver hallazgo en el docstring del módulo.

        No depende de `_lazy_import`/`euroleague_api` (es una llamada `requests`
        directa), pero vive en este cliente para que `pipeline.py` no tenga que
        conocer la URL del backend.
        """
        url = f"{CLUBS_BASE_URL}/v2/competitions/{self.competition_code}/seasons/{self.competition_code}{season}/clubs"
        response = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
        response.raise_for_status()
        return response.json()["data"]

    def fetch_club_people(self, season: int, club_code: str) -> List[Dict[str, Any]]:
        """Plantilla de un club con su FICHA FÍSICA — ver `ingest/euroleague/roster.py`.

        Mismo backend y mismo patrón de hallazgo que `fetch_clubs` (probando
        rutas plausibles, sin documentación oficial): `GET /v2/competitions/
        {code}/seasons/{code}{season}/clubs/{club}/people` devuelve una LISTA
        (no un `{"data": [...]}` como `clubs`, ojo) con un objeto por miembro
        del club. Verificado en vivo el 2026-09-10 contra `BAS`.

        De cada uno interesa `person`: `code` (id de jugador de Euroliga, el
        mismo espacio que el `Player_ID` del boxscore salvo por el prefijo
        `P` — comprobado, 21 de 23 de la plantilla del Baskonia casaban con
        los `player_external_ids` ya guardados), `height` en centímetros,
        `weight` en kilos, `birthDate` y `country.name`. Es la ÚNICA de las
        tres fuentes del proyecto que publica altura y peso.

        `type == "J"` distingue jugador de cuerpo técnico, igual que
        `team_member_role` en el scraper de baskonia.com.
        """
        url = (
            f"{CLUBS_BASE_URL}/v2/competitions/{self.competition_code}"
            f"/seasons/{self.competition_code}{season}/clubs/{club_code}/people"
        )
        response = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        # Tolerante con las dos formas: lista pelada (lo que devuelve hoy) o
        # envuelta en `{"data": [...]}` como el endpoint de clubes de al lado.
        return payload["data"] if isinstance(payload, dict) else payload

    def fetch_game_metadata(self, season: int, game_code: int) -> Any:
        return self._metadata.get_game_metadata(season, game_code)

    def fetch_game_boxscore(self, season: int, game_code: int) -> Any:
        return self._boxscore.get_players_boxscore_stats(season, game_code)

    def fetch_game_shot_data(self, season: int, game_code: int) -> Any:
        return self._shots.get_game_shot_data(season, game_code)

    def fetch_game_play_by_play(self, season: int, game_code: int) -> Any:
        """Play-by-play completo del partido (DataFrame); ver `adapter.py` para las columnas asumidas."""
        return self._play_by_play.get_game_play_by_play_data(season, game_code)
