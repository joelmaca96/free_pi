"""Herramienta del plan de rotación contra el rival (`app/assistant/tools/rotation_plan.py`).

Un partido del seed (`g5`, Baskonia en casa contra Valencia) con tramos en los
que Valencia sienta a su estrella (`v1`) en los minutos 8-12 y el Baskonia
mete a `nikos`, `lutse` y `costello` en 600-1200 s. Los demás titulares de
Valencia descansan 6 minutos (más que `v1`), para que `v1` sea su jugador de
más minutos, como en un equipo de verdad.
"""
from sqlalchemy import text

from app.assistant.capabilities import probe
from app.assistant.tools import ToolCatalog

from tests.app.assistant.conftest import add_stints

_START = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
_BENCH = ["howard", "moneke", "nikos", "lutse", "costello"]
_VAL = ["v1", "v2", "v3", "v4", "v5", "v6", "v7"]


def _seed(engine):
    with engine.begin() as conn:
        for i, pid in enumerate(_VAL):
            conn.execute(
                text("INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES (:p, 'val', :n, :i, '')"),
                {"p": pid, "n": f"Valencia {i}", "i": i},
            )
    add_stints(engine, [
        ("g5", "bas", 0.0, 600.0, 16, 10, 0, _START),
        ("g5", "bas", 600.0, 1200.0, 9, 5, 6, _BENCH),
        ("g5", "bas", 1200.0, 2400.0, 40, 40, 10, _START),
        ("g5", "val", 0.0, 420.0, 10, 10, 0, _VAL[:5]),
        ("g5", "val", 420.0, 720.0, 0, 10, 0, ["v6", "v2", "v3", "v4", "v5"]),
        ("g5", "val", 720.0, 1500.0, 45, 45, -10, _VAL[:5]),
        ("g5", "val", 1500.0, 1860.0, 0, 0, -10, ["v1", "v6", "v7", "v4", "v5"]),
        ("g5", "val", 1860.0, 2000.0, 0, 0, -10, _VAL[:5]),
        ("g5", "val", 2000.0, 2360.0, 0, 0, -10, ["v1", "v2", "v3", "v6", "v7"]),
        ("g5", "val", 2360.0, 2400.0, 0, 0, -10, _VAL[:5]),
    ])


def test_rotation_plan_tool_is_not_registered_without_stints(ctx):
    assert "rotation_plan_vs_rival" not in ToolCatalog(ctx).tools


def test_rotation_plan_tool_proposes_our_lineups_for_the_rivals_windows(engine, ctx):
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute(
        "1", "rotation_plan_vs_rival", {"team_id": "val", "is_home": True, "unavailable": ["kotsar"]}
    ).result

    assert "error" not in result, result
    data = result["data"]
    rest = next(w for w in data["windows"] if w["kind"] == "descanso")
    assert rest["label"] == "8-12" and rest["player_name"] == "Valencia 0"
    assert "Valencia 0" not in rest["rival_five"]
    assert rest["lineups"], rest
    for lineup in rest["lineups"]:
        assert "Maik Kotsar" not in lineup["players"]
    assert data["is_home"] is True
    assert any("cuando descansa Valencia 0" in line for line in data["summary"])
    assert any("aditivo" in warning for warning in result["meta"]["warnings"])


def test_rotation_plan_tool_refuses_our_own_team(engine, ctx):
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "rotation_plan_vs_rival", {"team_id": "bas"}).result

    assert result["error"] == "mismo equipo"


def test_rotation_plan_tool_fails_without_our_own_stints(engine, ctx):
    """Regresión: sin tramos propios el "plan" eran solo las ventanas del rival, sin avisar."""
    with engine.begin() as conn:
        for i, pid in enumerate(_VAL):
            conn.execute(
                text("INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES (:p, 'val', :n, :i, '')"),
                {"p": pid, "n": f"Valencia {i}", "i": i},
            )
    add_stints(engine, [
        ("g5", "val", 0.0, 420.0, 10, 10, 0, _VAL[:5]),
        ("g5", "val", 420.0, 720.0, 0, 10, 0, ["v6", "v2", "v3", "v4", "v5"]),
        ("g5", "val", 720.0, 2400.0, 45, 45, -10, _VAL[:5]),
    ])
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "rotation_plan_vs_rival", {"team_id": "val"}).result

    assert result["error"] == "sin tramos propios"


def test_rotation_plan_tool_warns_when_fewer_than_five_are_available(engine, ctx):
    """Regresión: con menos de cinco disponibles no hay quintetos, y el modelo tiene que saberlo."""
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute(
        "1", "rotation_plan_vs_rival", {"team_id": "val", "unavailable": ["kotsar", "codi", "howard", "sedekerskis"]}
    ).result

    assert "error" not in result, result
    assert all(w["lineups"] == [] for w in result["data"]["windows"])
    assert any("Menos de cinco disponibles" in warning for warning in result["meta"]["warnings"])
