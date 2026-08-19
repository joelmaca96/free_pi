"""Tests para `insights.py`.

Cubre las funciones puras (parseo de fechas/minutos, escalado por-36, medias)
y las de agregación sobre la base de datos (forma reciente por jugador,
resumen avanzado por equipo, rachas por z-score, dificultad de calendario,
proyección de enfrentamiento, narrativa y carga de minutos).
"""
import pytest

from packages.baskonia_core.insights import (
    league_label,
    parse_minutes,
    per_36,
    player_load,
    player_recent_form,
    player_form_zscore,
    project_next_matchup,
    schedule_difficulty,
    scouting_narrative,
    season_label,
    season_start_year,
    team_advanced_summary,
)
from packages.baskonia_core.db import models


# --- Funciones puras -------------------------------------------------------

@pytest.mark.parametrize(
    "date_str,expected",
    [
        # Partido de otoño (septiembre-diciembre): la temporada empieza ese año
        ("Sun, Nov 23, 2025", 2025),
        # Partido de invierno/primavera (enero-junio): la temporada empezó el año anterior
        ("Sun, Feb 8, 2026", 2025),
        ("Mon, May 4, 2026", 2025),
        # Partido de verano (julio-agosto): corte en mes >= 7
        ("Wed, Aug 12, 2026", 2026),
        # Formato inválido o vacío -> None
        ("not-a-date", None),
        ("", None),
        (None, None),
    ],
)
def test_season_start_year(date_str, expected):
    """El año de inicio de temporada se deriva de la fecha del partido."""
    assert season_start_year(date_str) == expected


def test_season_label():
    """El año de inicio se formatea como 'AAAA-AA'."""
    assert season_label(2025) == "2025-26"
    assert season_label(None) == "-"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("30:00", 30.0),
        ("28:30", 28.5),
        ("12:15", 12.25),
        ("0:00", 0.0),
        # Formato decimal directo
        ("25.5", 25.5),
        # Inválidos
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_minutes(value, expected):
    """'MM:SS' se convierte a minutos decimales."""
    assert parse_minutes(value) == expected


def test_per_36():
    """Escala un conteo a ritmo de 36 minutos."""
    assert per_36(18, 36) == 18.0
    assert per_36(9, 18) == 18.0
    assert per_36(None, 30) is None
    assert per_36(10, None) is None
    assert per_36(10, 0) is None


def test_league_label():
    """Los códigos de competición se formatean para la UI."""
    assert league_label("acb") == "ACB"
    assert league_label("euroleague") == "Euroliga"
    assert league_label("supercopa") == "Supercopa"
    assert league_label(None) == "Todas"
    # Código desconocido -> capitalize (no falla)
    assert league_label("copa") == "Copa"


# --- player_recent_form ----------------------------------------------------

def test_player_recent_form_basic(session, teams, played_game):
    """La forma reciente agrega medias por jugador sobre los partidos jugados."""
    form = player_recent_form(session, teams["vitoria"], last_n=5)
    assert len(form) == 1
    row = form[0]
    assert row["player_name"] == "Markus Howard"
    assert row["games"] == 1
    assert row["avg_pts"] == pytest.approx(22.0)
    assert row["avg_minutes"] == pytest.approx(30.0)
    # PTS por-36: 22 * 36 / 30
    assert row["avg_pts_per36"] == pytest.approx(22 * 36 / 30)
    assert row["avg_efg_pct"] == pytest.approx(0.667)
    assert row["avg_ts_pct"] == pytest.approx(0.690)


def test_player_recent_form_orders_by_pts(session, teams, played_game):
    """La forma reciente se ordena por puntos medios descendente."""
    # Añadir un segundo jugador con menos puntos
    session.add(
        models.BoxScore(
            game_id=played_game.id,
            team_id=teams["vitoria"].id,
            player_name="Chima Moneke",
            minutes="25:00",
            points=10,
            fg_made=4,
            fg_attempted=8,
            fg3_made=0,
            fg3_attempted=1,
            ft_made=2,
            ft_attempted=3,
        )
    )
    session.flush()
    form = player_recent_form(session, teams["vitoria"], last_n=5)
    assert [r["player_name"] for r in form] == ["Markus Howard", "Chima Moneke"]


def test_player_recent_form_skips_no_minutes(session, teams, played_game):
    """Los partidos sin minutos registrados no cuentan para la media."""
    session.add(
        models.BoxScore(
            game_id=played_game.id,
            team_id=teams["vitoria"].id,
            player_name="Sin Minutos",
            minutes=None,
            points=5,
        )
    )
    session.flush()
    form = player_recent_form(session, teams["vitoria"], last_n=5)
    # El jugador sin minutos no aparece
    assert all(r["player_name"] != "Sin Minutos" for r in form)


