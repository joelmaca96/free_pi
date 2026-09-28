"""Tests de la fuente Euroliga: adapter (DataFrame -> contrato común) y carga.

Columnas verificadas en vivo (ver `adapter.py`); no requieren `euroleague_api`
instalado: los DataFrames de entrada se construyen a mano con pandas (ya es
dependencia del proyecto), con las mismas columnas que devuelve la librería
real.
"""
from unittest import mock

import pandas as pd
import pytest
from sqlalchemy import text

from ingest.common.loader import load_game
from ingest.euroleague.adapter import _format_player_name, build_raw_game, build_scheduled_matchup
from ingest.euroleague.parser import parse_and_resolve
import ingest.euroleague.pipeline as pipeline_module
from ingest.euroleague.pipeline import discover_missing_games, run, run_single_game, run_upcoming


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    """`pipeline.run` duerme entre partidos para no tumbar el rate-limit real; en tests, no."""
    monkeypatch.setattr(pipeline_module, "REQUEST_DELAY", 0.0)

METADATA = {
    "Gamecode": 305, "Season": 2025, "Date": "20/01/2026",
    "TeamA": "KOSNER BASKONIA", "CodeTeamA": "BAS",
    "TeamB": "REAL MADRID", "CodeTeamB": "MAD",
    "ScoreA": 91, "ScoreB": 88,
}

BOXSCORE_DF = pd.DataFrame(
    [
        {"Player_ID": "EL-HOWARD", "Player": "HOWARD, Marcus", "Team": "BAS", "Dorsal": 0, "IsStarter": 1.0,
         "Minutes": "31:00", "Points": 24, "TotalRebounds": 4, "Assistances": 5, "Turnovers": 2,
         "OffensiveRebounds": 1, "DefensiveRebounds": 3, "FieldGoalsMade2": 5, "FieldGoalsAttempted2": 8,
         "FieldGoalsMade3": 3, "FieldGoalsAttempted3": 7, "FreeThrowsMade": 3, "FreeThrowsAttempted": 3},
        {"Player_ID": "EL-RIVAL1", "Player": "RIVAL, Jugador", "Team": "MAD", "Dorsal": 9, "IsStarter": 1.0,
         "Minutes": "29:00", "Points": 18, "TotalRebounds": 6, "Assistances": 3, "Turnovers": 1,
         "OffensiveRebounds": 2, "DefensiveRebounds": 4, "FieldGoalsMade2": 6, "FieldGoalsAttempted2": 10,
         "FieldGoalsMade3": 2, "FieldGoalsAttempted3": 5, "FreeThrowsMade": 2, "FreeThrowsAttempted": 2},
    ]
)

SHOTS_DF = pd.DataFrame(
    [
        {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 0.0, "COORD_Y": 100.0, "ID_ACTION": "2FGM"},
        {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 50.0, "COORD_Y": 150.0, "ID_ACTION": "2FGA"},
        {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": -1, "COORD_Y": -1, "ID_ACTION": "FTM"},  # sin ubicación
    ]
)


def test_build_raw_game_matches_common_contract():
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), SHOTS_DF.to_dict("records"))

    assert raw["game_id"] == "305"
    assert raw["date"] == "2026-01-20"
    assert raw["home_team"]["id"] == "BAS"
    assert raw["home_team"]["name"] == "Kosner Baskonia"
    assert raw["home_score"] == 91
    assert raw["away_score"] == 88
    assert len(raw["players"]) == 2
    assert raw["players"][0]["minutes"] == 31.0  # "31:00" -> 31.0
    assert len(raw["team_stats"]) == 2
    assert len(raw["shots"]) == 2  # el tiro con COORD (-1,-1) se descarta
    assert raw["shots"][0]["made"] is True
    assert raw["shots"][1]["made"] is False


# --- Coordenadas de tiro ---------------------------------------------------


