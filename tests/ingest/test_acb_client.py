"""Tests de la fuente ACB: `AcbClient` (mock de HTTP, sin red) y `adapter.build_raw_game`.

`client.py` fue reescrito (2026-08-20) para usar la API real de acb.com
(`api2.acb.com/api/{seasondata,matchdata}`, ver docstring del módulo) tras
confirmar que el backend `openapilive` reverse-engineered de OpenACB estaba
bloqueado (409) para todos. Estos tests simulan esa API con un `FakeSession`
en vez de golpear la red real.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from ingest.acb.adapter import build_raw_game
from ingest.acb.client import AcbClient, season_to_edition_id


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

    def __init__(self, edition_id, matches_by_week, last_week, boxscores, shots=None, plays=None):
        self.headers = {}
        self.edition_id = edition_id
        self.matches_by_week = matches_by_week
        self.last_week = last_week
        self.boxscores = boxscores
        self.shots = shots or {}
        self.plays = plays or {}

    def get(self, url, timeout=30):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if parsed.path.endswith("/Competition/matches"):
            week_id = int(qs["weekId"][0]) if "weekId" in qs else self.last_week
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
        raise AssertionError(f"URL inesperada en el test: {url}")


def _match(match_id, home_id, away_id, status="FINALIZED"):
    return {
        "id": match_id, "homeTeamId": home_id, "awayTeamId": away_id,
        "homeScore": 80, "awayScore": 75, "startDateTime": "2026-01-10T18:00:00Z",
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


def test_fetch_game_unknown_id_raises():
    client = AcbClient(session=_FakeSession(edition_id=90, matches_by_week={}, last_week=2985, boxscores={}))
    with pytest.raises(ValueError):
        client.fetch_game("does-not-exist")


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
