"""Tests de la fuente ACB: `AcbClient` (mock de HTTP, sin red) y `adapter.build_raw_game`.

`client.py` fue reescrito (2026-08-20) para usar la API real de acb.com
(`api2.acb.com/api/{seasondata,matchdata}`, ver docstring del módulo) tras
confirmar que el backend `openapilive` reverse-engineered de OpenACB estaba
bloqueado (409) para todos. Estos tests simulan esa API con un `FakeSession`
en vez de golpear la red real.
"""
from urllib.parse import parse_qs, urlparse

import pytest
import requests

import ingest.acb.client as client_module
from ingest.acb.adapter import build_raw_game
from ingest.acb.client import AcbClient, AcbSourceUnavailable, season_to_edition_id


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Sin esto, tolerar huecos de `weekId` (ver `_MAX_CONSECUTIVE_GAPS`) haría
    que cada test con un calendario incompleto en el fixture (la mayoría)
    sumara hasta 80 sleeps de `ACB_REQUEST_DELAY` reales - la suite entera
    debe seguir siendo offline y rápida."""
    monkeypatch.setattr(client_module, "REQUEST_DELAY", 0.0)


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Simula `Competition/matches` (paginado por `weekId`), `Result/boxscores`, `MatchShots` y `PlayByPlay`."""

    def __init__(self, edition_id, matches_by_week, last_week, boxscores, shots=None, plays=None,
                 match_headers=None, server_error_weeks=()):
        self.headers = {}
        self.edition_id = edition_id
        self.matches_by_week = matches_by_week
        self.last_week = last_week
        # `weekId` que contestan 500 en vez de 200/400. Caso real y persistente
        # (2026-09-11): la semana 3022 de la edición 91 - ver
        # `ingest.acb.client._SourceServerError`.
        self.server_error_weeks = set(server_error_weeks)
        self.boxscores = boxscores
        self.shots = shots or {}
        self.plays = plays or {}
        self.match_headers = match_headers or {}
        # Para poder afirmar CUÁNTAS peticiones se hacen, no solo qué devuelven
        # (ver `test_fetch_season_finished_matches_walks_the_calendar_only_once`).
        self.requested_urls = []

    def get(self, url, timeout=30):
        self.requested_urls.append(url)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if parsed.path.endswith("/Competition/matches"):
            week_id = int(qs["weekId"][0]) if "weekId" in qs else self.last_week
            if week_id in self.server_error_weeks:
                return _FakeResponse(500, text="500 Server Error")
            if week_id not in self.matches_by_week:
                return _FakeResponse(400, text=f'"Week with ID {week_id} is not valid or does not exist."')
            return _FakeResponse(
                200,
                payload={
                    "matches": self.matches_by_week[week_id],
                    "teams": [],
                    "selectedFilters": {"season": self.edition_id, "week": week_id},
                },
            )
        if parsed.path.endswith("/Result/boxscores"):
            match_id = int(qs["matchId"][0])
            return _FakeResponse(200, payload=self.boxscores[match_id])
        if parsed.path.endswith("/MatchShots/match-shots"):
            match_id = int(qs["matchId"][0])
            return _FakeResponse(200, payload=self.shots.get(match_id, {"shotPoints": []}))
        if parsed.path.endswith("/PlayByPlay/play-by-play"):
            match_id = int(qs["matchId"][0])
            return _FakeResponse(200, payload=self.plays.get(match_id, {"plays": []}))
        if parsed.path.endswith("/MatchHeader/match-header"):
            match_id = int(qs["matchId"][0])
            if match_id not in self.match_headers:
                # Sin fixture para este partido: simula el endpoint no disponible,
                # igual que `AdvancedStats` cuando no se le da fixture (fetch_game
                # lo captura y cae en el comportamiento por defecto).
                raise AssertionError(f"sin fixture de match-header para {match_id}")
            return _FakeResponse(200, payload=self.match_headers[match_id])
        raise AssertionError(f"URL inesperada en el test: {url}")


def _match(match_id, home_id, away_id, status="FINALIZED", start="2026-01-10T18:00:00Z"):
    return {
        "id": match_id, "homeTeamId": home_id, "awayTeamId": away_id,
        "homeScore": 80, "awayScore": 75, "startDateTime": start,
        "matchStatus": status,
    }