def test_shot_coords_put_shots_near_the_hoop_at_high_y():
    """`COORD_Y` es la distancia AL ARO, y `court_zones` usa "y alto = cerca del aro".

    Regresión: se reescalaba `COORD_Y` como si fuera una coordenada de campo
    normalizable de extremo a extremo, sin invertirla, y el mapa de tiros de
    Euroliga salía del revés — las bandejas caían en la zona "Triple exterior"
    (con un 62% de acierto, imposible para triples) y los triples junto al aro.
    """
    hoop = {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 0.0, "COORD_Y": 0.0, "ID_ACTION": "2FGM"}
    three = {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 0.0, "COORD_Y": 700.0, "ID_ACTION": "3FGM"}
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), [hoop, three])

    at_hoop, from_three = raw["shots"]
    assert at_hoop["y"] > from_three["y"]
    # Y en las coordenadas concretas del seed de `court_zones` (`schema.sql`):
    # el tiro bajo el aro cae en 'Pintura' (x 195-305, y 300-455)...
    assert 195 <= at_hoop["x"] <= 305 and 300 <= at_hoop["y"] <= 455
    # ...y el triple frontal, más allá del vértice del arco ('Triple exterior'.y_max).
    assert from_three["y"] < 170


def test_shot_coords_keep_left_and_right_apart():
    """`COORD_X` es el desplazamiento lateral: negativo a la izquierda del centro."""
    left = {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": -680.0, "COORD_Y": 50.0, "ID_ACTION": "3FGM"}
    right = {"ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "COORD_X": 680.0, "COORD_Y": 50.0, "ID_ACTION": "3FGM"}
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), [left, right])

    from_left, from_right = raw["shots"]
    # Esquinas: dentro del dominio 0-500 del gráfico y en su zona respectiva
    # ('Triple esquina izq.' x 15-55, 'Triple esquina der.' x 445-485).
    assert 15 <= from_left["x"] <= 55
    assert 445 <= from_right["x"] <= 485


# --- Nombres de jugador "APELLIDO, Nombre" -> "Nombre Apellido" -------------


def test_format_player_name_swaps_apellido_nombre_order():
    assert _format_player_name("HOWARD, Marcus") == "Marcus Howard"


def test_format_player_name_uppercases_both_parts_in_real_data():
    """Verificado en vivo (2026-08-24): el boxscore real trae AMBAS partes en
    mayúsculas, no solo el apellido (`"VILDOZA, LUCA"`, no `"VILDOZA, Luca"`)."""
    assert _format_player_name("VILDOZA, LUCA") == "Luca Vildoza"


def test_format_player_name_keeps_compound_surname_with_suffix_together():
    """Se parte por la PRIMERA coma, no por espacios — un apellido compuesto
    como "ALSTON JR." no debe perder el "JR." al reordenar."""
    assert _format_player_name("ALSTON JR., DERRICK") == "Derrick Alston Jr."


def test_format_player_name_leaves_value_without_comma_unchanged():
    assert _format_player_name("Sin Coma") == "Sin Coma"


def test_build_raw_game_formats_player_names():
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), SHOTS_DF.to_dict("records"))

    names = [p["name"] for p in raw["players"]]
    assert names == ["Marcus Howard", "Jugador Rival"]


def test_euroleague_transform_and_load_reuses_baskonia_and_is_idempotent(engine):
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), SHOTS_DF.to_dict("records"))

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw)
        assert game.home_team_id == "bas"  # "Kosner Baskonia" normaliza al equipo del seed
        load_game(conn, game)
    with engine.begin() as conn:  # segunda ejecución: no debe duplicar
        game = parse_and_resolve(conn, raw)
        load_game(conn, game)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM games WHERE id='euroleague-305'")).scalar_one() == 1
        pts = conn.execute(
            text("SELECT pts FROM player_game_stats WHERE game_id='euroleague-305' AND player_id='howard'")
        ).scalar_one()
        assert pts == 24
        assert conn.execute(text("SELECT COUNT(*) FROM teams WHERE name='Real Madrid'")).scalar_one() == 1


def test_euroleague_pipeline_run_reports_summary(engine):
    fake_client = mock.Mock()
    fake_client.fetch_season_game_codes.return_value = pd.DataFrame([{"game": 305, "gamecode": "E2025_305", "played": "true"}])
    fake_client.fetch_game_metadata.return_value = METADATA
    fake_client.fetch_game_boxscore.return_value = BOXSCORE_DF
    fake_client.fetch_game_shot_data.return_value = SHOTS_DF
    fake_client.fetch_game_play_by_play.return_value = pd.DataFrame([])

    summary = run(engine, season=2025, client=fake_client)

    assert summary == {"loaded": ["305"], "failed": []}


