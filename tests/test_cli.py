"""Tests para `apps/ingest/cli.py` y el puente `main.py` (Fase F4).

Cubre el contrato CLI de la migración F4:

- `main()` construye el parser con los flags `--refresh-teams` y `--fix-league`
  (requisito del gate de salida) y delega correctamente en `run()` /
  `backfill_league()` de `pipeline.py`.
- El puente `main.py` de la raíz importa y delega en `apps.ingest.cli.main`.

Herméticos: sin red y sin tocar `data/baskonia.db` real. Se mockean las
funciones de `pipeline.py` que harían trabajo real (red/BD).
"""
import importlib
import sys
from unittest import mock

import pytest

import apps.ingest.cli as cli


@pytest.fixture()
def mock_pipeline():
    """Mockea las funciones de pipeline.py que `cli.main()` delega.

    Devuelve una tupla `(run, backfill_league)` para poder verificar qué
    función se invocó y con qué argumentos, sin ejecutar red ni BD.
    """
    with mock.patch.object(cli, "run") as run, mock.patch.object(
        cli, "backfill_league"
    ) as backfill_league:
        yield run, backfill_league


def _run_cli(*argv):
    """Ejecuta `cli.main()` con un `sys.argv` controlado."""
    with mock.patch.object(sys, "argv", ["apps.ingest.cli", *argv]):
        cli.main()


# --- Parser: flags del gate de salida --------------------------------------

def test_cli_help_shows_gate_flags(capsys):
    """`--help` muestra `--refresh-teams` y `--fix-league` (gate de F4)."""
    with mock.patch.object(sys, "argv", ["apps.ingest.cli", "--help"]):
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--refresh-teams" in out
    assert "--fix-league" in out


# --- Delegación de main() ---------------------------------------------------

def test_cli_default_calls_run_without_refresh(mock_pipeline):
    """Sin flags, `main()` delega en `run(refresh_teams=False)`."""
    run, backfill_league = mock_pipeline
    _run_cli()
    run.assert_called_once_with(refresh_teams=False)
    backfill_league.assert_not_called()


def test_cli_refresh_teams_calls_run_with_refresh(mock_pipeline):
    """`--refresh-teams` delega en `run(refresh_teams=True)`."""
    run, backfill_league = mock_pipeline
    _run_cli("--refresh-teams")
    run.assert_called_once_with(refresh_teams=True)
    backfill_league.assert_not_called()


def test_cli_fix_league_calls_backfill(mock_pipeline):
    """`--fix-league` delega en `backfill_league()` y no en `run()`."""
    run, backfill_league = mock_pipeline
    _run_cli("--fix-league")
    backfill_league.assert_called_once_with()
    run.assert_not_called()


# --- Puente main.py (raíz) --------------------------------------------------

def test_main_bridge_delegates_to_cli_main():
    """El puente `main.py` importa y delega en `apps.ingest.cli.main`.

    Al importar `main.py` se ejecuta su cuerpo (el puente llama a `main()`).
    Se mockea `apps.ingest.cli.main` antes de importar para que el puente
    delegue en el mock y no ejecute el pipeline real.
    """
    with mock.patch.object(cli, "main") as cli_main_mock:
        # Fuerza a re-importar main.py para que ejecute el puente con el mock
        sys.modules.pop("main", None)
        importlib.import_module("main")
        cli_main_mock.assert_called_once_with()