def _player_row(player_id, name, shirt, minutes, pts, twopm, twopa, threepm, threepa, ftm, fta):
    first, last = name.split(" ", 1)
    return {
        "player": {"id": player_id, "firstName": first, "lastName": last, "nickname": None,
                   "shirtNumber": shirt, "gameRole": "Base"},
        "playTime": minutes, "isStarted": True,
        "points": pts, "twoPointersMade": twopm, "twoPointersAttempted": twopa,
        "threePointersMade": threepm, "threePointersAttempted": threepa,
        "freeThrowsMade": ftm, "freeThrowsAttempted": fta,
        "totalRebounds": 3, "assists": 2,
    }


def _team_period0(team_id, name, players, totals):
    return {
        "team": {"id": team_id, "fullName": name},
        "statsByPeriods": [
            {"quarter": 0, "stats": {"players": players, "team": {}, "total": totals}},
        ],
    }


def _totals(points, twopm, twopa, threepm, threepa, ftm, fta, tov, orb, drb, ast=0, stl=0, blk=0):
    return {
        "points": points, "twoPointersMade": twopm, "twoPointersAttempted": twopa,
        "threePointersMade": threepm, "threePointersAttempted": threepa,
        "freeThrowsMade": ftm, "freeThrowsAttempted": fta, "turnovers": tov,
        "offRebounds": orb, "defRebounds": drb, "assists": ast, "steals": stl, "blocks": blk,
    }


def test_season_to_edition_id_matches_verified_mapping():
    assert season_to_edition_id(2025) == 90
    assert season_to_edition_id(2024) == 89


def test_fetch_season_finished_matches_bridges_a_week_id_gap():
    """Verificado en vivo (2026-08-24): el espacio de `weekId` de una edición tiene huecos
    reales de decenas de semanas (edición 90: 2891-2945 inválidas, 2810-2890 vuelven a ser
    válidas con partidos reales - un partido de Baskonia de abril quedaba fuera del
    calendario cargado). Sin tolerancia a huecos, este fixture pararía en 2983 y nunca
    vería 2900."""
    matches_by_week = {
        2985: [_match(1001, 10, 20)],
        # 2984..2911 sin definir en el fixture -> 400 (_WeekNotFound) para todas: el hueco
        # (73 semanas, dentro de _MAX_CONSECUTIVE_GAPS=80).
        2910: [_match(1003, 20, 30)],
    }
    session = _FakeSession(edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={})
    client = AcbClient(session=session)

    matches = client.fetch_season_finished_matches(2025)

    assert {m["id"] for m in matches} == {1001, 1003}


def test_fetch_season_finished_matches_discards_matches_outside_season_date_window():
    """Red de seguridad: `selectedFilters.season` no valida nada de verdad (hace eco del
    parámetro pedido incluso en `weekId` claramente ajenos a la temporada, verificado en
    vivo) - sin el filtro de fecha, tolerar huecos podría arrastrar partidos de otra
    temporada si el hueco real resultase más ancho que lo tolerado."""
    matches_by_week = {
        2985: [_match(1001, 10, 20)],  # fecha por defecto de _match(): 2026-01-10, dentro de temporada 2025
        2900: [_match(1003, 20, 30, start="2019-03-01T18:00:00Z")],  # fuera de la ventana de la temporada 2025
    }
    session = _FakeSession(edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={})
    client = AcbClient(session=session)

    matches = client.fetch_season_finished_matches(2025)

    assert {m["id"] for m in matches} == {1001}


def test_fetch_season_finished_matches_walks_back_until_week_boundary():
    matches_by_week = {
        2985: [_match(1001, 10, 20)],
        2984: [_match(1002, 10, 30, status="NOT_STARTED")],  # aún no jugado, se descarta
        2983: [_match(1003, 20, 30)],
    }
    session = _FakeSession(edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={})
    client = AcbClient(session=session)

    matches = client.fetch_season_finished_matches(2025)

    assert {m["id"] for m in matches} == {1001, 1003}


