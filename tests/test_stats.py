"""Tests para `stats.py`.

Cubre el cálculo de estadísticas avanzadas: eFG%, TS%, posesiones estimadas,
ratings por equipo/partido (ORtg/DRtg/Net Rating/Pace) y la proyección de un
enfrentamiento.
"""
import pytest

from packages.baskonia_core.stats import (
    effective_fg_pct,
    estimate_possessions,
    project_matchup,
    team_game_ratings,
    true_shooting_pct,
)


# --- effective_fg_pct ------------------------------------------------------

def test_efg_basic():
    """eFG% = (FGM + 0.5*3PM) / FGA."""
    # 8 canastas de 15, 4 de ellas triples
    assert effective_fg_pct(8, 15, 4) == pytest.approx((8 + 0.5 * 4) / 15)


def test_efg_no_attempts_returns_none():
    """Sin intentos de campo, eFG% es None (evita división por cero)."""
    assert effective_fg_pct(0, 0, 0) is None
    assert effective_fg_pct(None, None, None) is None


def test_efg_handles_none_values():
    """Valores None se tratan como 0."""
    assert effective_fg_pct(5, 10, None) == pytest.approx(5 / 10)


# --- true_shooting_pct -----------------------------------------------------

def test_ts_basic():
    """TS% = PTS / (2 * (FGA + 0.44*FTA))."""
    pts, fga, fta = 22, 15, 2
    expected = pts / (2 * (fga + 0.44 * fta))
    assert true_shooting_pct(pts, fga, fta) == pytest.approx(expected)


def test_ts_no_denominator_returns_none():
    """Sin intentos, TS% es None."""
    assert true_shooting_pct(0, 0, 0) is None


# --- estimate_possessions --------------------------------------------------

def test_possessions_basic():
    """Posesiones ≈ FGA - ORB + TOV + 0.4*FTA."""
    fga, fta, orb, tov = 80, 20, 10, 12
    expected = fga - orb + tov + 0.4 * fta
    assert estimate_possessions(fga, fta, orb, tov) == pytest.approx(expected)


def test_possessions_none_fga():
    """Sin FGA, las posesiones son None."""
    assert estimate_possessions(None, 20, 10, 12) is None


# --- team_game_ratings -----------------------------------------------------

def test_team_game_ratings_basic():
    """ORtg/DRtg/Net/Pace se calculan a partir de los totales de ambos equipos."""
    team_totals = {"fga": 80, "fta": 20, "orb": 10, "tov": 12}
    opp_totals = {"fga": 75, "fta": 18, "orb": 9, "tov": 14}
    team_score, opp_score = 88, 80

    result = team_game_ratings(team_totals, opp_totals, team_score, opp_score)

    poss = estimate_possessions(80, 20, 10, 12)
    opp_poss = estimate_possessions(75, 18, 9, 14)
    assert result["possessions"] == pytest.approx(poss)
    assert result["pace"] == pytest.approx((poss + opp_poss) / 2)
    assert result["off_rating"] == pytest.approx(100 * team_score / poss)
    assert result["def_rating"] == pytest.approx(100 * opp_score / opp_poss)
    assert result["net_rating"] == pytest.approx(result["off_rating"] - result["def_rating"])


def test_team_game_ratings_missing_data():
    """Sin posesiones (FGA None), los ratings son None."""
    result = team_game_ratings({"fga": None}, {"fga": 75}, 88, 80)
    assert result["possessions"] is None
    assert result["off_rating"] is None
    assert result["pace"] is None


# --- project_matchup -------------------------------------------------------

def test_project_matchup_basic():
    """La proyección combina pace y ratings de ambos equipos."""
    result = project_matchup(70.0, 120.0, 110.0, 68.0, 115.0, 112.0)
    assert result is not None
    assert result["projected_possessions"] == pytest.approx((70.0 + 68.0) / 2)
    assert result["team_projected_rating"] == pytest.approx((120.0 + 112.0) / 2)
    assert result["opp_projected_rating"] == pytest.approx((115.0 + 110.0) / 2)
    assert result["team_projected_score"] == pytest.approx(
        result["team_projected_rating"] * result["projected_possessions"] / 100
    )


def test_project_matchup_missing_value_returns_none():
    """Si falta cualquiera de los 6 valores, la proyección es None."""
    assert project_matchup(None, 120.0, 110.0, 68.0, 115.0, 112.0) is None
    assert project_matchup(70.0, 120.0, 110.0, None, 115.0, 112.0) is None
