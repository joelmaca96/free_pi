"""Fixtures compartidas para la suite de tests.

Proporciona una base de datos SQLite en memoria aislada por test (para que
los tests de `insights.py`, `main.py` y `storage.py` no toquen la BD real de
`data/baskonia.db`) y datos de ejemplo reutilizables (equipos, partidos,
box scores).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import models


@pytest.fixture()
def session():
    """Sesión SQLAlchemy aislada sobre una BD SQLite en memoria.

    Cada test recibe una sesión nueva sobre una BD vacía, y se hace rollback
    al final para no dejar estado entre tests.
    """
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    db_session = testing_session()
    try:
        yield db_session
    finally:
        db_session.close()
        engine.dispose()


@pytest.fixture()
def teams(session):
    """Crea y devuelve dos equipos de ejemplo (Baskonia y Bilbao)."""
    vitoria = models.Team(slug="vitoria", name="Baskonia", league="acb")
    bilbao = models.Team(slug="bilbao", name="Surne Bilbao Basket", league="acb")
    session.add_all([vitoria, bilbao])
    session.flush()
    return {"vitoria": vitoria, "bilbao": bilbao}


@pytest.fixture()
def played_game(session, teams):
    """Crea un partido jugado Baskonia-Bilbao con box scores y stats avanzadas."""
    game = models.Game(
        date="Sun, Nov 23, 2025",
        league="acb",
        home_team_id=teams["vitoria"].id,
        away_team_id=teams["bilbao"].id,
        home_score=88,
        away_score=80,
        boxscore_url="/international/boxscores/202511230-vitoria.html",
    )
    session.add(game)
    session.flush()

    # Box scores de ejemplo (jugador del Baskonia y del Bilbao)
    session.add_all(
        [
            models.BoxScore(
                game_id=game.id,
                team_id=teams["vitoria"].id,
                player_name="Markus Howard",
                minutes="30:00",
                points=22,
                rebounds=3,
                assists=4,
                turnovers=2,
                fg_made=8,
                fg_attempted=15,
                fg3_made=4,
                fg3_attempted=9,
                ft_made=2,
                ft_attempted=2,
                efg_pct=0.667,
                ts_pct=0.690,
            ),
            models.BoxScore(
                game_id=game.id,
                team_id=teams["bilbao"].id,
                player_name="Melwin Pantzar",
                minutes="28:00",
                points=12,
                rebounds=5,
                assists=6,
                turnovers=3,
                fg_made=4,
                fg_attempted=10,
                fg3_made=2,
                fg3_attempted=5,
                ft_made=2,
                ft_attempted=2,
                efg_pct=0.500,
                ts_pct=0.551,
            ),
        ]
    )
    session.flush()

    # Stats avanzadas por equipo
    session.add_all(
        [
            models.TeamGameStats(
                game_id=game.id,
                team_id=teams["vitoria"].id,
                possessions=70.0,
                pace=69.5,
                off_rating=125.7,
                def_rating=115.1,
                net_rating=10.6,
            ),
            models.TeamGameStats(
                game_id=game.id,
                team_id=teams["bilbao"].id,
                possessions=69.0,
                pace=69.5,
                off_rating=115.9,
                def_rating=127.5,
                net_rating=-11.6,
            ),
        ]
    )
    session.flush()
    return game