def test_fetch_season_finished_matches_walks_the_calendar_only_once():
    """Recorrer el calendario son ~40 peticiones con `ACB_REQUEST_DELAY` entre medias. Se paga
    una vez sin problema en un backfill, pero `run_single_game` lo llamaba por CADA partido:
    recargar los 379 de la reparación de identidades eran horas de peaje contra minutos de
    trabajo. La segunda llamada tiene que salir de memoria, sin tocar la red."""
    matches_by_week = {2985: [_match(1001, 10, 20)], 2984: [_match(1002, 20, 30)]}
    session = _FakeSession(edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={})
    client = AcbClient(session=session)

    first = client.fetch_season_finished_matches(2025)
    requests_after_first = len(session.requested_urls)
    second = client.fetch_season_finished_matches(2025)

    assert {m["id"] for m in second} == {m["id"] for m in first}
    assert len(session.requested_urls) == requests_after_first, "ha vuelto a recorrer el calendario"


def test_fetch_game_uses_cached_match_and_builds_common_contract():
    matches_by_week = {2985: [_match(1001, 10, 20)]}
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "20:00", 10, 4, 6, 0, 1, 2, 2)],
                          _totals(80, 30, 50, 6, 20, 8, 10, 12, 10, 30)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "22:00", 8, 3, 5, 0, 2, 2, 2)],
                          _totals(75, 28, 55, 5, 18, 9, 12, 14, 8, 28)),
        ],
    }
    session = _FakeSession(edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={1001: boxscore})
    client = AcbClient(session=session)
    client.fetch_season_game_ids(2025)

    raw = client.fetch_game("1001")

    assert raw["game_id"] == "1001"
    assert raw["date"] == "2026-01-10"
    assert raw["home_team"] == {"id": "10", "name": "Home Team"}
    assert raw["away_team"] == {"id": "20", "name": "Away Team"}
    assert raw["home_score"] == 80 and raw["away_score"] == 75
    assert len(raw["players"]) == 2
    # sin shotPoints/plays en la fixture (ver _FakeSession), quedan vacíos - no por un hueco de la fuente
    assert raw["shots"] == [] and raw["lineups"] == [] and raw["score_progression"] == []
    # sin fixture de match-header (ver _FakeSession): cae en "ACB" por defecto, no revienta.
    assert raw["competition"] == "ACB"


def test_fetch_game_unknown_id_raises():
    client = AcbClient(session=_FakeSession(edition_id=90, matches_by_week={}, last_week=2985, boxscores={}))
    with pytest.raises(ValueError):
        client.fetch_game("does-not-exist")


def test_fetch_game_detects_copa_del_rey_from_match_header():
    """`Competition/matches?competitionId=1` mezcla Copa del Rey (verificado en vivo,
    2026-08-24: 3 partidos de Baskonia el 20-22 feb 2026 con `competitionId=2` real en
    `MatchHeader/match-header`) - sin esto quedaban etiquetados como "ACB" sin más."""
    matches_by_week = {2985: [_match(1001, 10, 20)]}
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "20:00", 10, 4, 6, 0, 1, 2, 2)],
                          _totals(80, 30, 50, 6, 20, 8, 10, 12, 10, 30)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "22:00", 8, 3, 5, 0, 2, 2, 2)],
                          _totals(75, 28, 55, 5, 18, 9, 12, 14, 8, 28)),
        ],
    }
    session = _FakeSession(
        edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={1001: boxscore},
        match_headers={1001: {"competitionId": 2}},
    )
    client = AcbClient(session=session)
    client.fetch_season_game_ids(2025)

    raw = client.fetch_game("1001")

    assert raw["competition"] == "Copa del Rey"


def test_fetch_game_rejects_out_of_scope_competition():
    """Hallazgo real (2026-08-24): partidos de Minicopa Endesa (`competitionId=10`, cantera,
    no primer equipo) venían mezclados en la misma lista que Liga Endesa/Copa del Rey/
    Supercopa. Sin este rechazo caían en "ACB" por defecto y sus jugadores, al compartir
    dorsal con jugadores reales del primer equipo, pisaban su nombre vía el fallback de
    identidad por dorsal+equipo. `fetch_game` debe fallar (el orquestador lo cuenta como
    partido fallido, no debe llegar a boxscore/loader) para cualquier competición no listada
    en `adapter._COMPETITION_BY_ID`, no solo para las reconocidas."""
    matches_by_week = {2985: [_match(1001, 10, 20)]}
    session = _FakeSession(
        edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={},
        match_headers={1001: {"competitionId": 10}},
    )
    client = AcbClient(session=session)
    client.fetch_season_game_ids(2025)

    with pytest.raises(ValueError, match="fuera de alcance"):
        client.fetch_game("1001")