def test_euroleague_pipeline_run_skips_unplayed_games(engine):
    fake_client = mock.Mock()
    fake_client.fetch_season_game_codes.return_value = pd.DataFrame(
        [{"game": 305, "gamecode": "E2025_305", "played": "true"}, {"game": 306, "gamecode": "E2025_306", "played": "false"}]
    )
    fake_client.fetch_game_metadata.return_value = METADATA
    fake_client.fetch_game_boxscore.return_value = BOXSCORE_DF
    fake_client.fetch_game_shot_data.return_value = SHOTS_DF
    fake_client.fetch_game_play_by_play.return_value = pd.DataFrame([])

    summary = run(engine, season=2025, client=fake_client)

    assert summary == {"loaded": ["305"], "failed": []}
    fake_client.fetch_game_metadata.assert_called_once_with(2025, 305)


PBP_DF = pd.DataFrame(
    [
        {"CODETEAM": "BAS", "PLAYER_ID": "EL-HOWARD", "PLAYTYPE": "2FGM", "PERIOD": 1, "MARKERTIME": "08:00"},
        {"CODETEAM": "MAD", "PLAYER_ID": "EL-RIVAL1", "PLAYTYPE": "3FGM", "PERIOD": 1, "MARKERTIME": "05:00"},
        {"CODETEAM": None, "PLAYER_ID": None, "PLAYTYPE": "BP", "PERIOD": 1, "MARKERTIME": "10:00"},  # begin period
    ]
)
BOXSCORE_WITH_STARTERS_DF = BOXSCORE_DF.copy()


def test_build_raw_game_with_play_by_play_feeds_lineup_reconstruction(engine):
    raw = build_raw_game(
        METADATA, BOXSCORE_WITH_STARTERS_DF.to_dict("records"), SHOTS_DF.to_dict("records"),
        PBP_DF.to_dict("records"),
    )
    assert raw["lineups"] == []  # el adapter no calcula lineups, solo pasa play_by_play
    assert len(raw["play_by_play"]) == 2
    assert raw["play_by_play"][0]["type"] == "score"

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw)

    # Solo 2 jugadores en el boxscore de prueba -> un único quinteto de 2 no
    # llega a 5, así que no se reconstruye ningún lineup completo (dato
    # insuficiente, no se inventa uno).
    assert game.lineups == []


# --- Refresco por-partido y discovery (feature 014) -------------------------


def _fake_euroleague_client(schedule_rows=None):
    """Cliente de Euroliga simulado (los 4 endpoints + calendario)."""
    client = mock.Mock()
    client.fetch_season_game_codes.return_value = pd.DataFrame(
        schedule_rows or [{"game": 305, "gamecode": "E2025_305", "played": "true"}]
    )
    client.fetch_game_metadata.return_value = METADATA
    client.fetch_game_boxscore.return_value = BOXSCORE_DF
    client.fetch_game_shot_data.return_value = SHOTS_DF
    client.fetch_game_play_by_play.return_value = pd.DataFrame([])
    return client


def test_euroleague_run_single_game_no_lista_el_calendario(engine):
    """Los 4 endpoints aceptan `(season, game_code)`: no hace falta el calendario."""
    client = _fake_euroleague_client()

    summary = run_single_game(engine, season=2025, game_code=305, client=client)

    assert summary == {"loaded": ["305"], "failed": []}
    client.fetch_season_game_codes.assert_not_called()
    client.fetch_game_metadata.assert_called_once_with(2025, 305)

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM games WHERE id='euroleague-305'")
        ).scalar_one() == 1


def test_euroleague_run_single_game_captura_el_fallo_de_la_fuente(engine):
    """Un 429/500 de la fuente deja el partido en `failed` sin propagar."""
    client = _fake_euroleague_client()
    client.fetch_game_boxscore.side_effect = RuntimeError("429 rate limit")

    summary = run_single_game(engine, season=2025, game_code=305, client=client)

    assert summary == {"loaded": [], "failed": ["305"]}


def test_euroleague_discover_missing_games_solo_jugados_y_ausentes(engine):
    """Compara el calendario con `games`: solo jugados, sin descargar partidos."""
    client = _fake_euroleague_client(
        [
            {"game": 305, "gamecode": "E2025_305", "played": "true"},
            {"game": 306, "gamecode": "E2025_306", "played": "true"},
            {"game": 307, "gamecode": "E2025_307", "played": "false"},
        ]
    )
    run_single_game(engine, season=2025, game_code=305, client=client)
    client.fetch_game_metadata.reset_mock()

    missing = discover_missing_games(engine, season=2025, client=client)

    assert missing == [306]  # 305 ya cargado, 307 no jugado
    client.fetch_game_metadata.assert_not_called()  # discovery no descarga partidos


