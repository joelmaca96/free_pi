"""Tests para la descarga bajo demanda de un box score (`fetch_game_boxscore`).

El pipeline solo captura automáticamente los últimos `config.LAST_N_GAMES`
partidos y los enfrentamientos directos, así que la mayoría del calendario
queda guardada sin box score. Antes, abrir uno de esos partidos en la GUI
mostraba dos tablas vacías sin explicación ni forma de pedirlo.
"""
import pytest

from apps.ingest import pipeline
from packages.baskonia_core.db import models
from packages.baskonia_core.db.storage import upsert_game, upsert_team

BOX_ROWS = {
    "home": [
        {"Player": "Markus Howard", "MP": "28:00", "PTS": 20, "TRB": 3, "AST": 4,
         "FG": 7, "FGA": 13, "3P": 3, "3PA": 7, "FT": 3, "FTA": 3, "TOV": 2},
    ],
    "away": [
        {"Player": "Facundo Campazzo", "MP": "30:00", "PTS": 14, "TRB": 2, "AST": 8,
         "FG": 5, "FGA": 11, "3P": 2, "3PA": 6, "FT": 2, "FTA": 2, "TOV": 3},
    ],
}


@pytest.fixture
def game(session):
    """Partido jugado, con enlace a box score y sin box score guardado."""
    home = upsert_team(session, "vitoria", "Baskonia", "acb")
    away = upsert_team(session, "real-madrid", "Real Madrid", "acb")
    game_obj = upsert_game(
        session,
        date="Tue, Sep 30, 2025",
        league="euroleague",
        home_team=home,
        away_team=away,
        home_score=90,
        away_score=85,
        boxscore_url="/international/boxscores/2025-09-30-vitoria.html",
    )
    session.commit()
    return game_obj


def test_downloads_and_stores_boxscore(session, game, monkeypatch):
    """Descarga el box score que faltaba y lo persiste."""
    monkeypatch.setattr(pipeline, "fetch_boxscore", lambda client, url: BOX_ROWS)

    assert pipeline.fetch_game_boxscore(session, client=None, game_obj=game) is True

    rows = session.query(models.BoxScore).filter_by(game_id=game.id).all()
    assert {r.player_name for r in rows} == {"Markus Howard", "Facundo Campazzo"}
    assert next(r for r in rows if r.player_name == "Markus Howard").points == 20


def test_is_idempotent(session, game, monkeypatch):
    """Llamarlo dos veces no duplica filas ni vuelve a pedir red."""
    calls = []

    def _fake(client, url):
        calls.append(url)
        return BOX_ROWS

    monkeypatch.setattr(pipeline, "fetch_boxscore", _fake)

    pipeline.fetch_game_boxscore(session, client=None, game_obj=game)
    pipeline.fetch_game_boxscore(session, client=None, game_obj=game)

    assert len(calls) == 1  # la segunda vez no toca la red
    assert session.query(models.BoxScore).filter_by(game_id=game.id).count() == 2


def test_computes_advanced_stats(session, game, monkeypatch):
    """Tras descargar, calcula las estadísticas avanzadas del partido.

    Es lo que alimenta las métricas de Pace/Net Rating de la ficha: sin esto la
    ficha seguiría media vacía después de descargar.
    """
    monkeypatch.setattr(pipeline, "fetch_boxscore", lambda client, url: BOX_ROWS)

    pipeline.fetch_game_boxscore(session, client=None, game_obj=game)

    stats = session.query(models.TeamGameStats).filter_by(game_id=game.id).all()
    assert len(stats) == 2
    howard = (
        session.query(models.BoxScore)
        .filter_by(game_id=game.id, player_name="Markus Howard")
        .one()
    )
    assert howard.efg_pct is not None
    assert howard.ts_pct is not None


def test_returns_false_without_boxscore_url(session):
    """Un partido sin enlace a box score no se puede descargar."""
    home = upsert_team(session, "vitoria", "Baskonia", "acb")
    away = upsert_team(session, "joventut", "Joventut", "acb")
    pending = upsert_game(
        session, date="Sun, May 3, 2026", league="acb", home_team=home, away_team=away
    )
    session.commit()

    assert pipeline.fetch_game_boxscore(session, client=None, game_obj=pending) is False


def test_returns_false_when_download_fails(session, game, monkeypatch):
    """Si Basketball-Reference falla, devuelve False en vez de propagar."""
    def _boom(client, url):
        raise RuntimeError("503")

    monkeypatch.setattr(pipeline, "fetch_boxscore", _boom)

    assert pipeline.fetch_game_boxscore(session, client=None, game_obj=game) is False
    assert session.query(models.BoxScore).filter_by(game_id=game.id).count() == 0


# --- _report_sources -------------------------------------------------------

def test_report_sources_warns_when_primary_contributed_nothing(caplog):
    """Avisa si la temporada se completa solo con fuentes de backup.

    Regresión: cada fuente se descarga en su propio `try/except`, así que si
    RealGM (la fuente principal) fallaba, el backfill seguía con BBR y no había
    nada en la salida que dijera que los datos NO venían de la fuente principal.
    """
    with caplog.at_level("WARNING"):
        pipeline._report_sources("euroleague", [("bbr", [{}, {}]), ("cms", [{}])])

    assert "fuente principal" in caplog.text
    assert "realgm" in caplog.text
    assert "bbr" in caplog.text


def test_report_sources_quiet_when_primary_contributed(caplog):
    """Si la fuente principal aporta partidos, no avisa."""
    with caplog.at_level("WARNING"):
        pipeline._report_sources("euroleague", [("realgm", [{}, {}]), ("bbr", [{}])])

    assert "fuente principal" not in caplog.text


def test_report_sources_ignores_leagues_without_primary(caplog):
    """Copa y Supercopa no las cubre RealGM: no se avisa por su ausencia."""
    with caplog.at_level("WARNING"):
        pipeline._report_sources("copa-del-rey", [("cms", [{}])])

    assert "fuente principal" not in caplog.text


def test_report_sources_logs_every_contribution(caplog):
    """El resumen enumera lo que aportó cada fuente."""
    with caplog.at_level("INFO"):
        pipeline._report_sources("acb", [("realgm", [{}]), ("bbr", [{}, {}])])

    assert "realgm=1" in caplog.text
    assert "bbr=2" in caplog.text
