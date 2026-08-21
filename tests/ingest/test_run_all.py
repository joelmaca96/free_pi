"""Tests del orquestador `ingest.run_all` (con los tres pipelines mockeados)."""
from unittest import mock

from ingest import run_all as run_all_module


def test_run_all_calls_the_three_modules_in_order(monkeypatch, engine):
    calls = []

    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    fake_baskonia = mock.Mock(side_effect=lambda eng: calls.append("baskonia_web") or {"active": []})
    fake_acb = mock.Mock(side_effect=lambda eng, season: calls.append("acb") or {"loaded": []})
    fake_euro = mock.Mock(side_effect=lambda eng, season: calls.append("euroleague") or {"loaded": []})

    with mock.patch("ingest.baskonia_web.pipeline.run", fake_baskonia), \
         mock.patch("ingest.acb.pipeline.run", fake_acb), \
         mock.patch("ingest.euroleague.pipeline.run", fake_euro):
        results = run_all_module.run_all(season=2025)

    assert calls == ["baskonia_web", "acb", "euroleague"]
    assert all(result["ok"] for result in results.values())


def test_run_all_isolates_failures_between_modules(monkeypatch, engine):
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    with mock.patch("ingest.baskonia_web.pipeline.run", side_effect=RuntimeError("roster caído")), \
         mock.patch("ingest.acb.pipeline.run", return_value={"loaded": ["g1"], "failed": []}), \
         mock.patch("ingest.euroleague.pipeline.run", return_value={"loaded": [], "failed": []}):
        results = run_all_module.run_all(season=2025)

    assert results["baskonia_web"]["ok"] is False
    assert "roster caído" in results["baskonia_web"]["error"]
    assert results["acb"]["ok"] is True
    assert results["euroleague"]["ok"] is True


def test_run_all_respects_skip(monkeypatch, engine):
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    with mock.patch("ingest.acb.pipeline.run") as fake_acb:
        results = run_all_module.run_all(season=2025, skip=("acb", "euroleague", "baskonia_web"))

    fake_acb.assert_not_called()
    assert all(result["ok"] is None for result in results.values())