# --- Calendario futuro (upcoming_matchups) + escudos ------------------------

_CLUBS_BY_CODE = {
    "BAS": {"code": "BAS", "name": "Kosner Baskonia Vitoria-Gasteiz", "images": {"crest": "https://cdn/bas.png"}},
    "MAD": {"code": "MAD", "name": "Real Madrid", "images": {"crest": "https://cdn/mad.png"}},
    "PAM": {"code": "PAM", "name": "Valencia Basket", "images": {}},  # sin escudo (hueco real posible)
}

_SCHEDULE_ROWS = [
    # Baskonia (BAS) de local ante Real Madrid (MAD).
    {"homecode": "BAS", "awaycode": "MAD", "hometeam": "KOSNER BASKONIA VITORIA-GASTEIZ", "awayteam": "REAL MADRID",
     "date": "Sep 24, 2026", "played": "false"},
    # Baskonia de visitante ante Valencia (PAM, sin escudo en el catálogo).
    {"homecode": "PAM", "awaycode": "BAS", "hometeam": "VALENCIA BASKET", "awayteam": "KOSNER BASKONIA VITORIA-GASTEIZ",
     "date": "Oct 01, 2026", "played": "false"},
    # Partido entre otros dos equipos: no debe entrar en upcoming_matchups del Baskonia.
    {"homecode": "MAD", "awaycode": "PAM", "hometeam": "REAL MADRID", "awayteam": "VALENCIA BASKET",
     "date": "Sep 25, 2026", "played": "false"},
    # Ya jugado: `run_upcoming` lo descarta antes de llamar a `build_scheduled_matchup`.
    {"homecode": "BAS", "awaycode": "MAD", "hometeam": "KOSNER BASKONIA VITORIA-GASTEIZ", "awayteam": "REAL MADRID",
     "date": "Jan 20, 2026", "played": "true"},
]


def test_build_scheduled_matchup_filtra_partidos_ajenos_al_baskonia():
    ajeno = build_scheduled_matchup(_SCHEDULE_ROWS[2], _CLUBS_BY_CODE, own_code="BAS")
    assert ajeno is None


def test_build_scheduled_matchup_resuelve_rival_nombre_y_escudo_desde_clubs():
    visitante = build_scheduled_matchup(_SCHEDULE_ROWS[0], _CLUBS_BY_CODE, own_code="BAS")
    assert visitante == {
        "opponent_code": "MAD", "opponent_name": "Real Madrid", "opponent_logo_url": "https://cdn/mad.png",
        "match_date": "2026-09-24", "is_home": True,
    }


def test_build_scheduled_matchup_sin_escudo_en_el_catalogo_no_falla():
    """`images` puede venir vacío (hueco real de la fuente) - `opponent_logo_url` queda
    `None`, no lanza `KeyError`."""
    local = build_scheduled_matchup(_SCHEDULE_ROWS[1], _CLUBS_BY_CODE, own_code="BAS")
    assert local["opponent_name"] == "Valencia Basket"
    assert local["opponent_logo_url"] is None
    assert local["is_home"] is False


def _fake_upcoming_client(schedule_rows=None, clubs=None):
    client = mock.Mock()
    client.fetch_season_game_codes.return_value = pd.DataFrame(
        schedule_rows if schedule_rows is not None else _SCHEDULE_ROWS
    )
    client.fetch_clubs.return_value = list((clubs or _CLUBS_BY_CODE).values())
    return client


def test_run_upcoming_carga_solo_los_partidos_no_jugados_del_baskonia(engine):
    client = _fake_upcoming_client()

    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 2  # el 3º (ajeno) y el 4º (ya jugado) se descartan
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT is_home, match_date FROM upcoming_matchups"
                " WHERE season_id = :season_id ORDER BY match_date"
            ),
            {"season_id": summary["season_id"]},
        ).all()
    assert rows == [(1, "2026-09-24"), (0, "2026-10-01")]


def test_run_upcoming_resuelve_el_equipo_propio_por_nombre_cuando_no_hay_enlace_previo(engine):
    """Sin ningún partido de Euroliga cargado antes (BD nueva, sin `team_external_ids`
    `source='euroleague'`), se resuelve por nombre normalizado + alias del sponsor
    real 2026-2027 (`_KNOWN_TEAM_ALIASES`), no por un código hardcodeado."""
    client = _fake_upcoming_client()

    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 2  # no 0 — si no resolviera al Baskonia, no encontraría ningún partido propio


