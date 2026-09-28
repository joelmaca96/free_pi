"""Herramienta del planificador de minutos (`app/assistant/tools/minutes_plan.py`, propuesta 17)."""
import numpy as np
import pytest
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


def test_minutes_plan_drops_the_coverage_of_a_position_with_nobody_available(engine, ctx):
    """Sin ningún pívot disponible, la cobertura de pívot es imposible: se quita y se avisa, no falla."""
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "minutes_plan", {"unavailable": ["kotsar", "costello"]}).result

    assert "error" not in result, result
    assert result["data"]["position_floors"] == {"Base": 40.0}
    assert any("Pívot" in warning and "no se exige" in warning for warning in result["meta"]["warnings"])
    assert sum(row["planned_minutes"] for row in result["data"]["plan"]) == 200


def test_minutes_plan_warns_about_ids_outside_the_roster(engine, ctx):
    """Un nombre en vez de un player_id no puede ignorarse en silencio: el plan saldría con él dentro."""
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "minutes_plan", {"unavailable": ["Kotsar"]}).result

    assert "error" not in result, result
    assert any("Kotsar" in warning and "ignorado" in warning for warning in result["meta"]["warnings"])


def test_minutes_plan_raises_a_suggested_cap_up_to_the_coach_minimum(engine, ctx):
    """Descanso corto deja a Howard en 28; si el entrenador le pide 32 de mínimo, manda el mínimo."""
    _seed(engine)
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute(
        "1", "minutes_plan", {"game_date": "2026-01-19", "min_minutes": {"howard": 32}}
    ).result

    assert "error" not in result, result
    howard = _plan(result)["howard"]
    assert howard["planned_minutes"] == 32
    assert howard["max_minutes"] == 32


def test_minutes_plan_output_is_valid_json_when_nobody_available_has_recent_minutes(engine, ctx):
    """Cinco fichajes sin partidos y el resto fuera: el margen "con el reparto reciente" es `None`, no `NaN`."""
    import json

    _seed(engine)
    new = [f"new{i}" for i in range(5)]
    with engine.begin() as conn:
        for i, pid in enumerate(new):
            conn.execute(
                text("INSERT INTO players (id, team_id, name, number, position, active)"
                     " VALUES (:p, 'bas', :n, :i, 'Alero', 1)"),
                {"p": pid, "n": f"Fichaje {i}", "i": 90 + i},
            )
    ctx.capabilities = probe(engine)
    regulars = ["howard", "moneke", "codi", "sedekerskis", "kotsar", "nikos", "lutse", "costello"]

    result = ToolCatalog(ctx).execute(
        "1", "minutes_plan", {"unavailable": regulars, "max_minutes": {pid: 40 for pid in new}},
    ).result

    assert "error" not in result, result
    assert result["data"]["recent_distribution_margin"] is None
    json.dumps(result, allow_nan=False)  # no lanza


def _screen(url: str, season_id: int) -> None:
    """Script de `AppTest`: solo la sección del planificador, sin el resto de la pantalla."""
    from packages.baskonia_core.db.scouting import create_scouting_engine

    from app.components.minutes_plan import minutes_plan_section

    minutes_plan_section(create_scouting_engine(url), "bas", season_id, None, "Baskonia", {})


def test_screen_section_uses_the_rapm_of_the_prior_checkbox(engine):
    """La tabla del planificador enseña el MISMO RAPM que la sección de RAPM de encima.

    Esa sección guarda su casilla "Usar la temporada anterior" en
    `st.session_state["impact_use_prior"]`; el planificador pedía siempre el
    ajuste con prior, así que con la casilla quitada sus RAPM no cuadraban.
    """
    from streamlit.testing.v1 import AppTest

    from app.data import queries_assistant

    _seed(engine)  # temporada 1 (la del seed) con tramos: será el prior de la 2
    with engine.begin() as conn:
        game_id = conn.execute(text(
            "SELECT id FROM games WHERE season_id = 2 AND 'bas' IN (home_team_id, away_team_id) ORDER BY id LIMIT 1"
        )).scalar()
        rival = conn.execute(text(
            "SELECT CASE home_team_id WHEN 'bas' THEN away_team_id ELSE home_team_id END FROM games WHERE id = :g"
        ), {"g": game_id}).scalar()
    add_stints(engine, [
        (game_id, "bas", 0.0, 1200.0, 30, 20, 0, _STARTERS),
        (game_id, "bas", 1200.0, 2400.0, 20, 30, 10, _BENCH),
        (game_id, rival, 0.0, 2400.0, 50, 50, 0, _VAL),
    ])
    url = str(engine.url)
    shown = {}
    for use_prior in (True, False):
        at = AppTest.from_function(_screen, args=(url, 2), default_timeout=60)
        at.session_state["impact_use_prior"] = use_prior
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        table = at.dataframe[0].value  # el editor de disponibles y topes
        fit = queries_assistant.season_impact(engine, 2, None, use_prior=use_prior)["fit"]
        assert fit["prior_used"] is use_prior  # premisa: hay prior y la casilla lo quita
        expected = fit["players"].set_index("player_id")["rapm"]
        rows = table.set_index("player_id")
        for pid in ["codi", "nikos", "costello"]:
            assert rows.loc[pid, "rapm"] == pytest.approx(expected[pid], abs=1e-6)
        shown[use_prior] = rows["rapm"]
    assert not np.allclose(shown[True].sort_index(), shown[False].sort_index())


def test_minutes_plan_follows_use_prior(engine, ctx, monkeypatch):
    """Mismo interruptor que `lineup_builder`: `use_prior` llega a `season_impact`."""
    from app.assistant.tools import minutes_plan as tool

    _seed(engine)
    ctx.capabilities = probe(engine)
    seen = []
    real = tool.queries_assistant.season_impact

    def spy(engine_, season, competition, use_prior=True):
        seen.append(use_prior)
        return real(engine_, season, competition, use_prior=use_prior)

    monkeypatch.setattr(tool.queries_assistant, "season_impact", spy)
    ToolCatalog(ctx).execute("1", "minutes_plan", {"use_prior": False})
    ToolCatalog(ctx).execute("1", "minutes_plan", {})

    assert seen == [False, True]