def test_build_raw_game_computes_advanced_stats_and_pace():
    match = _match(2001, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 20, 8, 15, 0, 2, 4, 5)],
                          _totals(90, 35, 60, 6, 20, 14, 18, 10, 12, 33)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 18, 7, 12, 2, 6, 2, 3)],
                          _totals(85, 32, 58, 5, 22, 16, 20, 13, 9, 30)),
        ],
    }

    raw = build_raw_game(match, boxscore, season=2025)

    home_stats = next(t for t in raw["team_stats"] if t["team_id"] == "10")
    # fgm = twoPointersMade + threePointersMade = 35 + 6, fga = twoPointersAttempted + threePointersAttempted = 60 + 20
    assert home_stats["efg_pct"] == round(100 * (41 + 0.5 * 6) / 80, 1)
    assert raw["pace"] > 0
    # jugador: twoPointersMade=8, twoPointersAttempted=15, threePointersMade=0, threePointersAttempted=2
    assert raw["players"][0]["efg_pct"] == round(100 * (8 + 0.5 * 0) / 17, 1)


def test_build_raw_game_converts_shots_starters_subs_and_score_progression():
    match = _match(3001, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 2, 1, 1, 0, 0, 0, 0)],
                          _totals(2, 1, 1, 0, 0, 0, 0, 0, 0, 0)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 0, 0, 1, 0, 0, 0, 0)],
                          _totals(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)),
        ],
    }
    shots = {
        "shotPoints": [
            # tiro libre: sin coordenadas reales, se descarta
            {"playType": 92, "posX": 0, "posY": 0, "playerLicenseId": 501, "local": True},
            {"playType": 93, "posX": 1000, "posY": 500, "playerLicenseId": 501, "local": True},
            {"playType": 97, "posX": 2000, "posY": -1500, "playerLicenseId": 601, "local": False},
        ]
    }
    plays = {
        "plays": [
            {"order": 10, "playType": 599, "local": True, "quarter": 1, "minute": 10, "second": 0, "playerLicenseId": 501,
             "scoreHome": 0, "scoreAway": 0},
            {"order": 11, "playType": 599, "local": False, "quarter": 1, "minute": 10, "second": 0, "playerLicenseId": 601,
             "scoreHome": 0, "scoreAway": 0},
            {"order": 20, "playType": 93, "local": True, "quarter": 1, "minute": 9, "second": 30, "playerLicenseId": 501,
             "scoreHome": 2, "scoreAway": 0},
            {"order": 30, "playType": 115, "local": True, "quarter": 1, "minute": 5, "second": 0, "playerLicenseId": 501,
             "scoreHome": 2, "scoreAway": 0},
            {"order": 31, "playType": 112, "local": True, "quarter": 1, "minute": 5, "second": 0, "playerLicenseId": 502,
             "scoreHome": 2, "scoreAway": 0},
        ]
    }

    raw = build_raw_game(match, boxscore, season=2025, shots=shots, play_by_play=plays)

    assert len(raw["shots"]) == 2  # el tiro libre (92) queda fuera
    made = next(s for s in raw["shots"] if s["made"])
    assert made["player_id"] == "501" and made["team_id"] == "10"
    assert 0 <= made["x"] <= 500 and 0 <= made["y"] <= 500

    assert raw["starters"] == {"home": ["501"], "away": ["601"]}
    sub_types = {(e["type"], e["player_id"]) for e in raw["play_by_play"] if e["type"] in ("sub_in", "sub_out")}
    assert ("sub_out", "501") in sub_types and ("sub_in", "502") in sub_types

    assert raw["score_progression"] == [
        {"step": 0, "home": 0, "away": 0},
        {"step": 1, "home": 2, "away": 0},
    ]