def test_run_upcoming_prefiere_el_enlace_ya_existente_en_team_external_ids(engine):
    """Si ya hay un enlace `euroleague/BAS` -> `bas` (de una carga de partidos jugados
    anterior), se usa directamente — más barato y sin depender de coincidencia de
    nombre (que podría fallar si el sponsor vuelve a cambiar)."""
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO team_external_ids (team_id, source, external_id) VALUES ('bas', 'euroleague', 'BAS')")
        )
    client = _fake_upcoming_client()

    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 2


def test_run_upcoming_backfillea_el_escudo_del_baskonia_y_del_rival(engine):
    client = _fake_upcoming_client()

    run_upcoming(engine, season=2026, client=client)

    with engine.connect() as conn:
        bas_logo = conn.execute(text("SELECT logo_url FROM teams WHERE id = 'bas'")).scalar_one()
        mad_logo = conn.execute(
            text("SELECT t.logo_url FROM teams t JOIN team_external_ids tei ON tei.team_id = t.id"
                 " WHERE tei.source='euroleague' AND tei.external_id='MAD'")
        ).scalar_one()

    assert bas_logo == "https://cdn/bas.png"
    assert mad_logo == "https://cdn/mad.png"


def test_run_upcoming_es_idempotente_y_no_toca_el_calendario_de_acb(engine):
    """Reemplaza su propio calendario (no acumula) y no borra filas de otra
    competición para la misma temporada (ver fix del mismo día en
    `ingest/acb/pipeline.py::run_upcoming` — acotar solo por `season_id` borraba
    también el calendario de la otra fuente)."""
    from ingest.common.identity import get_competition_id, get_or_create_season

    with engine.begin() as conn:
        season_id = get_or_create_season(conn, 2026)
        acb_competition_id = get_competition_id(conn, "ACB")
        conn.execute(
            text(
                "INSERT INTO upcoming_matchups (opponent_team_id, competition_id, match_date, is_home, season_id)"
                " VALUES ('rm', :cid, '2026-09-20', 1, :sid)"
            ),
            {"cid": acb_competition_id, "sid": season_id},
        )

    client = _fake_upcoming_client()
    run_upcoming(engine, season=2026, client=client)
    summary = run_upcoming(engine, season=2026, client=client)  # segunda llamada: no debe duplicar

    with engine.connect() as conn:
        euroleague_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM upcoming_matchups um JOIN competitions c ON c.id = um.competition_id"
                " WHERE um.season_id = :sid AND c.name = 'Euroliga'"
            ),
            {"sid": summary["season_id"]},
        ).scalar_one()
        acb_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM upcoming_matchups um JOIN competitions c ON c.id = um.competition_id"
                " WHERE um.season_id = :sid AND c.name = 'ACB'"
            ),
            {"sid": summary["season_id"]},
        ).scalar_one()

    assert euroleague_count == 2  # no 4: la 2ª llamada reemplazó, no acumuló
    assert acb_count == 1  # sigue ahí: run_upcoming de Euroliga no la tocó


def test_run_upcoming_descarta_partidos_sin_fecha_valida(engine):
    rows = [{"homecode": "BAS", "awaycode": "MAD", "hometeam": "KOSNER BASKONIA VITORIA-GASTEIZ",
              "awayteam": "REAL MADRID", "date": None, "played": "false"}]
    client = _fake_upcoming_client(schedule_rows=rows)

    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 0


# ---- tiros libres en bruto (ftm/fta) ----
# Mismo hueco que en ACB: `_team_totals` los sumaba solo para `ft_rate` y el
# boxscore por jugador no los emitía. Ver `tests/ingest/test_acb_client.py`.


def test_build_raw_game_emits_raw_free_throws_for_team_and_player():
    raw = build_raw_game(METADATA, BOXSCORE_DF.to_dict("records"), SHOTS_DF.to_dict("records"))

    home_stats = next(t for t in raw["team_stats"] if t["team_id"] == "BAS")
    assert (home_stats["ftm"], home_stats["fta"]) == (3, 3)  # el único jugador de BAS en el fixture
    assert home_stats["ft_rate"] == round(100 * 3 / 15, 1)   # la tasa sigue igual

    howard = next(p for p in raw["players"] if p["player_id"] == "EL-HOWARD")
    assert (howard["ftm"], howard["fta"]) == (3, 3)


