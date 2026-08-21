"""Fixtures de los tests de la API (feature 012, esquema de scouting).

Proporciona un `TestClient` sobre la app FastAPI con `dependency_overrides`
para que `get_repository` use una BD de scouting SQLite en memoria aislada por
test (la misma estrategia que `tests/test_scouting_db.py`), de modo que los
tests no toquen `data/baskonia.db` real.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.deps import get_repository
from apps.api.main import create_app
from packages.baskonia_core.db.scouting import (
    ScoutingRepository,
    init_scouting_db,
)


@pytest.fixture()
def engine():
    """Engine SQLite en memoria con el esquema de scouting ya cargado (seed).

    Usa `StaticPool` para que todas las conexiones (incluidas las del hilo del
    `TestClient`) compartan la misma BD en memoria; si no, cada conexión nueva
    vería una BD vacía distinta.
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def repo(engine):
    """Repositorio de scouting sobre la BD en memoria."""
    return ScoutingRepository(engine)


@pytest.fixture()
def client(engine):
    """TestClient de la app con `get_repository` sobreescrito a la BD en memoria."""
    app = create_app()

    def _override_get_repository():
        yield ScoutingRepository(engine)

    app.dependency_overrides[get_repository] = _override_get_repository
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
