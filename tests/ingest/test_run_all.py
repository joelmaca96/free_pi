"""Tests del orquestador `ingest.run_all` (con los tres pipelines mockeados)."""
from unittest import mock

from ingest import run_all as run_all_module


def test_run_all_calls_the_three_modules_in_order(monkeypatch, engine):
    calls = []

    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    fake_baskonia = mock.Mock(side_effect=lambda eng: calls.append("baskonia_web") or {"active": []})
    fake_acb = mock.Mock(side_effect=lambda eng, season: calls.append("acb") or {"loaded": []})
    fake_acb_upcoming = mock.Mock(side_effect=lambda eng, season: calls.append("acb_upcoming") or {"upcoming": 0})
    fake_euro = mock.Mock(side_effect=lambda eng, season: calls.append("euroleague") or {"loaded": []})
    fake_euro_upcoming = mock.Mock(
        side_effect=lambda eng, season: calls.append("euroleague_upcoming") or {"upcoming": 0}
    )
    # La ficha física también se mockea: sin esto salía a api-live.euroleague.net
    # DE VERDAD durante los tests (y volvía la suite roja con un 429 cuando el
    # servidor limitaba). La suite es 100% offline, ver README §6.
    fake_euro_roster = mock.Mock(
        side_effect=lambda eng, season: calls.append("euroleague_roster") or {"clubs": 0, "players": 0}
    )

    with mock.patch("ingest.baskonia_web.pipeline.run", fake_baskonia), \
         mock.patch("ingest.acb.pipeline.run", fake_acb), \
         mock.patch("ingest.acb.pipeline.run_upcoming", fake_acb_upcoming), \
         mock.patch("ingest.euroleague.pipeline.run", fake_euro), \
         mock.patch("ingest.euroleague.pipeline.run_upcoming", fake_euro_upcoming), \
         mock.patch("ingest.euroleague.roster.run", fake_euro_roster):
        results = run_all_module.run_all(season=2025)

    assert calls == [
        "baskonia_web", "acb", "acb_upcoming", "euroleague", "euroleague_upcoming", "euroleague_roster",
    ]
    assert all(result["ok"] for result in results.values())


def test_run_all_isolates_failures_between_modules(monkeypatch, engine):
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    with mock.patch("ingest.baskonia_web.pipeline.run", side_effect=RuntimeError("roster caído")), \
         mock.patch("ingest.acb.pipeline.run", return_value={"loaded": ["g1"], "failed": []}), \
         mock.patch("ingest.acb.pipeline.run_upcoming", return_value={"upcoming": 0}), \
         mock.patch("ingest.euroleague.pipeline.run", return_value={"loaded": [], "failed": []}), \
         mock.patch("ingest.euroleague.pipeline.run_upcoming", return_value={"upcoming": 0}), \
         mock.patch("ingest.euroleague.roster.run", return_value={"clubs": 0, "players": 0}):
        results = run_all_module.run_all(season=2025)

    assert results["baskonia_web"]["ok"] is False
    assert "roster caído" in results["baskonia_web"]["error"]
    assert results["acb"]["ok"] is True
    assert results["acb_upcoming"]["ok"] is True
    assert results["euroleague"]["ok"] is True
    assert results["euroleague_upcoming"]["ok"] is True


def test_run_all_isolates_acb_upcoming_from_acb_finished(monkeypatch, engine):
    """Un fallo refrescando el calendario futuro no debe tumbar el backfill de
    partidos ya finalizados que se acaba de cargar en la misma llamada (WP
    separado a propósito en `run_all.py`, ver su comentario)."""
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    with mock.patch("ingest.acb.pipeline.run", return_value={"loaded": ["g1"], "failed": []}), \
         mock.patch("ingest.acb.pipeline.run_upcoming", side_effect=RuntimeError("ACB caído")), \
         mock.patch("ingest.baskonia_web.pipeline.run", return_value={"active": []}), \
         mock.patch("ingest.euroleague.pipeline.run", return_value={"loaded": [], "failed": []}), \
         mock.patch("ingest.euroleague.pipeline.run_upcoming", return_value={"upcoming": 0}), \
         mock.patch("ingest.euroleague.roster.run", return_value={"clubs": 0, "players": 0}):
        results = run_all_module.run_all(season=2025)

    assert results["acb"]["ok"] is True
    assert results["acb_upcoming"]["ok"] is False
    assert "ACB caído" in results["acb_upcoming"]["error"]


