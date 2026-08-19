"""Tests de los servicios extraídos de `app.py` en la fase F2 de la migración.

Cubre por primera vez la lógica de negocio que vivía en `app.py` (sin tests
hasta ahora): calendario, plantilla, enfrentamientos directos, box scores y
fechas. Reutiliza las fixtures de `tests/conftest.py` (`session`, `teams`,
`played_game`), que crean una BD SQLite en memoria aislada por test.

Los tests son herméticos: no tocan `data/baskonia.db` real ni hacen peticiones
de red.
"""
from datetime import datetime, timedelta

from packages.baskonia_core.dates import parse_bbr_date
from packages.baskonia_core.db import models
from packages.baskonia_core.services import (
    _result_label,
    _rival_of,
    _team_games,
    _team_stats_for_game,
    boxscore_rows,
    current_roster,
    games_in_window,
    has_roster,
    head_to_head_games,
    past_games,
    team_by_slug,
    upcoming_games,
)


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------
class TestParseBbrDate:
    def test_parses_valid_bbr_date(self):
        dt = parse_bbr_date("Sun, Nov 23, 2025")
        assert dt == datetime(2025, 11, 23)

    def test_returns_none_for_invalid(self):
        assert parse_bbr_date("no es una fecha") is None
        assert parse_bbr_date(None) is None


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------
class TestTeamGames:
    def test_returns_all_games_for_team(self, session, teams, played_game):
        games = _team_games(session, teams["vitoria"])
        assert len(games) == 1
        assert games[0].id == played_game.id

    def test_filters_by_league(self, session, teams, played_game):
        games = _team_games(session, teams["vitoria"], league="euroleague")
        assert games == []

    def test_filters_by_season(self, session, teams, played_game):
        # played_game es de 2025-11-23 → temporada 2025
        games = _team_games(session, teams["vitoria"], season=2025)
        assert len(games) == 1
        games_other = _team_games(session, teams["vitoria"], season=2024)
        assert games_other == []


class TestPastGames:
    def test_only_played_games_sorted_desc(self, session, teams, played_game):
        # Añadir un partido pendiente que no debe aparecer
        session.add(
            models.Game(
                date="Sun, Dec 07, 2025",
                league="acb",
                home_team_id=teams["vitoria"].id,
                away_team_id=teams["bilbao"].id,
                home_score=None,
                away_score=None,
            )
        )
        session.flush()
        games = past_games(session, teams["vitoria"])
        assert len(games) == 1
        assert games[0].id == played_game.id


class TestUpcomingGames:
    def test_only_pending_future_games(self, session, teams):
        # `upcoming_games` filtra por `datetime.now()`: el partido pendiente debe
        # tener fecha futura respecto a hoy para aparecer.
        future = models.Game(
            date="Sun, Dec 07, 2026",
            league="acb",
            home_team_id=teams["vitoria"].id,
            away_team_id=teams["bilbao"].id,
            home_score=None,
            away_score=None,
        )
        past_pending = models.Game(
            date="Sun, Jan 01, 2020",
            league="acb",
            home_team_id=teams["vitoria"].id,
            away_team_id=teams["bilbao"].id,
            home_score=None,
            away_score=None,
        )
        session.add_all([future, past_pending])
        session.flush()
        games = upcoming_games(session, teams["vitoria"])
        # Solo el futuro; el pendiente con fecha pasada se excluye
        assert [g.id for g in games] == [future.id]


class TestGamesInWindow:
    def test_filters_by_window(self, session, teams, played_game):
        # played_game es 2025-11-23; ventana de 7 días alrededor de esa fecha
        ref = datetime(2025, 11, 25)
        games = games_in_window(session, teams["vitoria"], window_days=7, reference_date=ref)
        assert [g.id for g in games] == [played_game.id]

    def test_excludes_outside_window(self, session, teams, played_game):
        ref = datetime(2025, 12, 25)  # 32 días después del partido
        games = games_in_window(session, teams["vitoria"], window_days=7, reference_date=ref)
        assert games == []


class TestResultLabel:
    def test_played_game(self, session, teams, played_game):
        assert _result_label(played_game, teams["vitoria"]) == "88-80"

    def test_pending_game(self, session, teams):
        pending = models.Game(
            date="Sun, Dec 07, 2025",
            league="acb",
            home_team_id=teams["vitoria"].id,
            away_team_id=teams["bilbao"].id,
            home_score=None,
            away_score=None,
        )
        session.add(pending)
        session.flush()
        assert _result_label(pending, teams["vitoria"]) == "pendiente"


class TestRivalOf:
    def test_returns_away_when_home(self, session, teams, played_game):
        assert _rival_of(played_game, teams["vitoria"]).id == teams["bilbao"].id

    def test_returns_home_when_away(self, session, teams, played_game):
        assert _rival_of(played_game, teams["bilbao"]).id == teams["vitoria"].id


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------
class TestCurrentRoster:
    def test_only_players_with_photo_sorted_by_number(self, session, teams):
        session.add_all(
            [
                models.Player(team_id=teams["vitoria"].id, name="Markus Howard", number="0", photo_url="http://x/0.jpg"),
                models.Player(team_id=teams["vitoria"].id, name="Tadas Sedekerskis", number="10", photo_url="http://x/10.jpg"),
                # Sin foto → no es plantilla actual
                models.Player(team_id=teams["vitoria"].id, name="Ex Jugador", number="5", photo_url=None),
            ]
        )
        session.flush()
        roster = current_roster(session, teams["vitoria"])
        assert [p.name for p in roster] == ["Markus Howard", "Tadas Sedekerskis"]


class TestHasRoster:
    def test_true_when_players_exist(self, session, teams):
        session.add(models.Player(team_id=teams["vitoria"].id, name="Markus Howard", number="0", photo_url="http://x/0.jpg"))
        session.flush()
        assert has_roster(session, teams["vitoria"]) is True

    def test_false_when_no_players(self, session, teams):
        assert has_roster(session, teams["vitoria"]) is False


class TestTeamBySlug:
    def test_returns_team(self, session, teams):
        assert team_by_slug(session, "vitoria").id == teams["vitoria"].id

    def test_returns_none_for_unknown(self, session):
        assert team_by_slug(session, "no-existe") is None


# ---------------------------------------------------------------------------
# matchup
# ---------------------------------------------------------------------------
class TestHeadToHeadGames:
    def test_returns_games_between_two_teams(self, session, teams, played_game):
        games = head_to_head_games(session, teams["vitoria"], teams["bilbao"])
        assert [g.id for g in games] == [played_game.id]

    def test_filters_by_league(self, session, teams, played_game):
        games = head_to_head_games(session, teams["vitoria"], teams["bilbao"], league="euroleague")
        assert games == []


# ---------------------------------------------------------------------------
# boxscore
# ---------------------------------------------------------------------------
class TestTeamStatsForGame:
    def test_returns_stats_for_team(self, session, teams, played_game):
        stats = _team_stats_for_game(session, played_game.id, teams["vitoria"].id)
        assert stats is not None
        assert stats.pace == 69.5

    def test_returns_none_for_unknown_team(self, session, teams, played_game):
        assert _team_stats_for_game(session, played_game.id, 999) is None


class TestBoxscoreRows:
    def test_returns_rows_sorted_by_points_desc(self, session, teams, played_game):
        rows = boxscore_rows(session, played_game.id, teams["vitoria"].id)
        assert [r.player_name for r in rows] == ["Markus Howard"]
        assert rows[0].points == 22