def test_player_recent_form_season_filter(session, teams, played_game):
    """El filtro de temporada excluye partidos de otras temporadas."""
    # Partido de otra temporada (2026-27)
    other = models.Game(
        date="Sun, Oct 4, 2026",
        league="acb",
        home_team_id=teams["vitoria"].id,
        away_team_id=teams["bilbao"].id,
        home_score=90,
        away_score=85,
    )
    session.add(other)
    session.flush()
    session.add(
        models.BoxScore(
            game_id=other.id,
            team_id=teams["vitoria"].id,
            player_name="Markus Howard",
            minutes="30:00",
            points=30,
            fg_made=10,
            fg_attempted=18,
            fg3_made=5,
            fg3_attempted=10,
            ft_made=5,
            ft_attempted=5,
        )
    )
    session.flush()

    # Sin filtro: 2 partidos
    form_all = player_recent_form(session, teams["vitoria"], last_n=5)
    assert form_all[0]["games"] == 2
    # Filtrado a 2025-26: solo el partido original
    form_2025 = player_recent_form(session, teams["vitoria"], last_n=5, season=2025)
    assert form_2025[0]["games"] == 1
    assert form_2025[0]["avg_pts"] == pytest.approx(22.0)


def test_player_recent_form_league_filter(session, teams, played_game):
    """El filtro de competición excluye partidos de otras ligas."""
    other = models.Game(
        date="Fri, Oct 2, 2026",
        league="euroleague",
        home_team_id=teams["vitoria"].id,
        away_team_id=teams["bilbao"].id,
        home_score=85,
        away_score=90,
    )
    session.add(other)
    session.flush()
    session.add(
        models.BoxScore(
            game_id=other.id,
            team_id=teams["vitoria"].id,
            player_name="Markus Howard",
            minutes="30:00",
            points=25,
            fg_made=9,
            fg_attempted=16,
            fg3_made=4,
            fg3_attempted=8,
            ft_made=3,
            ft_attempted=4,
        )
    )
    session.flush()

    form_acb = player_recent_form(session, teams["vitoria"], last_n=5, league="acb")
    assert form_acb[0]["games"] == 1
    assert form_acb[0]["avg_pts"] == pytest.approx(22.0)

    form_el = player_recent_form(session, teams["vitoria"], last_n=5, league="euroleague")
    assert form_el[0]["games"] == 1
    assert form_el[0]["avg_pts"] == pytest.approx(25.0)


# --- team_advanced_summary -------------------------------------------------

def test_team_advanced_summary_basic(session, teams, played_game):
    """El resumen avanzado agrega pace/ratings y eFG%/TS% medios."""
    summary = team_advanced_summary(session, teams["vitoria"])
    assert summary["avg_pace"] == pytest.approx(69.5)
    assert summary["avg_off_rating"] == pytest.approx(125.7)
    assert summary["avg_def_rating"] == pytest.approx(115.1)
    assert summary["avg_net_rating"] == pytest.approx(10.6)
    assert summary["avg_efg_pct"] == pytest.approx(0.667)
    assert summary["avg_ts_pct"] == pytest.approx(0.690)


def test_team_advanced_summary_empty_team(session, teams):
    """Un equipo sin partidos devuelve todos los valores a None."""
    summary = team_advanced_summary(session, teams["bilbao"])
    assert summary["avg_pace"] is None
    assert summary["avg_off_rating"] is None
    assert summary["avg_net_rating"] is None


# --- player_form_zscore ----------------------------------------------------

def test_player_form_zscore_basic(session, teams, played_game):
    """El z-score de racha se calcula sobre la temporada del jugador."""
    # Necesitamos varios partidos del mismo jugador en la misma temporada.
    # Calculamos ts_pct explícitamente (como hace upsert_boxscore) para que
    # el z-score de TS% tenga suficientes partidos con dato.
    for i, pts in enumerate([20, 24, 18, 22, 26]):
        game = models.Game(
            date=f"Sun, Nov {10 + i}, 2025",
            league="acb",
            home_team_id=teams["vitoria"].id,
            away_team_id=teams["bilbao"].id,
            home_score=80 + i,
            away_score=75,
        )
        session.add(game)
        session.flush()
        fga = 15
        fta = 2
        session.add(
            models.BoxScore(
                game_id=game.id,
                team_id=teams["vitoria"].id,
                player_name="Markus Howard",
                minutes="30:00",
                points=pts,
                fg_made=pts // 2,
                fg_attempted=fga,
                fg3_made=3,
                fg3_attempted=8,
                ft_made=2,
                ft_attempted=fta,
                ts_pct=pts / (2 * (fga + 0.44 * fta)),
            )
        )
    session.flush()

    streaks = player_form_zscore(session, teams["vitoria"], season=2025, recent_n=3, min_season_games=3)
    assert len(streaks) == 1
    row = streaks[0]
    assert row["player_name"] == "Markus Howard"
    assert row["games_season"] == 6  # 1 del fixture + 5 añadidos
    assert row["z_score_pts"] is not None
    assert row["z_score_ts"] is not None