def test_run_all_isolates_euroleague_upcoming_from_euroleague_finished(monkeypatch, engine):
    """Mismo criterio que `test_run_all_isolates_acb_upcoming_from_acb_finished`,
    para la fuente Euroliga."""
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    with mock.patch("ingest.baskonia_web.pipeline.run", return_value={"active": []}), \
         mock.patch("ingest.acb.pipeline.run", return_value={"loaded": [], "failed": []}), \
         mock.patch("ingest.acb.pipeline.run_upcoming", return_value={"upcoming": 0}), \
         mock.patch("ingest.euroleague.pipeline.run", return_value={"loaded": ["g1"], "failed": []}), \
         mock.patch("ingest.euroleague.pipeline.run_upcoming", side_effect=RuntimeError("Euroliga caído")), \
         mock.patch("ingest.euroleague.roster.run", return_value={"clubs": 0, "players": 0}):
        results = run_all_module.run_all(season=2025)

    assert results["euroleague"]["ok"] is True
    assert results["euroleague_upcoming"]["ok"] is False
    assert "Euroliga caído" in results["euroleague_upcoming"]["error"]


def test_run_all_respects_skip(monkeypatch, engine):
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    with mock.patch("ingest.acb.pipeline.run") as fake_acb:
        results = run_all_module.run_all(season=2025, skip=("acb", "euroleague", "baskonia_web"))

    fake_acb.assert_not_called()
    # `identity_check` corre SIEMPRE, incluso con todo lo demás omitido (es
    # una comprobación de lo que ya hay en la BD, no de lo que se acaba de
    # ingerir) — se comprueba aparte, ver `test_run_all_always_runs_the_identity_check`.
    pipeline_results = {name: result for name, result in results.items() if name != "identity_check"}
    assert all(result["ok"] is None for result in pipeline_results.values())


def test_run_all_always_runs_the_identity_check_even_with_everything_skipped(monkeypatch, engine):
    """La comprobación de integridad de identidad de club (§5 de
    `doc/features/propuestas/04_fatiga_y_calendario.md`) no depende de qué
    pipeline se acabe de correr: lee lo que YA hay en `teams`, así que debe
    reportarse aunque `--skip` deje fuera los tres módulos."""
    monkeypatch.setattr(run_all_module, "get_engine", lambda database_url=None: engine)

    results = run_all_module.run_all(season=2025, skip=("acb", "euroleague", "baskonia_web"))

    assert results["identity_check"]["ok"] is True  # el seed de test no tiene clubes duplicados
    assert "sin colisiones" in results["identity_check"]["summary"]


# --- código de salida de la CLI -------------------------------------------
# `baskonia-ingest.service` es `Type=oneshot` (ver deploy/systemd/): si la CLI
# sale siempre con 0, systemd marca la unidad como correcta aunque no haya
# entrado un solo partido, y la ingesta se queda muerta en silencio.


def _run_main(monkeypatch, results):
    monkeypatch.setattr(run_all_module, "run_all", lambda *a, **k: results)
    monkeypatch.setattr(run_all_module, "configure_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["run_all", "--season", "2025"])
    run_all_module.main()


def test_cli_exits_non_zero_when_a_module_failed(monkeypatch, capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, {
            "baskonia_web": {"ok": True, "summary": {}},
            "acb": {"ok": False, "error": "x-apikey caducada"},
        })

    assert exc.value.code != 0
    assert "acb" in str(exc.value.code)


def test_cli_exits_zero_when_everything_ran(monkeypatch):
    """Módulos omitidos con `--skip` y un AVISO de identidad no son un fallo de ingesta:
    el primero es lo que se ha pedido y el segundo es algo que revisar, no algo que no se
    haya cargado."""
    _run_main(monkeypatch, {
        "acb": {"ok": True, "summary": {}},
        "euroleague": {"ok": None, "summary": "omitido"},
        "identity_check": {"ok": False, "summary": "1 de club, 0 de jugador: ['baskonia']"},
    })  # sin SystemExit
