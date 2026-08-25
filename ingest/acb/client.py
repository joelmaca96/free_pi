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
    shots       = https://api2.acb.com/api/matchdata/MatchShots/match-shots?matchId={match_id}
                  Devuelve `shotPoints`: un tiro por fila con `posX`/`posY`
                  (mm, ver `adapter.py` para la conversión), `playType`
                  (código numérico - ver mapeo verificado en `adapter.py`),
                  `quarter`/`minute`/`second`, `local` (bool: true=home),
                  `scoreHome`/`scoreAway`, `playerLicenseId` (mismo espacio
                  de ids que `boxscores`). Solo los tiros de campo (2/3,
                  hechos o fallados) traen coordenadas reales; los tiros
                  libres vienen con `posX=posY=0` (se descartan para `shots`,
                  que exige coordenadas).
    play-by-play = https://api2.acb.com/api/matchdata/PlayByPlay/play-by-play?matchId={match_id}
                  Devuelve `plays`: TODAS las jugadas (no solo tiros),
                  ordenadas cronológicamente por el campo `order` (no por
                  quarter/minute/second sueltos). `playType` clave para
                  reconstruir quintetos: `599`=quinteto inicial (10 eventos,
                  5 por equipo, al principio del partido), `112`=entra a
                  pista, `115`=sale de pista (verificado en vivo cruzando
                  eventos de sustitución reales con el quinteto inicial -
                  ver el turno en que se decodificó). También trae
                  `scoreHome`/`scoreAway` en cada jugada, más granular que
                  `shotPoints` (678 eventos vs 201 tiros) - se usa para
                  `score_progression`.
    Encontrados verificando en vivo con capturas de DevTools reales del
    usuario en la página "resumen" (carta de tiro) y la pestaña "jugadas"
    (play-by-play) de un partido - no adivinados por prueba y error como los
    intentos anteriores de `Result/{name}`.
    advanced-stats = https://api2.acb.com/api/matchdata/AdvancedStats/match-advanced-stats?matchId={match_id}
                  Devuelve `homeAdvancedStats`/`awayAdvancedStats`: el
                  cálculo OFICIAL de acb.com (no una aproximación nuestra)
                  de posesiones/pace/`fourFactors` (efgPct/orbPct/tovPct/
                  fTr)/`ratings` (oer/der/netRating)/`ballHandling`
                  (astPct/stlPct/blkPct)/`shooting.tsPct`, cada uno con
                  contexto de temporada (`partido`/`temporada`/`win`/`loss`).
                  Se usa con preferencia sobre `_estimate_possessions` de
                  `adapter.py` cuando está disponible (ver `build_raw_game`).
                  Encontrado junto con el resto de esta lista, capturado por
                  el usuario en `doc/acb_endpoints.md` (incluye más
                  endpoints de la pestaña "resumen" no integrados aún -
                  `Overview/lead-tracker`, `Overview/lineup`,
                  `Overview/match-team-comparison`, `Overview/match-leaders`,
                  `MatchHeader/match-header` - redundantes con datos que ya
                  cargamos por otra vía o de menor prioridad; revisar ese
                  fichero si se necesita alguno en el futuro).

`edition_id`: el mapeo `edition_id = (season + 1) - 1936` viene de
`openacb_api/config/seasons.R` y sigue siendo válido contra la API nueva
(verificado en vivo: `--season 2025` -> `editionId=90`, coincide con el
`id` de la temporada "2025-2026" que devuelve `availableFilters.seasons`).
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

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

# Cuántos `weekId` inválidos SEGUIDOS se toleran antes de dar la temporada por
# terminada (ver historia en `fetch_season_finished_matches`) - el hueco más
# ancho verificado en vivo fue de 55 semanas, este margen lo cubre sin
# arriesgarse a recorrer cientos de semanas de más en cada llamada.
_MAX_CONSECUTIVE_GAPS = int(os.getenv("ACB_MAX_CONSECUTIVE_WEEK_GAPS", "80"))

