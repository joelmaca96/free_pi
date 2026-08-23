"""Tests del endpoint de refresco bajo demanda: POST /games/{game_id}/refresh.

100% offline: los dos módulos de pipeline se sustituyen por dobles
(`apps.api.ingest_dispatch.PIPELINE_MODULES` monkeypatcheado), así que ningún
test toca la red real ni `data/baskonia.db`.
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import text

from apps.api import ingest_dispatch
from apps.api.routers import refresh


@pytest.fixture(autouse=True)
def _clean_inflight():
    """El guardia `_INFLIGHT` es estado de módulo: se limpia entre tests."""
    refresh._INFLIGHT.clear()
    yield
    refresh._INFLIGHT.clear()


@pytest.fixture()
def fake_pipelines(monkeypatch):
    """Sustituye los pipelines reales por dobles y devuelve los mocks por fuente."""
    modules = {
        source: SimpleNamespace(run_single_game=mock.Mock(return_value={"loaded": [], "failed": []}))
        for source in ("acb", "euroleague")
    }
    monkeypatch.setattr(ingest_dispatch, "PIPELINE_MODULES", modules)
    return modules


@pytest.fixture()
def acb_game(engine):
    """Inserta un partido ACB real (id prefijado) en la BD en memoria."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id,"
                " away_team_id, game_date, home_score, away_score, pace)"
                " VALUES ('acb-105370', 1, 1, 'bas', 'rm', '2026-02-14', 90, 85, 72.0)"
            )
        )
    return "acb-105370"


def test_refresh_partido_existente_devuelve_202_triggered(client, fake_pipelines, acb_game):
    """Partido ya cargado: 202 `triggered` y temporada resuelta desde la fila."""
    r = client.post(f"/api/v1/games/{acb_game}/refresh")

    assert r.status_code == 202
    assert r.json() == {"game_id": acb_game, "status": "triggered"}

    run_single_game = fake_pipelines["acb"].run_single_game
    run_single_game.assert_called_once()
    _engine, season, external_id = run_single_game.call_args.args
    assert (season, external_id) == (2025, "105370")  # '2025-2026' -> 2025


def test_refresh_con_season_label_carga_un_partido_que_aun_no_existe(client, fake_pipelines):
    """Partido descubierto por discovery (no está en `games`): basta `season_label`."""
    r = client.post("/api/v1/games/euroleague-7/refresh?season_label=2025-2026")

    assert r.status_code == 202
    assert r.json()["status"] == "triggered"

    _engine, season, external_id = fake_pipelines["euroleague"].run_single_game.call_args.args
    assert season == 2025
    assert external_id == 7  # Euroliga usa game_code entero


def test_refresh_partido_inexistente_sin_season_label_es_404(client, fake_pipelines):
    """Sin fila y sin `season_label` no hay temporada resoluble: 404 problem+json."""
    r = client.post("/api/v1/games/acb-999999/refresh")

    assert r.status_code == 404
    assert r.json()["type"].endswith("game-not-found")
    fake_pipelines["acb"].run_single_game.assert_not_called()


def test_refresh_fuente_no_soportada_es_400(client, fake_pipelines):
    """Un `game_id` sin prefijo de fuente soportada (seed 'g1') → 400."""
    r = client.post("/api/v1/games/g1/refresh")

    assert r.status_code == 400
    assert r.json()["type"].endswith("invalid-filter")

    r = client.post("/api/v1/games/bbr-123/refresh?season_label=2025-2026")
    assert r.status_code == 400


def test_refresh_duplicado_devuelve_already_in_progress(client, fake_pipelines, acb_game):
    """Con un refresco ya en vuelo para ese partido no se encola otro."""
    import time

    refresh._INFLIGHT[acb_game] = time.monotonic()

    r = client.post(f"/api/v1/games/{acb_game}/refresh")

    assert r.status_code == 202
    assert r.json()["status"] == "already_in_progress"
    fake_pipelines["acb"].run_single_game.assert_not_called()


def test_refresh_rechaza_cuando_se_alcanza_el_limite_de_concurrencia(
    client, fake_pipelines, acb_game, monkeypatch
):
    """Con `refresh_max_concurrent` refrescos en vuelo, uno nuevo se rechaza."""
    import time

    from apps.api.settings import settings

    monkeypatch.setattr(settings, "refresh_max_concurrent", 2)
    refresh._INFLIGHT["acb-1"] = time.monotonic()
    refresh._INFLIGHT["acb-2"] = time.monotonic()

    r = client.post(f"/api/v1/games/{acb_game}/refresh")

    assert r.status_code == 202
    assert r.json()["status"] == "rejected_busy"
    fake_pipelines["acb"].run_single_game.assert_not_called()


def test_refresh_excepcion_en_background_no_propaga_ni_deja_guardia_huerfano(
    client, fake_pipelines, acb_game
):
    """Un fallo del pipeline se captura dentro de la tarea y libera `_INFLIGHT`."""
    fake_pipelines["acb"].run_single_game.side_effect = RuntimeError("boom")

    r = client.post(f"/api/v1/games/{acb_game}/refresh")

    assert r.status_code == 202  # la respuesta ya se envió antes de fallar
    # `TestClient` ejecuta las BackgroundTasks antes de devolver la respuesta.
    assert refresh._INFLIGHT == {}


def test_refresh_libera_el_guardia_tras_un_refresco_correcto(client, fake_pipelines, acb_game):
    """Tras terminar el trabajo, el partido vuelve a ser refrescable."""
    r = client.post(f"/api/v1/games/{acb_game}/refresh")

    assert r.json()["status"] == "triggered"
    assert refresh._INFLIGHT == {}
