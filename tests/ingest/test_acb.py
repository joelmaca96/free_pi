"""Tests de la fuente ACB: transform-and-load con un partido de ejemplo (fixture)."""
from unittest import mock

from sqlalchemy import text

from ingest.acb.parser import parse_and_resolve
from ingest.acb.pipeline import discover_missing_games, run, run_single_game
from ingest.common.loader import load_game

RAW_GAME = {
    "game_id": "2025123401",
    "date": "2026-02-14",
    "season": 2025,
    "competition": "ACB",
    "home_team": {"id": "acb-bas", "name": "Kosner Baskonia"},
    "away_team": {"id": "acb-uni", "name": "Unicaja"},
    "home_score": 90,
    "away_score": 85,
    "pace": 73.0,
    "narrative": "Partido de ejemplo (fixture ACB).",
    "team_stats": [
        {"team_id": "acb-bas", "efg_pct": 56.0, "ts_pct": 59.0, "tov_pct": 11.0, "orb_pct": 26.0, "ortg": 114.0, "drtg": 109.0},
        {"team_id": "acb-uni", "efg_pct": 52.0, "ts_pct": 54.0, "tov_pct": 13.0, "orb_pct": 22.0, "ortg": 109.0, "drtg": 114.0},
    ],
    "players": [
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "name": "Marcus Howard", "number": 0, "position": "Base",
         "minutes": 30.0, "pts": 22, "reb": 3, "ast": 6, "efg_pct": 60.0},
        {"player_id": "acb-p-rival1", "team_id": "acb-uni", "name": "Jugador Rival", "number": 5, "position": "Alero",
         "minutes": 28.0, "pts": 15, "reb": 4, "ast": 2, "efg_pct": 48.0},
    ],
    "lineups": [
        {"team_id": "acb-bas", "player_ids": ["acb-p-howard"], "minutes": 5.0, "plus_minus": 3},
    ],
    "shots": [
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "x": 250.0, "y": 480.0, "made": True},
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "x": 260.0, "y": 460.0, "made": False},
    ],
    "events": [
        {"team_id": "acb-bas", "quarter": "Q4", "clock": "00:40", "label": "Triple de Howard"},
    ],
    "score_progression": [
        {"step": 0, "home": 0, "away": 0},
        {"step": 1, "home": 3, "away": 0},
    ],
}


def test_parse_and_resolve_reuses_baskonia_team_and_player(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)

    assert game.id == "acb-2025123401"
    assert game.home_team_id == "bas"  # normaliza "Kosner Baskonia" -> equipo ya existente
    assert game.boxscore[0].player_id == "howard"  # matchea por team_id+dorsal