# Techo absoluto de `weekId` visitados por llamada, independiente de
# `_MAX_CONSECUTIVE_GAPS`: verificado en vivo que semanas MUY antiguas (fuera
# de cualquier temporada real) pueden seguir devolviendo 200 con partidos en
# vez de 400 (`selectedFilters.season` no lo distingue, ver historia arriba) -
# sin este techo, una racha de huecos válidos-pero-vacíos podría alargar el
# recorrido mucho más de lo que cualquier temporada real necesita (~120
# semanas entre liga regular/playoffs/huecos vistos hasta ahora).
_MAX_TOTAL_WEEKS_WALKED = int(os.getenv("ACB_MAX_TOTAL_WEEKS_WALKED", "300"))


def season_to_edition_id(season: int) -> int:
    """Convierte un año de inicio de temporada (`2025` = "2025-2026") a `editionId`."""
    return (season + 1) - _EDITION_ID_OFFSET


def _season_date_bounds(season: int) -> tuple:
    """Ventana de fechas (`YYYY-MM-DD`, `YYYY-MM-DD`) razonable para una temporada.

    Red de seguridad de `fetch_season_finished_matches`: `selectedFilters.season`
    no valida nada de verdad (ver historia ahí), así que sin esto tolerar
    huecos de `weekId` podría arrastrar partidos de temporadas ajenas si el
    hueco real resultara más ancho que `_MAX_CONSECUTIVE_GAPS`. Julio de
    `season` a agosto de `season + 1` cubre pretemporada y playoffs con margen.
    """
    return f"{season}-07-01", f"{season + 1}-08-31"


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

        HISTORIA (2026-08-24): la primera versión de este método paraba en el
        PRIMER `weekId` inválido (400) al recorrer hacia atrás desde la última
        semana disponible, asumiendo un rango contiguo. Falso: verificado en
        vivo que el espacio de `weekId` de una misma edición tiene huecos
        reales de decenas de semanas seguidas devolviendo 400 (p.ej. edición
        90: semanas 2891-2945 inválidas, pero 2810-2890 vuelven a ser válidas
        y tienen partidos reales - un partido de Baskonia de abril,
        `matchId=104679`, quedaba así fuera del calendario cargado). Tampoco
        sirve `selectedFilters.season` como señal de límite: se comprobó que
        devuelve el `edition_id` pedido incluso en `weekId` claramente ajenos
        a la temporada (huecos de decenas/cientos de semanas más atrás) - no
        valida nada del lado del servidor, solo hace eco del parámetro.

        Por eso ahora: (1) tolera hasta `_MAX_CONSECUTIVE_GAPS` semanas
        inválidas SEGUIDAS antes de dar la temporada por terminada (en vez de
        pararse en la primera), para saltar huecos como el de arriba; (2)
        como red de seguridad ante que el recorrido se alargue más de la
        cuenta hacia semanas realmente ajenas a la temporada, descarta
        cualquier partido cuya fecha caiga fuera de una ventana amplia
        alrededor de `season` (`_season_date_bounds`) - sin esto, un hueco
        más ancho que el ya visto arrastraría partidos de otras temporadas;
        (3) `_MAX_TOTAL_WEEKS_WALKED` como techo absoluto de peticiones, por
        si semanas antiguas ajenas a cualquier temporada siguen devolviendo
        200 en vez de 400 (verificado en vivo que puede pasar).
        """
        edition_id = season_to_edition_id(season)
        anchor = self._get_matches_page(edition_id, week_id=None)
        week_id = anchor["selectedFilters"]["week"]
        date_min, date_max = _season_date_bounds(season)

        matches_by_id: Dict[Any, Dict[str, Any]] = {}
        consecutive_gaps = 0
        weeks_walked = 0
        while consecutive_gaps <= _MAX_CONSECUTIVE_GAPS and weeks_walked < _MAX_TOTAL_WEEKS_WALKED:
            weeks_walked += 1
            try:
                page = self._get_matches_page(edition_id, week_id)
            except _WeekNotFound:
                consecutive_gaps += 1
                week_id -= 1
                continue
            consecutive_gaps = 0
            for match in page["matches"]:
                if date_min <= str(match.get("startDateTime", ""))[:10] <= date_max:
                    matches_by_id[match["id"]] = match
            week_id -= 1

        finished = [m for m in matches_by_id.values() if m.get("matchStatus") == "FINALIZED"]
        for match in finished:
            key = str(match["id"])
            self._match_cache[key] = match
            self._season_by_match[key] = season
        return finished

    def fetch_season_scheduled_matches(self, season: int) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """Calendario NO finalizado (`matchStatus != "FINALIZED"`) de una temporada.

        VERIFICADO EN VIVO (2026-08-24): a diferencia de una temporada ya jugada, la
        semana ancla (`weekId` sin especificar) de una temporada que **todavía no ha
        empezado** no cae cerca del final del calendario sino cerca del PRINCIPIO -
        edición 91 (temporada 2026-2027, sin un solo partido finalizado todavía): la
        ancla devolvió `weekId=2987` con partidos del 26-27/09/2026 (la primera
        jornada), y siguió habiendo semanas válidas hacia ADELANTE (`weekId` hasta
        ~3020) con partidos programados hasta mediados de mayo de 2027 (35 semanas ≈
        34 jornadas de liga regular, mismo patrón que una temporada completa). Por
        eso este método recorre la ancla en AMBAS direcciones (a diferencia de
        `fetch_season_finished_matches`, que solo recorre hacia atrás porque ahí la
        ancla sí es el final) - con la misma tolerancia a huecos de `weekId` ya
        verificada (ver historia de ese método), no se puede asumir de nuevo que el
        rango es contiguo.

        Returns:
            Tupla `(matches, teams_by_id)`: `matches` son los partidos con
            `matchStatus != "FINALIZED"` (calendario futuro; para una temporada sin
            empezar, prácticamente todos). `teams_by_id` mapea el id numérico de
            equipo de ACB (`str`) a `{"name": ..., "logo_url": ...}` de esta edición
            (p.ej. `"Kosner Baskonia"` en 2026-2027) - viene del mismo payload, así
            que `adapter.build_scheduled_matchup` no necesita una llamada aparte
            para resolver nombre/escudo del rival. `logo_url` es el campo `logo`
            real que trae ACB (verificado en vivo, 2026-08-24:
            `https://static.acb.com/img/www/clubes2024/...png`) - primera fuente
            de escudo de equipo verificada en el proyecto (ver
            `local/features/003-vista-plantilla/01_design.md` §9, que documentaba
            este hueco como sin resolver).
        """
        edition_id = season_to_edition_id(season)
        anchor = self._get_matches_page(edition_id, week_id=None)
        anchor_week = anchor["selectedFilters"]["week"]
        date_min, date_max = _season_date_bounds(season)

        matches_by_id: Dict[Any, Dict[str, Any]] = {}
        teams_by_id: Dict[str, Dict[str, Any]] = {}

        def _collect(page: Dict[str, Any]) -> None:
            for match in page["matches"]:
                if date_min <= str(match.get("startDateTime", ""))[:10] <= date_max:
                    matches_by_id[match["id"]] = match
            for team in page.get("teams", []):
                teams_by_id[str(team["id"])] = {
                    "name": team.get("shortName") or team.get("fullName") or str(team["id"]),
                    "logo_url": team.get("logo"),
                }

        _collect(anchor)

        for step in (-1, 1):
            week_id = anchor_week + step
            consecutive_gaps = 0
            weeks_walked = 0
            while consecutive_gaps <= _MAX_CONSECUTIVE_GAPS and weeks_walked < _MAX_TOTAL_WEEKS_WALKED:
                weeks_walked += 1
                try:
                    page = self._get_matches_page(edition_id, week_id)
                except _WeekNotFound:
                    consecutive_gaps += 1
                    week_id += step
                    continue
                consecutive_gaps = 0
                _collect(page)
                week_id += step

        scheduled = [m for m in matches_by_id.values() if m.get("matchStatus") != "FINALIZED"]
        return scheduled, teams_by_id

    def fetch_game_boxscore(self, match_id: Any) -> Dict[str, Any]:
        """Boxscore completo (por equipo y jugador) de un partido."""
        return self._get(f"{MATCHDATA_BASE}/Result/boxscores?matchId={match_id}")

    def fetch_game_shots(self, match_id: Any) -> Dict[str, Any]:
        """Tiros con coordenadas de un partido (`shotPoints`, ver `adapter.py`)."""
        return self._get(f"{MATCHDATA_BASE}/MatchShots/match-shots?matchId={match_id}")

    def fetch_game_play_by_play(self, match_id: Any) -> Dict[str, Any]:
        """Play-by-play completo de un partido (`plays`, ver `adapter.py`)."""
        return self._get(f"{MATCHDATA_BASE}/PlayByPlay/play-by-play?matchId={match_id}")

    def fetch_game_advanced_stats(self, match_id: Any) -> Dict[str, Any]:
        """Estadísticas avanzadas OFICIALES del partido (`homeAdvancedStats`/`awayAdvancedStats`).

        A diferencia de `_estimate_possessions` (aproximación Dean Oliver en
        `adapter.py`), esto es el cálculo real que hace acb.com (posesiones,
        pace, ortg/drtg/net_rating, four factors...) - se usa con
        preferencia sobre la estimación propia cuando está disponible.
        """
        return self._get(f"{MATCHDATA_BASE}/AdvancedStats/match-advanced-stats?matchId={match_id}")

    def fetch_match_header(self, match_id: Any) -> Dict[str, Any]:
        """Cabecera del partido (`competitionId`/marcador por cuarto/equipos).

        Único sitio donde `Competition/matches?competitionId=1&...` (que trae
        TODOS los partidos de "Liga Endesa" en sentido amplio de la API, no
        solo liga regular) dice de qué competición es realmente cada
        partido: verificado en vivo (2026-08-24) que la Copa del Rey
        (3 partidos de Baskonia el 20-22 feb 2026, formato de eliminatoria a
        un partido en días consecutivos) viene mezclada en esa misma lista
        con `competitionId=2`, no la `1` ("Liga Endesa") que cabría esperar -
        de ahí que `adapter.py` necesite esto para no etiquetar Copa del Rey
        como ACB. Catálogo real (`availableFilters.competitions` de
        `Competition/matches`): 1=Liga Endesa, 2=Copa del Rey,
        3=Supercopa Endesa (ver `adapter._competition_name`).
        """
        return self._get(f"{MATCHDATA_BASE}/MatchHeader/match-header?matchId={match_id}")

    # ---- Compatibilidad con pipeline.py/tests existentes ----

    def fetch_season_game_ids(self, season: int) -> List[str]:
        """IDs de los partidos finalizados de una temporada (envoltorio de `fetch_season_finished_matches`)."""
        return [str(match["id"]) for match in self.fetch_season_finished_matches(season)]

    def fetch_game(self, game_id: str) -> Dict[str, Any]:
        """Contrato común (`ingest.common.raw_game`) para un partido ya finalizado.

        Necesita que `game_id` venga de una llamada previa a
        `fetch_season_finished_matches`/`fetch_season_game_ids` (de ahí saca
        el resumen del partido - equipo local/visitante, marcador, fecha -
        cacheado en memoria); boxscore, tiros y play-by-play se piden aquí.
        """
        from .adapter import build_raw_game, is_out_of_scope_competition  # import diferido: evita ciclo con parser.py

        key = str(game_id)
        match = self._match_cache.get(key)
        if match is None:
            raise ValueError(
                f"AcbClient.fetch_game: partido {game_id} desconocido - llama antes a "
                "fetch_season_finished_matches/fetch_season_game_ids."
            )
        # `match_header` primero y barato (una llamada) a propósito: si resulta ser una
        # competición ajena (ver `is_out_of_scope_competition`), no merece la pena pedir
        # boxscore/tiros/play-by-play - y sobre todo, no hay que dejar que sus jugadores
        # lleguen a `build_raw_game`/el loader (ver historia de ese chequeo).
        try:
            competition_id = self.fetch_match_header(game_id).get("competitionId")
        except Exception:  # noqa: BLE001 - opcional: si falla, se trata como "desconocida" (cae en ACB)
            competition_id = None
        if is_out_of_scope_competition(competition_id):
            raise ValueError(
                f"AcbClient: partido {game_id} pertenece a una competición fuera de "
                f"alcance (competitionId={competition_id}, ver adapter._COMPETITION_BY_ID) "
                "- no se carga."
            )

        boxscore = self.fetch_game_boxscore(game_id)
        shots = self.fetch_game_shots(game_id)
        play_by_play = self.fetch_game_play_by_play(game_id)
        try:
            advanced_stats = self.fetch_game_advanced_stats(game_id)
        except Exception:  # noqa: BLE001 - opcional: si falla, build_raw_game cae a la estimación propia
            advanced_stats = None
        return build_raw_game(
            match, boxscore, self._season_by_match[key],
            shots=shots, play_by_play=play_by_play, advanced_stats=advanced_stats,
            competition_id=competition_id,
        )