def test_shot_coords_land_on_the_drawn_court():
    """`posX` es la distancia AL ARO en mm, no una coordenada normalizable de 0 a 7.500.

    Regresión: se normalizaba contra un rango fijo, lo que estiraba la
    profundidad un 76% — el aro caía en y=500 en vez de 455, así que el 35% de
    los tiros se pintaba por encima del aro (detrás del tablero) y el 77% no
    entraba en ninguna zona. Las coordenadas de referencia salen del seed de
    `court_zones` (`schema.sql`).
    """
    match = _match(3002, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 2, 1, 1, 0, 0, 0, 0)],
                          _totals(2, 1, 1, 0, 0, 0, 0, 0, 0, 0)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 0, 0, 1, 0, 0, 0, 0)],
                          _totals(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)),
        ],
    }
    shots = {
        "shotPoints": [
            {"playType": 93, "posX": 0, "posY": 0, "playerLicenseId": 501, "local": True},        # bajo el aro (0,0 = el propio aro)
            {"playType": 94, "posX": 7000, "posY": 0, "playerLicenseId": 501, "local": True},     # triple frontal
            {"playType": 98, "posX": 500, "posY": -6800, "playerLicenseId": 601, "local": False},  # triple esquina izq.
        ]
    }

    raw = build_raw_game(match, boxscore, season=2025, shots=shots, play_by_play=None)
    at_hoop, top_three, corner_three = raw["shots"]

    # Bajo el aro: en 'Pintura' (x 195-305, y 300-455), pegado a su borde de fondo.
    assert at_hoop["x"] == pytest.approx(250.0) and at_hoop["y"] == pytest.approx(455.0)
    # Triple frontal: más allá del vértice del arco ('Triple exterior'.y_max = 170).
    assert top_three["y"] < 170
    # Triple de esquina izquierda: en 'Triple esquina izq.' (x 15-55, y 380-460).
    assert 15 <= corner_three["x"] <= 55 and 380 <= corner_three["y"] <= 460


def test_dunks_are_loaded_but_flagged_as_not_located():
    """Los mates (100) llegan con `posX=posY=0`, el mismo centinela que los tiros
    libres: se cargan igual -son canastas de 2 reales- pero marcados
    `located=False`, para que el mapa no los presente como una coordenada
    medida (ver `shots.located` en `schema.sql`)."""
    match = _match(3003, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 2, 1, 1, 0, 0, 0, 0)],
                          _totals(2, 1, 1, 0, 0, 0, 0, 0, 0, 0)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 0, 0, 1, 0, 0, 0, 0)],
                          _totals(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)),
        ],
    }
    shots = {
        "shotPoints": [
            {"playType": 100, "posX": 0, "posY": 0, "playerLicenseId": 501, "local": True},   # mate
            {"playType": 93, "posX": 1500, "posY": 800, "playerLicenseId": 501, "local": True},
        ]
    }

    raw = build_raw_game(match, boxscore, season=2025, shots=shots, play_by_play=None)
    dunk, jumper = raw["shots"]

    assert dunk["made"] is True and dunk["located"] is False
    assert (dunk["x"], dunk["y"]) == (250.0, 455.0)  # el centinela cae en el aro
    assert jumper["located"] is True


def _official_side(efg, orb, tov, ft_rate, oer, der, net_rating, ast_pct, stl_pct, blk_pct, ts_pct, pace):
    return {
        "fourFactors": {"efgPct": {"partido": efg}, "orbPct": {"partido": orb}, "tovPct": {"partido": tov},
                        "fTr": {"partido": ft_rate}},
        "gameRhythm": {"possessions": {"partido": 80.0}, "pace": {"partido": pace}, "pointsPerPossession": {"partido": 1.1}},
        "ratings": {"netRating": {"partido": net_rating}, "oer": {"partido": oer}, "der": {"partido": der}},
        "ballHandling": {"astPct": {"partido": ast_pct}, "stlPct": {"partido": stl_pct}, "blkPct": {"partido": blk_pct}},
        "shooting": {"tsPct": {"partido": ts_pct}},
    }


