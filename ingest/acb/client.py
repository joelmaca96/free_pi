"""Cliente HTTP para la API real de acb.com (`api2.acb.com/api/{seasondata,matchdata}`).

HISTORIA (2026-08-20, dos turnos): la primera versión de este cliente usaba
`api2.acb.com/api/v1/openapilive` con el token público de OpenACB
(`openacb_api/config/seasons.R`) - quedó bloqueada permanentemente con un
409 ("An unhandled error has occurred"): se verificó en vivo replicando
byte a byte la petición que hace el propio scraper R de OpenACB (mismo
token/headers/URL) y da el mismo 409, así que ese backend "openapilive" está
efectivamente muerto para todos, no solo para nosotros.

El usuario capturó tráfico real de red del **frontend actual** de acb.com
(acb.com/live.acb.com, Next.js) con DevTools y encontró la API real que usa
la web hoy en día - **distinta** de la que documenta/usa OpenACB:

    auth        = header `x-apikey: <token>` (NO Bearer/JWT - una request sin
                  ningún otro header además de `x-apikey` funciona igual).
                  Es la misma clave pública que manda cualquier visitante sin
                  loguearse (embebida en el bundle JS del sitio), igual que el
                  token de OpenACB - no es una credencial personal. Se puede
                  sobreescribir con `ACB_API_KEY`.
    seasondata  = https://api2.acb.com/api/seasondata/Competition/matches
                  ?competitionId=1&editionId={edition_id}&weekId={week_id}&isRoundSelected=false
                  Devuelve `{"matches": [...], "teams": [...], "selectedFilters": {...}}`.
                  Sin `weekId`, devuelve la ÚLTIMA jornada disponible de esa
                  edición (para una temporada ya acabada, la final) - se usa
                  como ancla para encontrar el rango de semanas de la
                  temporada, recorriéndolo hacia atrás hasta que la API
                  responde 400 "Week with ID N is not valid or does not
                  exist." (verificado en vivo: edición 90 = temporada
                  2025-2026 abarca weekId 2946-2985, 40 semanas = 34 jornadas
                  liga regular + playoffs).
    matchdata   = https://api2.acb.com/api/matchdata/Result/boxscores?matchId={match_id}
                  Devuelve boxscore completo por equipo/jugador (puntos,
                  tiros de 1/2/3, rebotes off/def, asistencias, robos,
                  pérdidas, tapones, faltas, +/-, minutos "MM:SS", titular).
                  NO se ha encontrado (ni adivinando rutas con el mismo
                  prefijo `Result/...` ni con acceso al repo de OpenACB) un
                  endpoint equivalente de play-by-play/tiro con coordenadas
                  para este backend nuevo - por eso `shots`/`lineups`/
                  `score_progression` quedan vacíos para ACB (huecos
                  conocidos y declarados, igual que `lineups` en Euroliga
                  cuando no hay play-by-play). Si se localiza ese endpoint en
                  el futuro, solo hay que ampliar `fetch_game`/`adapter.py`.

`edition_id`: el mapeo `edition_id = (season + 1) - 1936` viene de
`openacb_api/config/seasons.R` y sigue siendo válido contra la API nueva
(verificado en vivo: `--season 2025` -> `editionId=90`, coincide con el
`id` de la temporada "2025-2026" que devuelve `availableFilters.seasons`).
"""
import os
import time
from typing import Any, Dict, List, Optional

import requests

USER_AGENT = os.getenv(
    "ACB_USER_AGENT",
    "baskonia-scouting-bot/1.0 (+https://github.com/; contacto: scouting@baskonia-pipeline.local)",
)
SEASONDATA_BASE = os.getenv("ACB_SEASONDATA_BASE", "https://api2.acb.com/api/seasondata")
MATCHDATA_BASE = os.getenv("ACB_MATCHDATA_BASE", "https://api2.acb.com/api/matchdata")
REQUEST_DELAY = float(os.getenv("ACB_REQUEST_DELAY", "0.5"))

# Clave pública embebida en el frontend de acb.com (la manda cualquier
# visitante sin loguearse); sobreescribible con ACB_API_KEY si deja de
# funcionar o se prefiere una propia.
API_KEY = os.getenv("ACB_API_KEY", "0dd94928-6f57-4c08-a3bd-b1b2f092976e")

