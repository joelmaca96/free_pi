"""Fixtures para los tests de los módulos de ingesta.

BD de scouting en memoria por test (esquema + seed reales), aislada de
`data/baskonia.db`.
"""
import pytest

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()
