"""Tests del endpoint de discovery: POST /discovery/missing-games.

100% offline: `discover_missing_games` de cada pipeline se sustituye por un
doble, así que ningún test golpea la red real ni `data/baskonia.db`.
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.api import ingest_dispatch


@pytest.fixture()
def fake_pipelines(monkeypatch):
    """Dobles de los dos pipelines, con `discover_missing_games` y `run_single_game`."""
    modules = {
        "acb": SimpleNamespace(
            discover_missing_games=mock.Mock(return_value=["105370", "105371"]),
            run_single_game=mock.Mock(),
        ),
        "euroleague": SimpleNamespace(
            discover_missing_games=mock.Mock(return_value=[7, 8]),
            run_single_game=mock.Mock(),
        ),
    }
    monkeypatch.setattr(ingest_dispatch, "PIPELINE_MODULES", modules)
    return modules


def test_discovery_devuelve_ids_prefijados(client, fake_pipelines):
    """Los ids vienen listos para pasarlos a POST /games/{game_id}/refresh."""
    r = client.post("/api/v1/discovery/missing-games?source=acb&season_label=2025-2026")

    assert r.status_code == 200
    assert r.json() == {
        "source": "acb",
        "season_label": "2025-2026",
        "missing_game_ids": ["acb-105370", "acb-105371"],
    }
    _engine, season = fake_pipelines["acb"].discover_missing_games.call_args.args
    assert season == 2025


def test_discovery_prefija_los_game_code_enteros_de_euroliga(client, fake_pipelines):
    """Euroliga devuelve `game_code` entero; el id sale igualmente prefijado."""
    r = client.post("/api/v1/discovery/missing-games?source=euroleague&season_label=2025-2026")

    assert r.status_code == 200
    assert r.json()["missing_game_ids"] == ["euroleague-7", "euroleague-8"]


def test_discovery_no_dispara_ninguna_carga(client, fake_pipelines):
    """Criterio explícito del diseño: discovery solo reporta, nunca carga."""
    client.post("/api/v1/discovery/missing-games?source=acb&season_label=2025-2026")

    fake_pipelines["acb"].run_single_game.assert_not_called()
    fake_pipelines["euroleague"].run_single_game.assert_not_called()


def test_discovery_sin_huecos_devuelve_lista_vacia(client, fake_pipelines):
    """Sin partidos ausentes, `missing_game_ids` es `[]` (no un error)."""
    fake_pipelines["acb"].discover_missing_games.return_value = []

    r = client.post("/api/v1/discovery/missing-games?source=acb&season_label=2025-2026")

    assert r.status_code == 200
    assert r.json()["missing_game_ids"] == []


def test_discovery_fuente_invalida_es_422(client, fake_pipelines):
    """`source` fuera del literal → 422 problem+json (validación de FastAPI)."""
    r = client.post("/api/v1/discovery/missing-games?source=nba&season_label=2025-2026")

    assert r.status_code == 422
    assert r.json()["type"].endswith("validation-error")


def test_discovery_season_label_invalida_es_422(client, fake_pipelines):
    """`season_label` que no cumple el patrón '\\d{4}-\\d{4}' → 422."""
    r = client.post("/api/v1/discovery/missing-games?source=acb&season_label=2025")

    assert r.status_code == 422
    fake_pipelines["acb"].discover_missing_games.assert_not_called()