def test_missing_free_throws_stay_null_instead_of_becoming_zero():
    """`euroleague_api` devuelve DataFrames, así que un hueco llega como NaN.
    Convertirlo a 0 sería afirmar "no tiró ninguno" sobre un dato que no está —
    y `gp_ft` de las vistas existe justamente para distinguir los dos casos."""
    import numpy as np

    records = BOXSCORE_DF.to_dict("records")
    records[0] = {**records[0], "FreeThrowsMade": np.nan, "FreeThrowsAttempted": None}

    raw = build_raw_game(METADATA, records, SHOTS_DF.to_dict("records"))

    howard = next(p for p in raw["players"] if p["player_id"] == "EL-HOWARD")
    assert howard["ftm"] is None and howard["fta"] is None


# ---- tiros con reloj/contexto y tiempos muertos (2026-09-28) ----
# Columnas verificadas en vivo en 16 partidos de 2025 (ver docstring del
# adapter): `ShotData` sin `PERIOD` pero con `MINUTE`/`CONSOLE`/`NUM_ANOT`/
# `POINTS_A/B` y las banderas como TEXTO "0"/"1" (solo a 1 en anotados); el
# PBP con `TOUT` (equipo) y `TOUT_TV` (sin equipo) y NaN en `POINTS_A/B` fuera
# de las jugadas que anotan.

_CTX_PBP_DF = pd.DataFrame(
    [
        {"NUMBEROFPLAY": 10, "PERIOD": 1, "MARKERTIME": "09:50", "CODETEAM": "MAD", "PLAYER_ID": "EL-RIVAL1",
         "PLAYTYPE": "2FGA", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 11, "PERIOD": 1, "MARKERTIME": "09:48", "CODETEAM": "BAS", "PLAYER_ID": "EL-HOWARD",
         "PLAYTYPE": "D", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 12, "PERIOD": 1, "MARKERTIME": "09:44", "CODETEAM": "BAS", "PLAYER_ID": "EL-HOWARD",
         "PLAYTYPE": "2FGM", "POINTS_A": 2, "POINTS_B": 0},
        {"NUMBEROFPLAY": 13, "PERIOD": 1, "MARKERTIME": "09:05", "CODETEAM": "MAD", "PLAYER_ID": "EL-RIVAL1",
         "PLAYTYPE": "TO", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 14, "PERIOD": 1, "MARKERTIME": "09:05", "CODETEAM": "BAS", "PLAYER_ID": "EL-HOWARD",
         "PLAYTYPE": "ST", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 15, "PERIOD": 1, "MARKERTIME": "09:00", "CODETEAM": "BAS", "PLAYER_ID": "EL-HOWARD",
         "PLAYTYPE": "3FGA", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 16, "PERIOD": 1, "MARKERTIME": "08:59", "CODETEAM": "BAS", "PLAYER_ID": "",
         "PLAYTYPE": "TOUT", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 17, "PERIOD": 1, "MARKERTIME": "08:59", "CODETEAM": "", "PLAYER_ID": "",
         "PLAYTYPE": "TOUT_TV", "POINTS_A": None, "POINTS_B": None},
        {"NUMBEROFPLAY": 18, "PERIOD": 1, "MARKERTIME": "08:40", "CODETEAM": "MAD", "PLAYER_ID": "EL-RIVAL1",
         "PLAYTYPE": "FTM", "POINTS_A": 2, "POINTS_B": 1},
        {"NUMBEROFPLAY": 19, "PERIOD": 1, "MARKERTIME": "08:40", "CODETEAM": "MAD", "PLAYER_ID": "EL-RIVAL1",
         "PLAYTYPE": "FTA", "POINTS_A": None, "POINTS_B": None},
    ]
)

