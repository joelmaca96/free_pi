"""Tests del arnés de paridad (Fase F0).

Verifica dos propiedades del arnés `tools/parity_dump.py`:

1. **Determinismo**: dos ejecuciones de `build_dump` con la misma fecha de
   referencia producen JSON canónico idéntico byte a byte (el gate de salida
   de F0 exige que dos ejecuciones seguidas den ficheros idénticos).
2. **Línea base presente**: los ficheros de `tests/parity/baseline/` existen
   y son JSON válidos.

Los tests usan la BD SQLite en memoria de `conftest.py` (herméticos, sin tocar
`data/baskonia.db` real ni hacer peticiones de red).
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from tools import parity_dump

BASELINE_DIR = Path(__file__).parent / "parity" / "baseline"

# Combinaciones de línea base definidas en 01_design.md
BASELINE_FILES = [
    "vitoria-2025-all-last5.json",
    "vitoria-2025-euroleague-last5.json",
    "vitoria-2026-all-last5.json",
    "gran-canaria-2025-all-last3.json",
]


def _canonical(dump):
    """Serializa un dump a JSON canónico (misma función que el arnés)."""
    return parity_dump._canonical_json(dump)


def test_build_dump_is_deterministic(session, teams, played_game):
    """Dos ejecuciones con la misma fecha de referencia dan JSON idéntico."""
    ref = datetime(2026, 8, 19)
    team = teams["vitoria"]

    dump1 = parity_dump.build_dump(
        session, team, season=2025, league=None, last_n=5, reference_date=ref
    )
    dump2 = parity_dump.build_dump(
        session, team, season=2025, league=None, last_n=5, reference_date=ref
    )

    json1 = _canonical(dump1)
    json2 = _canonical(dump2)
    assert json1 == json2
    # Hash idéntico (gate de salida de F0)
    assert hashlib.sha256(json1.encode("utf-8")).hexdigest() == hashlib.sha256(
        json2.encode("utf-8")
    ).hexdigest()


def test_build_dump_contains_all_parity_keys(session, teams, played_game):
    """El dump expone las 13 salidas de paridad definidas en 02_migration.md."""
    ref = datetime(2026, 8, 19)
    dump = parity_dump.build_dump(
        session, teams["vitoria"], season=2025, league=None, last_n=5, reference_date=ref
    )
    expected = {
        "past_games",
        "upcoming_games",
        "team_summary_df",
        "recent_games_df",
        "recent_form_df",
        "streaks_df",
        "schedule_difficulty_df",
        "player_load_df",
        "head_to_head_summary_df",
        "boxscore_df",
        "team_advanced_summary",
        "project_next_matchup",
        "scouting_narrative",
    }
    assert expected.issubset(dump.keys())


def test_build_dump_rounds_floats_to_4_decimals(session, teams, played_game):
    """Los flotantes del dump van redondeados a 4 decimales (JSON canónico)."""
    ref = datetime(2026, 8, 19)
    dump = parity_dump.build_dump(
        session, teams["vitoria"], season=2025, league=None, last_n=5, reference_date=ref
    )
    raw = json.dumps(dump)

    def _check(value):
        if isinstance(value, float):
            assert round(value, 4) == value, f"flotante no redondeado: {value}"
        elif isinstance(value, dict):
            for v in value.values():
                _check(v)
        elif isinstance(value, list):
            for v in value:
                _check(v)

    _check(dump)


def test_baseline_files_exist_and_are_valid_json():
    """Los 4 ficheros de línea base existen y son JSON válidos."""
    for name in BASELINE_FILES:
        path = BASELINE_DIR / name
        assert path.exists(), f"Falta línea base: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "meta" in data
        assert "team_advanced_summary" in data