def test_build_raw_game_prefers_official_advanced_stats_when_given():
    match = _match(4001, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 20, 8, 15, 0, 2, 4, 5)],
                          _totals(90, 35, 60, 6, 20, 14, 18, 10, 12, 33, ast=20, stl=5, blk=2)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 18, 7, 12, 2, 6, 2, 3)],
                          _totals(85, 32, 58, 5, 22, 16, 20, 13, 9, 30, ast=15, stl=6, blk=3)),
        ],
    }
    advanced_stats = {
        "homeAdvancedStats": _official_side(57.5, 36.6, 13.5, 27.5, 125.7, 103.4, 22.3, 57.1, 9.6, 2.4, 59.7, 84.2),
        "awayAdvancedStats": _official_side(45.7, 33.3, 17.9, 41.4, 103.4, 125.7, -22.3, 44.0, 7.2, 5.0, 51.0, 84.2),
    }

    raw = build_raw_game(match, boxscore, season=2025, advanced_stats=advanced_stats)

    home_stats = next(t for t in raw["team_stats"] if t["team_id"] == "10")
    # los valores OFICIALES sustituyen a la estimación propia (que daría otros números para este mismo boxscore)
    assert home_stats["ortg"] == 125.7 and home_stats["drtg"] == 103.4
    assert home_stats["efg_pct"] == 57.5 and home_stats["ft_rate"] == 27.5
    assert home_stats["ast_to_ratio"] == round(20 / 10, 2)  # sigue calculándose desde el boxscore, no viene en el endpoint
    assert raw["pace"] == 84.2


def test_build_raw_game_rejects_unfinished_match():
    match = _match(2002, 10, 20)
    with pytest.raises(ValueError):
        build_raw_game(match, {"matchFinished": False, "teamBoxscores": []}, season=2025)


# ---- tiros libres en bruto (ftm/fta) ----
# Las columnas existían en `schema.sql` desde el 2026-08-24, pero ningún
# adapter las emitía: `_team_totals` calculaba `ftm`/`fta` solo para derivar
# `ft_rate` y los descartaba, así que `player_game_stats.fta` seguía a NULL en
# las 17.455 filas de la base de datos real por muchas reingestas que se
# hicieran. Estos tests fijan que ya no se pierden.


def test_build_raw_game_emits_raw_free_throws_for_team_and_player():
    match = _match(5001, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 20, 8, 15, 0, 2, 4, 5)],
                          _totals(90, 35, 60, 6, 20, 14, 18, 10, 12, 33)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 18, 7, 12, 2, 6, 2, 3)],
                          _totals(85, 32, 58, 5, 22, 16, 20, 13, 9, 30)),
        ],
    }

    raw = build_raw_game(match, boxscore, season=2025)

    home_stats = next(t for t in raw["team_stats"] if t["team_id"] == "10")
    assert (home_stats["ftm"], home_stats["fta"]) == (14, 18)   # `_totals(..., ftm=14, fta=18, ...)`
    # Y la tasa que ya existía sigue saliendo igual: son datos complementarios,
    # no uno sustituyendo al otro.
    assert home_stats["ft_rate"] == round(100 * 14 / 80, 1)

    home_player = next(p for p in raw["players"] if p["player_id"] == "501")
    assert (home_player["ftm"], home_player["fta"]) == (4, 5)   # `_player_row(..., ftm=4, fta=5)`


def test_official_advanced_stats_still_carry_the_raw_free_throw_counts():
    """`match-advanced-stats` solo da la TASA (`fTr`); el recuento bruto tiene
    que seguir saliendo del boxscore aunque se prefiera el cálculo oficial."""
    match = _match(5002, 10, 20)
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [_player_row(501, "A Home", "7", "30:00", 20, 8, 15, 0, 2, 4, 5)],
                          _totals(90, 35, 60, 6, 20, 14, 18, 10, 12, 33, ast=20, stl=5, blk=2)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 18, 7, 12, 2, 6, 2, 3)],
                          _totals(85, 32, 58, 5, 22, 16, 20, 13, 9, 30, ast=15, stl=6, blk=3)),
        ],
    }
    advanced_stats = {
        "homeAdvancedStats": _official_side(57.5, 36.6, 13.5, 27.5, 125.7, 103.4, 22.3, 57.1, 9.6, 2.4, 59.7, 84.2),
        "awayAdvancedStats": _official_side(45.7, 33.3, 17.9, 41.4, 103.4, 125.7, -22.3, 44.0, 7.2, 5.0, 51.0, 84.2),
    }

    raw = build_raw_game(match, boxscore, season=2025, advanced_stats=advanced_stats)

    home_stats = next(t for t in raw["team_stats"] if t["team_id"] == "10")
    assert home_stats["ft_rate"] == 27.5              # tasa oficial
    assert (home_stats["ftm"], home_stats["fta"]) == (14, 18)  # recuento del boxscore