def test_acb_transform_and_load_produces_coherent_rows(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT home_score, away_score FROM games WHERE id = 'acb-2025123401'")).first()
        assert row == (90, 85)
        pts = conn.execute(
            text("SELECT pts FROM player_game_stats WHERE game_id='acb-2025123401' AND player_id='howard'")
        ).scalar_one()
        assert pts == 22
        # net_rating derivado de ortg-drtg (ambos presentes en el fixture)
        net_rating = conn.execute(
            text("SELECT net_rating FROM game_advanced_stats WHERE game_id='acb-2025123401' AND team_id='bas'")
        ).scalar_one()
        assert net_rating == 5.0


def test_acb_transform_and_load_is_idempotent(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM games WHERE id='acb-2025123401'")).scalar_one() == 1
        assert conn.execute(
            text("SELECT COUNT(*) FROM player_game_stats WHERE game_id='acb-2025123401'")
        ).scalar_one() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-2025123401'")).scalar_one() == 2
        # el rival ("Unicaja") no se duplica al re-ejecutar
        assert conn.execute(text("SELECT COUNT(*) FROM teams WHERE name='Unicaja'")).scalar_one() == 1


RAW_GAME_WITH_PBP = {
    "game_id": "2025999999",
    "date": "2026-02-20",
    "season": 2025,
    "competition": "ACB",
    "home_team": {"id": "acb-bas", "name": "Kosner Baskonia"},
    "away_team": {"id": "acb-uni", "name": "Unicaja"},
    "home_score": 4,
    "away_score": 0,
    "pace": 70.0,
    "players": [
        {"player_id": f"acb-h{i}", "team_id": "acb-bas", "name": f"Local {i}", "number": 60 + i,
         "minutes": 40.0, "pts": 0, "reb": 0, "ast": 0, "efg_pct": 0.0, "starter": True}
        for i in range(1, 6)
    ] + [
        {"player_id": f"acb-a{i}", "team_id": "acb-uni", "name": f"Visitante {i}", "number": 70 + i,
         "minutes": 40.0, "pts": 0, "reb": 0, "ast": 0, "efg_pct": 0.0, "starter": True}
        for i in range(1, 6)
    ],
    "shots": [],
    "play_by_play": [
        {"team_id": "acb-bas", "type": "score", "quarter": "Q1", "clock": "08:00", "points": 4},
    ],
}


def test_acb_parse_and_resolve_reconstructs_lineups_from_play_by_play(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME_WITH_PBP)

    assert len(game.lineups) == 2  # un quinteto por equipo, sin sustituciones
    home_lineup = next(l for l in game.lineups if len(l.player_ids) == 5 and l.plus_minus == 4)
    away_lineup = next(l for l in game.lineups if l.plus_minus == -4)
    assert len(home_lineup.player_ids) == 5
    assert len(away_lineup.player_ids) == 5


def test_acb_pipeline_run_uses_client_and_reports_summary(engine):
    fake_client = mock.Mock()
    fake_client.fetch_season_game_ids.return_value = ["2025123401"]
    fake_client.fetch_game.return_value = RAW_GAME

    summary = run(engine, season=2025, client=fake_client)

    assert summary == {"loaded": ["2025123401"], "failed": []}
    fake_client.fetch_season_game_ids.assert_called_once_with(2025)


def test_acb_pipeline_run_isolates_failures():
    fake_client = mock.Mock()
    fake_client.fetch_season_game_ids.return_value = ["good", "bad"]

    def fetch_game(game_id):
        if game_id == "bad":
            raise RuntimeError("boom")
        return RAW_GAME

    fake_client.fetch_game.side_effect = fetch_game

    from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

    engine = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(engine)
    try:
        summary = run(engine, season=2025, client=fake_client)
    finally:
        engine.dispose()

    assert summary["failed"] == ["bad"]
    assert summary["loaded"] == ["good"]


# --- Refresco por-partido y discovery (feature 014) -------------------------


def _fake_acb_client(match_ids=("2025123401",)):
    """Cliente ACB simulado: calendario en memoria + `fetch_game` fijo."""
    client = mock.Mock()
    client.fetch_season_finished_matches.return_value = [{"id": mid} for mid in match_ids]
    client.fetch_game.return_value = RAW_GAME
    return client


def test_acb_run_single_game_carga_solo_el_partido_pedido(engine):
    """Descarga un único partido tras listar el calendario (requisito de `fetch_game`)."""
    client = _fake_acb_client(("2025123401", "2025123402"))

    summary = run_single_game(engine, season=2025, game_id="2025123401", client=client)

    assert summary == {"loaded": ["2025123401"], "failed": []}
    client.fetch_season_finished_matches.assert_called_once_with(2025)
    client.fetch_game.assert_called_once_with("2025123401")

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM games WHERE id='acb-2025123401'")
        ).scalar_one() == 1


def test_acb_run_single_game_es_idempotente(engine):
    """Refrescar dos veces el mismo partido no duplica filas."""
    client = _fake_acb_client()
    run_single_game(engine, season=2025, game_id="2025123401", client=client)
    summary = run_single_game(engine, season=2025, game_id="2025123401", client=client)

    assert summary["loaded"] == ["2025123401"]
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM player_game_stats WHERE game_id='acb-2025123401'")
        ).scalar_one() == 2


def test_acb_run_single_game_partido_desconocido_queda_en_failed(engine):
    """Un id que no está en el calendario de la temporada no lanza: va a `failed`."""
    client = _fake_acb_client(("2025123401",))

    summary = run_single_game(engine, season=2025, game_id="no-existe", client=client)

    assert summary == {"loaded": [], "failed": ["no-existe"]}
    client.fetch_game.assert_not_called()


def test_acb_run_single_game_captura_el_fallo_de_la_fuente(engine):
    """Si la fuente falla (500, red), el partido queda en `failed` sin propagar."""
    client = _fake_acb_client()
    client.fetch_game.side_effect = RuntimeError("500 del servidor")

    summary = run_single_game(engine, season=2025, game_id="2025123401", client=client)

    assert summary == {"loaded": [], "failed": ["2025123401"]}


def test_acb_discover_missing_games_compara_calendario_con_bd(engine):
    """Devuelve los finalizados que faltan en `games`, sin descargar ningún partido."""
    client = _fake_acb_client(("2025123401", "2025123402"))
    run_single_game(engine, season=2025, game_id="2025123401", client=client)
    client.fetch_game.reset_mock()

    missing = discover_missing_games(engine, season=2025, client=client)

    assert missing == ["2025123402"]
    client.fetch_game.assert_not_called()  # discovery no descarga partidos
