"""Tests de la herramienta `game_key_moments` (propuesta 18, hoja de ruta A4).

"¿Qué decidió el partido?" contestada con la MISMA lógica que la pestaña
"Momentos clave" de "Partidos anteriores" (`queries_win_probability`). Se usa
`g5` del seed (bas local, 84-79 a val) con un play-by-play sintético:
igualado hasta el minuto 37 y un 5-0 del Baskonia en el 38, que es lo que
tiene que salir primero.
"""
from sqlalchemy import text

from app.assistant.capabilities import probe
from app.assistant.tools import ToolCatalog

from tests.app.assistant.conftest import add_stints

_EVENTS = (
    "('g5', 'bas', NULL, 'Q1', '08:00', 120,  'dreb', 4,  4),"
    "('g5', 'val', NULL, 'Q2', '05:00', 900,  'dreb', 30, 30),"
    "('g5', 'bas', NULL, 'Q4', '10:00', 1800, 'dreb', 60, 60),"
    "('g5', 'val', NULL, 'Q4', '02:30', 2250, 'dreb', 77, 77),"
    "('g5', 'bas', 'howard', 'Q4', '02:00', 2280, 'steal', 80, 77),"
    "('g5', 'val', NULL, 'Q4', '01:30', 2310, 'turnover', 82, 77),"
    "('g5', 'bas', NULL, 'Q4', '00:10', 2390, 'dreb', 82, 79)"
)


def _load(engine, ctx, with_stints=True):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                f" event_type, home_score, away_score) VALUES {_EVENTS}"
            )
        )
    if with_stints:
        add_stints(engine, [("g5", "bas", 0.0, 2400.0, 84, 79, 0, ["howard", "moneke", "codi", "nikos", "kotsar"])])
    ctx.capabilities = probe(engine)
    return ToolCatalog(ctx)


def test_game_key_moments_is_not_registered_without_play_events(ctx):
    assert "game_key_moments" not in ToolCatalog(ctx).tools


def test_game_key_moments_puts_the_late_swing_first(engine, ctx):
    catalog = _load(engine, ctx)
    assert "game_key_moments" in catalog.tools
    assert catalog.tools["game_key_moments"].family == "team"

    result = catalog.execute("1", "game_key_moments", {"game_id": "g5"}).result
    data = result["data"]
    top = data["momentos"][0]

    assert data["team_id"] == "bas"
    assert data["prob_final_pct"] == 100.0
    assert top["orden"] == 1 and top["desde"].startswith("Q4")
    assert top["marcador_antes"] == "77-77"
    assert top["wpa_pp"] > 0 and top["prob_despues_pct"] > top["prob_antes_pct"]
    assert "Marcus Howard" in top["quinteto_propio"]
    assert data["wpa_quintetos_propios"][0]["quinteto"].count("·") == 4
    assert result["artifact"]["type"] == "table"
    assert result["meta"]["gp"] == 1


def test_game_key_moments_says_what_the_model_does_not_know(engine, ctx):
    catalog = _load(engine, ctx)
    result = catalog.execute("1", "game_key_moments", {"game_id": "g5"}).result
    warnings = " ".join(result["meta"]["warnings"])

    assert "posesión" in warnings
    assert "tiros" in warnings
    # Un solo partido con play-by-play: modelo de reserva, y se dice.
    assert "reserva" in warnings


def test_game_key_moments_from_the_rival_side(engine, ctx):
    catalog = _load(engine, ctx)
    result = catalog.execute("1", "game_key_moments", {"game_id": "g5", "team_id": "val"}).result

    assert result["data"]["prob_final_pct"] == 0.0
    assert result["data"]["momentos"][0]["wpa_pp"] < 0


def test_game_key_moments_warns_without_lineups(engine, ctx):
    catalog = _load(engine, ctx, with_stints=False)
    result = catalog.execute("1", "game_key_moments", {"game_id": "g5"}).result

    assert result["data"]["momentos"][0]["quinteto_propio"] is None
    assert any("tramos de quinteto" in w for w in result["meta"]["warnings"])


def test_game_key_moments_rejects_a_foreign_team_and_an_unknown_game(engine, ctx):
    catalog = _load(engine, ctx)

    foreign = catalog.execute("1", "game_key_moments", {"game_id": "g5", "team_id": "rm"}).result
    assert foreign["error"] == "equipo ajeno al partido"
    unknown = catalog.execute("1", "game_key_moments", {"game_id": "no-existe"}).result
    assert unknown["error"] == "partido desconocido"


def test_game_key_moments_without_play_by_play_in_that_game(engine, ctx):
    catalog = _load(engine, ctx)  # hay play_events, pero solo de g5
    result = catalog.execute("1", "game_key_moments", {"game_id": "g4"}).result

    assert result["error"] == "sin play-by-play"
    assert "game_boxscore" in result["suggestion"]