def test_a_player_row_without_free_throws_leaves_them_null_not_zero():
    """NULL = "la fuente no lo dio"; 0 = "no tiró ni uno". Confundirlos hace que
    `gp_ft` de las vistas deje de servir para nada (ver `schema.sql`)."""
    match = _match(5003, 10, 20)
    player = _player_row(501, "A Home", "7", "30:00", 20, 8, 15, 0, 2, 4, 5)
    del player["freeThrowsMade"], player["freeThrowsAttempted"]
    boxscore = {
        "matchFinished": True,
        "teamBoxscores": [
            _team_period0(10, "Home Team", [player], _totals(90, 35, 60, 6, 20, 14, 18, 10, 12, 33)),
            _team_period0(20, "Away Team", [_player_row(601, "B Away", "9", "28:00", 18, 7, 12, 2, 6, 2, 3)],
                          _totals(85, 32, 58, 5, 22, 16, 20, 13, 9, 30)),
        ],
    }

    raw = build_raw_game(match, boxscore, season=2025)

    home_player = next(p for p in raw["players"] if p["player_id"] == "501")
    assert home_player["ftm"] is None and home_player["fta"] is None


def test_fetch_season_finished_matches_skips_a_week_that_returns_500():
    """Una semana rota del lado de ACB no puede tumbar el recorrido entero.

    Verificado en vivo (2026-09-11) con la edición 91: `weekId=3022` devuelve
    500 de forma persistente mientras 3021 responde 200 - antes de esto,
    cualquier estado que no fuese 400 salía por `raise_for_status()` y ese
    único 500 abortaba el calendario completo.
    """
    matches_by_week = {
        2985: [_match(1001, 10, 20)],
        2984: [_match(1002, 30, 40)],  # solo se ve si el 500 de 2983 no aborta el recorrido
    }
    session = _FakeSession(
        edition_id=90, matches_by_week=matches_by_week, last_week=2985, boxscores={},
        server_error_weeks={2983},
    )
    client = AcbClient(session=session)

    matches = client.fetch_season_finished_matches(2025)

    assert {m["id"] for m in matches} == {1001, 1002}


def test_fetch_season_scheduled_matches_skips_a_week_that_returns_500():
    """El caso que tumbó la ingesta diaria: `acb_upcoming` con una semana envenenada."""
    matches_by_week = {
        2987: [_match(2001, 10, 20, status="SCHEDULED", start="2026-09-26T18:00:00Z")],  # ancla
        2989: [_match(2002, 30, 40, status="SCHEDULED", start="2026-10-10T18:00:00Z")],
        2986: [_match(2003, 50, 60, status="SCHEDULED", start="2026-09-19T18:00:00Z")],
    }
    session = _FakeSession(
        edition_id=91, matches_by_week=matches_by_week, last_week=2987, boxscores={},
        server_error_weeks={2988},  # entre el ancla y 2989, como la 3022 real
    )
    client = AcbClient(session=session)

    scheduled, _teams = client.fetch_season_scheduled_matches(2026)

    assert {m["id"] for m in scheduled} == {2001, 2002, 2003}


def test_a_calendar_walk_that_is_all_server_errors_fails_instead_of_returning_empty():
    """Si TODAS las semanas dan 5xx (API caída o `x-apikey` rotada), hay que fallar.

    Devolver un calendario vacío sería peor que fallar: no se distingue de una
    temporada que todavía no tiene partidos, así que la ingesta terminaría en
    verde sin haber cargado nada.
    """
    anchor = 2987
    matches_by_week = {anchor: [_match(2001, 10, 20, status="SCHEDULED", start="2026-09-26T18:00:00Z")]}
    session = _FakeSession(
        edition_id=91, matches_by_week=matches_by_week, last_week=anchor, boxscores={},
        # Todo menos el ancla: el recorrido entero se va en 5xx.
        server_error_weeks=set(range(anchor - 200, anchor + 201)) - {anchor},
    )
    client = AcbClient(session=session)

    with pytest.raises(AcbSourceUnavailable):
        client.fetch_season_scheduled_matches(2026)


def test_a_500_on_the_anchor_week_still_fails():
    """Sin semana ancla no hay recorrido que tolerar: el 5xx sigue siendo un fallo visible."""
    session = _FakeSession(
        edition_id=91, matches_by_week={2987: []}, last_week=2987, boxscores={},
        server_error_weeks={2987},
    )
    client = AcbClient(session=session)

    with pytest.raises(requests.HTTPError):
        client.fetch_season_scheduled_matches(2026)