_CTX_SHOTS_DF = pd.DataFrame(
    [
        {"NUM_ANOT": 10, "ID_PLAYER": "EL-RIVAL1", "TEAM": "MAD", "ID_ACTION": "2FGA", "COORD_X": 100.0,
         "COORD_Y": 300.0, "MINUTE": 1, "CONSOLE": "09:50", "POINTS_A": 0, "POINTS_B": 0,
         "FASTBREAK": "0", "SECOND_CHANCE": "0", "POINTS_OFF_TURNOVER": "0"},
        # Anotado: la fuente dice "no contraataque" aunque sea a 4 s de un
        # rebote — su bandera manda sobre la derivación.
        {"NUM_ANOT": 12, "ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "ID_ACTION": "2FGM", "COORD_X": 0.0,
         "COORD_Y": 100.0, "MINUTE": 1, "CONSOLE": "09:44", "POINTS_A": 2, "POINTS_B": 0,
         "FASTBREAK": "0", "SECOND_CHANCE": "0", "POINTS_OFF_TURNOVER": "0"},
        # Fallado tras robo: la fuente lo trae a "0" (sus banderas son de
        # PUNTOS), así que se deriva. `CONSOLE` con el minuto de más real.
        {"NUM_ANOT": 15, "ID_PLAYER": "EL-HOWARD", "TEAM": "BAS", "ID_ACTION": "3FGA", "COORD_X": 600.0,
         "COORD_Y": 300.0, "MINUTE": 1, "CONSOLE": "10:00", "POINTS_A": 2, "POINTS_B": 0,
         "FASTBREAK": "0", "SECOND_CHANCE": "0", "POINTS_OFF_TURNOVER": "0"},
        {"NUM_ANOT": 18, "ID_PLAYER": "EL-RIVAL1", "TEAM": "MAD", "ID_ACTION": "FTM", "COORD_X": -1,
         "COORD_Y": -1, "MINUTE": 2, "CONSOLE": "08:40", "POINTS_A": 2, "POINTS_B": 1,
         "FASTBREAK": "0", "SECOND_CHANCE": "0", "POINTS_OFF_TURNOVER": "0"},
    ]
)


def _ctx_raw(with_pbp=True):
    return build_raw_game(
        METADATA, BOXSCORE_DF.to_dict("records"), _CTX_SHOTS_DF.to_dict("records"),
        _CTX_PBP_DF.to_dict("records") if with_pbp else None,
    )


def test_euroleague_play_events_include_shots_and_only_team_timeouts():
    raw = _ctx_raw()
    types = [event["event_type"] for event in raw["play_events"]]
    assert types == [
        "fg2_missed", "dreb", "fg2_made", "turnover", "steal", "fg3_missed", "timeout", "ft_made", "ft_missed",
    ]  # TOUT_TV (sin equipo) no entra
    timeout = raw["play_events"][6]
    assert timeout["team_id"] == "BAS" and timeout["player_id"] is None
    made = raw["play_events"][2]
    assert (made["home_score"], made["away_score"]) == (2, 0)


def test_euroleague_shots_take_clock_from_pbp_and_keep_source_flags_only_when_made():
    raw = _ctx_raw()
    miss_rival, made, miss_three = raw["shots"]  # el libre (-1,-1) sigue fuera
    assert (made["quarter"], made["clock"], made["home_score"], made["away_score"]) == ("Q1", "09:44", 2, 0)
    assert made["is_fastbreak"] is False  # bandera de la fuente, tal cual
    assert miss_three["clock"] == "09:00"  # del PBP, no del `CONSOLE` erróneo
    assert miss_three["is_fastbreak"] is None  # fallado: se deja para derivar
    assert miss_rival["team_id"] == "MAD"


def test_euroleague_missed_shot_context_is_derived_from_pbp(engine):
    raw = _ctx_raw()
    with engine.begin() as conn:
        load_game(conn, parse_and_resolve(conn, raw))
    with engine.connect() as conn:
        rows = dict(
            (row[0], tuple(row[1:])) for row in conn.execute(text(
                "SELECT game_clock, seconds, is_fastbreak, is_second_chance, is_off_turnover"
                " FROM shots WHERE game_id = 'euroleague-305'"
            ))
        )
    assert rows["09:44"] == (16.0, 0, 0, 0)  # anotado: bandera de la fuente (no contraataque)
    assert rows["09:00"] == (60.0, 1, 0, 1)  # fallado 5 s tras robo: derivado
    assert rows["09:50"] == (10.0, 0, 0, 0)


def test_euroleague_shot_clock_without_pbp_fixes_console_with_minute():
    """Sin PBP, `CONSOLE` "10:00" con `MINUTE`=1 es en realidad 09:00 (verificado en vivo)."""
    raw = _ctx_raw(with_pbp=False)
    clocks = [(shot["quarter"], shot["clock"]) for shot in raw["shots"]]
    assert clocks == [("Q1", "09:50"), ("Q1", "09:44"), ("Q1", "09:00")]
    assert raw["play_events"] == []
