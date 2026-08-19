"""Fixtures de los tests de la API.

Proporciona un `TestClient` sobre la app FastAPI con `dependency_overrides`
para que `get_session` use una BD SQLite en memoria aislada por test (la misma
estrategia que `tests/conftest.py`), de modo que los tests no toquen la BD real.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.deps import get_session
from apps.api.main import create_app
from packages.baskonia_core.db import models


@pytest.fixture()
def api_session():
    """Sesión SQLAlchemy aislada sobre una BD SQLite en memoria para la API.

    Usa `StaticPool` para que todas las conexiones compartan la misma BD en
    memoria (si no, cada conexión nueva vería una BD vacía distinta).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    db_session = testing_session()
    try:
        yield db_session
    finally:
        db_session.close()
        engine.dispose()


@pytest.fixture()
def client(api_session):
    """TestClient de la app con `get_session` sobreescrito a la BD en memoria."""
    app = create_app()

    def _override_get_session():
        yield api_session

    app.dependency_overrides[get_session] = _override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def api_teams(api_session):
    """Crea y devuelve dos equipos de ejemplo (Baskonia y Bilbao) en la BD de la API."""
    vitoria = models.Team(slug="vitoria", name="Baskonia", league="acb")
    bilbao = models.Team(slug="bilbao", name="Surne Bilbao Basket", league="acb")
    api_session.add_all([vitoria, bilbao])
    api_session.flush()
    return {"vitoria": vitoria, "bilbao": bilbao}


@pytest.fixture()
def api_played_game(api_session, api_teams):
    """Crea un partido jugado Baskonia-Bilbao con box scores y stats avanzadas."""
    game = models.Game(
        date="Sun, Nov 23, 2025",
        league="acb",
        home_team_id=api_teams["vitoria"].id,
        away_team_id=api_teams["bilbao"].id,
        home_score=22,  # suma de los puntos del box score de vitoria (consistente)
        away_score=10,  # suma de los puntos del box score de bilbao (consistente)
        boxscore_url="/international/boxscores/202511230-vitoria.html",
    )
    api_session.add(game)
    api_session.flush()

    api_session.add_all(
        [
            models.BoxScore(
                game_id=game.id,
                team_id=api_teams["vitoria"].id,
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
                team_id=api_teams["bilbao"].id,
                player_name="Xabi Rabaseda",
                minutes="28:00",
                points=10,
                rebounds=5,
                assists=2,
                turnovers=1,
                fg_made=4,
                fg_attempted=9,
                fg3_made=2,
                fg3_attempted=5,
                ft_made=0,
                ft_attempted=0,
                efg_pct=0.556,
                ts_pct=0.556,
            ),
        ]
    )
    api_session.add(
        models.TeamGameStats(
            game_id=game.id,
            team_id=api_teams["vitoria"].id,
            possessions=70,
            pace=70.0,
            off_rating=125.7,
            def_rating=114.3,
            net_rating=11.4,
        )
    )
    api_session.add(
        models.TeamGameStats(
            game_id=game.id,
            team_id=api_teams["bilbao"].id,
            possessions=70,
            pace=70.0,
            off_rating=110.0,
            def_rating=120.0,
            net_rating=-10.0,
        )
    )
    api_session.flush()
    return game
