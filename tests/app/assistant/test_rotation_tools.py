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
    # La temporada del seed no tiene anterior: sin prior y sin aviso de prior.
    assert result["data"]["prior_season"] is None
    assert not any("punto de partida" in warning for warning in result["meta"]["warnings"])


def _seed_league_season(engine):
    """Tramos en la temporada sintética (2): `bas` recibe a `val` en `syn-2-3` (ver `_add_league`)."""
    add_stints(engine, [
        ("syn-2-3", "bas", 0.0, 1200.0, 30, 20, 0, _STARTERS),
        ("syn-2-3", "bas", 1200.0, 2400.0, 20, 26, 10, _BENCH),
        ("syn-2-3", "val", 0.0, 2400.0, 46, 50, 0, _VAL),
    ])


def test_lineup_builder_uses_the_previous_season_as_prior_by_default(engine, league_ctx):
    _seed(engine)                 # temporada 1: la anterior
    _seed_league_season(engine)   # temporada 2: la que se pregunta
    league_ctx.capabilities = probe(engine)

    result = ToolCatalog(league_ctx).execute("1", "lineup_builder", {"team_id": "bas"}).result

    assert "error" not in result, result
    assert result["data"]["prior_season"] == "2025-2026"
    assert any("2025-2026" in w and "punto de partida" in w for w in result["meta"]["warnings"])
    howard = next(row for row in result["data"]["player_impact"] if row["player_id"] == "howard")
    assert {"rapm_no_prior", "prior"} <= set(howard)
    assert howard["prior"] is not None


def test_lineup_builder_can_ignore_the_previous_season(engine, league_ctx):
    _seed(engine)
    _seed_league_season(engine)
    league_ctx.capabilities = probe(engine)

    result = ToolCatalog(league_ctx).execute(
        "1", "lineup_builder", {"team_id": "bas", "use_prior": False}
    ).result

    assert "error" not in result, result
    assert result["data"]["prior_season"] is None
    assert not any("punto de partida" in warning for warning in result["meta"]["warnings"])
    assert "prior" not in result["data"]["player_impact"][0]