_EDITION_ID_OFFSET = int(os.getenv("ACB_EDITION_ID_OFFSET", "1936"))
COMPETITION_ID = int(os.getenv("ACB_COMPETITION_ID", "1"))  # 1 = Liga Endesa


def season_to_edition_id(season: int) -> int:
    """Convierte un año de inicio de temporada (`2025` = "2025-2026") a `editionId`."""
    return (season + 1) - _EDITION_ID_OFFSET


class _WeekNotFound(Exception):
    """Señal interna: `weekId` fuera del rango de la edición (límite de temporada)."""


class AcbClient:
    """Cliente de red de la fuente ACB."""

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.session.headers.setdefault("x-apikey", API_KEY)
        self._last_request = 0.0
        # Poblados por fetch_season_finished_matches(); fetch_game() los usa
        # para no tener que volver a pedir el calendario por partido.
        self._match_cache: Dict[str, Dict[str, Any]] = {}
        self._season_by_match: Dict[str, int] = {}

    def _get(self, url: str) -> Any:
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        response = self.session.get(url, timeout=30)
        self._last_request = time.monotonic()
        if response.status_code == 400:
            raise _WeekNotFound(response.text)
        response.raise_for_status()
        return response.json()

    def _get_matches_page(self, edition_id: int, week_id: Optional[int]) -> Dict[str, Any]:
        url = (
            f"{SEASONDATA_BASE}/Competition/matches"
            f"?competitionId={COMPETITION_ID}&editionId={edition_id}&isRoundSelected=false"
        )
        if week_id is not None:
            url += f"&weekId={week_id}"
        return self._get(url)

    def fetch_season_finished_matches(self, season: int) -> List[Dict[str, Any]]:
        """Partidos finalizados (`matchStatus == "FINALIZED"`) de una temporada.

        Recorre hacia atrás cada `weekId` de la edición (empezando por la
        última semana disponible) hasta que la API señala el límite de la
        temporada (400) o el `season` devuelto deja de coincidir con
        `edition_id` (semana fuera de rango que la API resuelve haciendo
        fallback a otra edición en vez de dar error).
        """
        edition_id = season_to_edition_id(season)
        anchor = self._get_matches_page(edition_id, week_id=None)
        week_id = anchor["selectedFilters"]["week"]

        matches_by_id: Dict[Any, Dict[str, Any]] = {}
        while True:
            try:
                page = self._get_matches_page(edition_id, week_id)
            except _WeekNotFound:
                break
            if page["selectedFilters"]["season"] != edition_id:
                break
            for match in page["matches"]:
                matches_by_id[match["id"]] = match
            week_id -= 1

        finished = [m for m in matches_by_id.values() if m.get("matchStatus") == "FINALIZED"]
        for match in finished:
            key = str(match["id"])
            self._match_cache[key] = match
            self._season_by_match[key] = season
        return finished

    def fetch_game_boxscore(self, match_id: Any) -> Dict[str, Any]:
        """Boxscore completo (por equipo y jugador) de un partido."""
        return self._get(f"{MATCHDATA_BASE}/Result/boxscores?matchId={match_id}")

    # ---- Compatibilidad con pipeline.py/tests existentes ----

    def fetch_season_game_ids(self, season: int) -> List[str]:
        """IDs de los partidos finalizados de una temporada (envoltorio de `fetch_season_finished_matches`)."""
        return [str(match["id"]) for match in self.fetch_season_finished_matches(season)]

    def fetch_game(self, game_id: str) -> Dict[str, Any]:
        """Contrato común (`ingest.common.raw_game`) para un partido ya finalizado.

        Necesita que `game_id` venga de una llamada previa a
        `fetch_season_finished_matches`/`fetch_season_game_ids` (de ahí saca
        el resumen del partido - equipo local/visitante, marcador, fecha -
        cacheado en memoria); el boxscore por jugador/equipo se pide aquí.
        """
        from .adapter import build_raw_game  # import diferido: evita ciclo con parser.py

        key = str(game_id)
        match = self._match_cache.get(key)
        if match is None:
            raise ValueError(
                f"AcbClient.fetch_game: partido {game_id} desconocido - llama antes a "
                "fetch_season_finished_matches/fetch_season_game_ids."
            )
        boxscore = self.fetch_game_boxscore(game_id)
        return build_raw_game(match, boxscore, self._season_by_match[key])

