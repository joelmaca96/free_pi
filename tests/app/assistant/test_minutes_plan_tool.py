"""Herramienta del planificador de minutos (`app/assistant/tools/minutes_plan.py`, propuesta 17)."""
from sqlalchemy import text

from app.analytics import minutes_plan as mp
from app.assistant.capabilities import probe
from app.assistant.tools import ToolCatalog

from tests.app.assistant.conftest import add_stints

_STARTERS = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
_BENCH = ["howard", "nikos", "moneke", "lutse", "costello"]
_VAL = ["v1", "v2", "v3", "v4", "v5"]


def _seed(engine):
    """Mismos tramos que `test_rotation_tools.py`: `g5` (2026-01-18) del seed, Baskonia contra Valencia."""
    with engine.begin() as conn:
        for i, pid in enumerate(_VAL):
            conn.execute(
                text("INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES (:p, 'val', :n, :i, '')"),
                {"p": pid, "n": f"Valencia {i}", "i": i},
            )
    add_stints(engine, [
        ("g5", "bas", 0.0, 600.0, 20, 10, 0, _STARTERS),
        ("g5", "bas", 600.0, 900.0, 4, 10, 10, _BENCH),
        ("g5", "bas", 900.0, 2400.0, 60, 60, 4, _STARTERS),
        ("g5", "val", 0.0, 2400.0, 80, 84, 0, _VAL),
    ])


def _plan(result) -> dict:
    return {row["player_id"]: row for row in result["data"]["plan"]}


def test_minutes_plan_is_not_registered_without_stints(ctx):
    assert "minutes_plan" not in ToolCatalog(ctx).tools


def test_minutes_plan_hands_out_200_minutes_and_respects_the_coach(engine, ctx):
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute(
        "1", "minutes_plan",
        {"unavailable": ["kotsar"], "max_minutes": {"howard": 20}, "min_minutes": {"lutse": 25}},
    ).result

    assert "error" not in result, result
    plan = _plan(result)
    assert sum(row["planned_minutes"] for row in plan.values()) == 200
    assert plan["kotsar"]["planned_minutes"] == 0
    assert plan["kotsar"]["binding"] == mp.BIND_UNAVAILABLE
    assert plan["howard"]["planned_minutes"] <= 20
    assert plan["howard"]["max_minutes"] == 20
    assert plan["lutse"]["planned_minutes"] >= 25
    # Sin Kotsar, el único pívot es Costello: la cobertura le da al menos 40… que no caben (tope 32),
    # así que su tope sugerido se sube lo justo y se avisa.
    assert plan["costello"]["planned_minutes"] >= 40 - 1e-9
    assert any("se han subido" in warning for warning in result["meta"]["warnings"])
    assert any("aditivo" in warning for warning in result["meta"]["warnings"])
    assert result["data"]["position_floors"] == {"Base": 40.0, "Pívot": 40.0}


def test_minutes_plan_caps_a_player_on_short_rest(engine, ctx):
    """Partido al día siguiente de `g5`, donde Howard jugó 31 minutos: tope de descanso corto."""
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "minutes_plan", {"game_date": "2026-01-19"}).result

    assert "error" not in result, result
    assert result["data"]["rest_days"] == 1
    howard = _plan(result)["howard"]
    assert howard["max_minutes"] == 28
    assert howard["binding"] == mp.BIND_CAP_SHORT_REST
    assert howard["explanation"] == "Marcus Howard: 30 → 28 min (tope por descanso corto: 1 día de descanso tras 31 min)"


def test_minutes_plan_rejects_a_bad_date(engine, ctx):
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "minutes_plan", {"game_date": "domingo"}).result

    assert result["error"] == "fecha inválida"