def test_player_form_zscore_skips_few_games(session, teams, played_game):
    """Un jugador con menos del mínimo de partidos se omite."""
    streaks = player_form_zscore(session, teams["vitoria"], season=2025, recent_n=3, min_season_games=6)
    assert streaks == []


# --- schedule_difficulty ---------------------------------------------------

def test_schedule_difficulty_basic(session, teams, played_game):
    """La dificultad del calendario promedia el Net Rating de los próximos rivales."""
    # Crear un rival con stats avanzadas en la temporada
    rival = models.Team(slug="real-madrid", name="Real Madrid", league="acb")
    session.add(rival)
    session.flush()
    rival_game = models.Game(
        date="Sun, Nov 30, 2025",
        league="acb",
        home_team_id=rival.id,
        away_team_id=teams["bilbao"].id,
        home_score=95,
        away_score=80,
    )
    session.add(rival_game)
    session.flush()
    session.add(
        models.TeamGameStats(
            game_id=rival_game.id,
            team_id=rival.id,
            possessions=70.0,
            pace=70.0,
            off_rating=135.0,
            def_rating=110.0,
            net_rating=25.0,
        )
    )
    session.flush()

    # Partido pendiente contra el rival
    upcoming = models.Game(
        date="Sun, Dec 7, 2025",
        league="acb",
        home_team_id=teams["vitoria"].id,
        away_team_id=rival.id,
    )
    session.add(upcoming)
    session.flush()

    result = schedule_difficulty(session, teams["vitoria"], [upcoming], season=2025, next_n=5)
    assert result["games_considered"] == 1
    assert result["opponents_scouted"] == 1
    assert result["avg_opponent_net_rating"] == pytest.approx(25.0)
    assert result["opponents"][0]["opponent_name"] == "Real Madrid"


def test_schedule_difficulty_league_filter(session, teams, played_game):
    """El filtro de competición descarta partidos de otras ligas antes de tomar los próximos N."""
    rival = models.Team(slug="real-madrid", name="Real Madrid", league="acb")
    session.add(rival)
    session.flush()

    # Dos partidos pendientes: uno de Euroliga y uno de ACB
    el_game = models.Game(
        date="Fri, Dec 5, 2025",
        league="euroleague",
        home_team_id=teams["vitoria"].id,
        away_team_id=rival.id,
    )
    acb_game = models.Game(
        date="Sun, Dec 7, 2025",
        league="acb",
        home_team_id=teams["vitoria"].id,
        away_team_id=rival.id,
    )
    session.add_all([el_game, acb_game])
    session.flush()

    result = schedule_difficulty(
        session, teams["vitoria"], [el_game, acb_game], season=2025, next_n=5, league="acb"
    )
    assert result["games_considered"] == 1
    assert result["opponents"][0]["date"] == "Sun, Dec 7, 2025"


# --- project_next_matchup --------------------------------------------------

def test_project_next_matchup_basic(session, teams, played_game):
    """La proyección combina las medias de temporada de ambos equipos.

    El fixture `played_game` ya crea stats avanzadas para ambos equipos
    (vitoria y bilbao), así que la proyección tiene datos de los dos.
    """
    result = project_next_matchup(session, teams["vitoria"], teams["bilbao"], season=2025)
    assert result is not None
    assert result["projected_possessions"] == pytest.approx(69.5)
    assert result["team_projected_score"] > 0


def test_project_next_matchup_no_data(session, teams):
    """Sin stats avanzadas en la temporada, la proyección es None."""
    result = project_next_matchup(session, teams["vitoria"], teams["bilbao"], season=2025)
    assert result is None


# --- scouting_narrative ----------------------------------------------------

def test_scouting_narrative_basic(session, teams, played_game):
    """La narrativa genera un resumen en español con los datos disponibles."""
    narrative = scouting_narrative(session, teams["vitoria"], season=2025)
    assert narrative is not None
    assert "posesiones" in narrative
    assert "Net Rating" in narrative


def test_scouting_narrative_no_data(session, teams):
    """Sin partidos con stats avanzadas en la temporada, la narrativa es None."""
    narrative = scouting_narrative(session, teams["bilbao"], season=2025)
    assert narrative is None


# --- player_load -----------------------------------------------------------

def test_player_load_basic(session, teams, played_game):
    """La carga de minutos agrega los minutos de los partidos indicados."""
    load = player_load(session, teams["vitoria"], [played_game])
    assert len(load) == 1
    assert load[0]["player_name"] == "Markus Howard"
    assert load[0]["total_minutes"] == pytest.approx(30.0)
    assert load[0]["avg_minutes"] == pytest.approx(30.0)


def test_player_load_empty_games(session, teams):
    """Sin partidos, la carga es vacía."""
    assert player_load(session, teams["vitoria"], []) == []
