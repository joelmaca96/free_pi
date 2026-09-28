"""Herramientas de patrón de rotación y constructor de quintetos (`app/assistant/tools/rotations.py`)."""
from sqlalchemy import text

from app.assistant.capabilities import probe
from app.assistant.tools import ToolCatalog

from tests.app.assistant.conftest import add_stints

_STARTERS = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
_BENCH = ["howard", "nikos", "moneke", "lutse", "costello"]
_VAL = ["v1", "v2", "v3", "v4", "v5"]


def _seed(engine):
    with engine.begin() as conn:
        for i, pid in enumerate(_VAL):
            conn.execute(
                text("INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES (:p, 'val', :n, :i, '')"),
                {"p": pid, "n": f"Valencia {i}", "i": i},
            )
    rows = []
    for game_id in ("g5",):
        rows += [
            (game_id, "bas", 0.0, 600.0, 20, 10, 0, _STARTERS),
            (game_id, "bas", 600.0, 900.0, 4, 10, 10, _BENCH),
            (game_id, "bas", 900.0, 2400.0, 60, 60, 4, _STARTERS),
            (game_id, "val", 0.0, 2400.0, 80, 84, 0, _VAL),
        ]
    add_stints(engine, rows)


def test_rotation_tools_are_not_registered_without_stints(ctx):
    tools = ToolCatalog(ctx).tools

    assert "team_rotation_pattern" not in tools
    assert "lineup_builder" not in tools


def test_team_rotation_pattern_answers_with_a_summary(engine, ctx):
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "team_rotation_pattern", {"team_id": "bas"}).result

    data = result["data"]
    assert data["games"] == 1
    assert data["starting_lineups"][0]["games"] == 1
    assert any("Quinteto inicial más probable" in line for line in data["summary"])
    assert len(data["blocks"]) == 10


def test_lineup_builder_respects_unavailable_players(engine, ctx):
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute(
        "1", "lineup_builder", {"team_id": "bas", "unavailable": ["kotsar"], "limit": 3}
    ).result

    assert "error" not in result, result
    names = {row["player_name"] for row in result["data"]["player_impact"]}
    assert "Maik Kotsar" in names  # su impacto se sigue enseñando…
    for lineup in result["data"]["best_lineups"]:
        assert "Maik Kotsar" not in lineup["players"]  # …pero no entra en ningún quinteto
    assert any("aditivo" in warning for warning in result["meta"]["warnings"])
