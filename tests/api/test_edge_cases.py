"""Tests de casos límite y huecos de cobertura de la feature 012.

Complementa las suites existentes (`test_teams`, `test_games`, `test_players`,
`test_matchups`, `test_meta`, `test_errors`) con los casos límite que el
desarrollo inicial no cubría:

- Filtro `season_label` en `/games`, `/roster` y `/summary`.
- Identidad TEXT (`team_id`/`game_id`) con ids de estilo real (`'acb-105370'`).
- Boxscore con filas de **ambos** equipos (el esquema no filtra por equipo).
- Proyección (`/projection`) con datos ORtg/DRtg presentes (no solo el caso
  degradado a `null`).
- Narrativa degradada a `null` para un equipo sin partidos.
- Calendario vacío para un rival sin partidos en el seed.
- Mapeo `InvalidFilter` → 400 `problem+json` (RFC 9457).
- Dificultad de calendario con `next_n` por defecto.

Ninguno de estos tests modifica código de producción: solo siembran datos
adicionales en la BD de scouting en memoria del fixture `engine` cuando hace
falta (boxscore de ambos equipos, proyección con datos).
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from apps.api.errors import register_exception_handlers
from packages.baskonia_core.errors import InvalidFilter


# ---------------------------------------------------------------------------
# Filtro season_label
# ---------------------------------------------------------------------------

def test_games_filtered_by_season_label(client):
    """`season_label` filtra los partidos de un equipo por temporada."""
    r = client.get("/api/v1/teams/bas/games", params={"season_label": "2025-2026"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5


def test_games_filtered_by_unknown_season_label(client):
    """`season_label` inexistente → lista vacía (no error)."""
    r = client.get("/api/v1/teams/bas/games", params={"season_label": "2099-2100"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_roster_filtered_by_season_label(client):
    """`season_label` filtra la plantilla por temporada."""
    r = client.get("/api/v1/teams/bas/roster", params={"season_label": "2025-2026"})
    assert r.status_code == 200
    body = r.json()
    assert body["season_label"] == "2025-2026"
    assert len(body["players"]) == 8


def test_summary_filtered_by_season_label(client):
    """`season_label` filtra el resumen (medias avanzadas) por temporada."""
    r = client.get("/api/v1/teams/bas/summary", params={"season_label": "2025-2026"})
    assert r.status_code == 200
    body = r.json()
    assert body["filters"]["season_label"] == "2025-2026"
    assert body["games_played"] == 5


# ---------------------------------------------------------------------------
# Identidad TEXT (team_id / game_id)
# ---------------------------------------------------------------------------

def test_game_id_text_style_acb_not_found(client):
    """Un `game_id` TEXT de estilo real inexistente → 404 game-not-found.

    El esquema usa ids como `'acb-105370'`; un id de ese estilo que no existe
    en la BD debe devolver 404 problem+json, no un 500.
    """
    r = client.get("/api/v1/games/acb-105370")
    assert r.status_code == 404
    body = r.json()
    assert body["type"].endswith("game-not-found")
    assert body["status"] == 404


def test_team_id_text_style_non_own_team(client):
    """Un `team_id` TEXT de un rival (no propio) se resuelve correctamente."""
    r = client.get("/api/v1/teams/rm")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "rm"
    assert body["name"] == "Real Madrid"
    assert body["is_own_team"] is False


# ---------------------------------------------------------------------------
# Boxscore con ambos equipos
# ---------------------------------------------------------------------------

def test_boxscore_includes_both_teams(engine, client):
    """El boxscore devuelve filas de ambos equipos (no filtra por equipo).

    El seed solo tiene jugadores del Baskonia; se siembra un jugador del Real
    Madrid con una fila de `player_game_stats` en `g1` para verificar que el
    boxscore lo incluye junto a los del Baskonia.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position) "
                "VALUES ('campazzo', 'rm', 'Facundo Campazzo', 7, 'Base')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO player_game_stats "
                "(game_id, player_id, minutes, pts, reb, ast, efg_pct) "
                "VALUES ('g1', 'campazzo', 28.0, 15, 2, 8, 52.0)"
            )
        )

    r = client.get("/api/v1/games/g1/boxscore")
    assert r.status_code == 200
    body = r.json()
    assert body["game_id"] == "g1"
    # 8 del Baskonia + 1 del Real Madrid.
    assert len(body["rows"]) == 9
    team_ids = {row["player_id"] for row in body["rows"]}
    assert "campazzo" in team_ids
    assert "howard" in team_ids
    # Orden por puntos desc: howard (20) primero, campazzo (15) después.
    assert body["rows"][0]["player_id"] == "howard"
    assert body["rows"][1]["player_id"] == "campazzo"
    # Cada fila expone el team_id del jugador (para agrupar por equipo).
    assert all("team_id" in row for row in body["rows"])
    assert {row["team_id"] for row in body["rows"]} == {"bas", "rm"}


# ---------------------------------------------------------------------------
# Proyección con datos ORtg/DRtg presentes
# ---------------------------------------------------------------------------

def test_projection_computes_when_ortg_drtg_present(engine, client):
    """La proyección se calcula cuando ambos equipos tienen ORtg/DRtg.

    El seed de `game_advanced_stats` no tiene ortg/drtg (solo net_rating/efg/...),
    así que la proyección se degrada a `null`. Se siembran ortg/drtg para ambos
    equipos y se verifica que la proyección se calcula.
    """
    with engine.begin() as conn:
        # Añade ortg/drtg a las filas avanzadas existentes de 'bas' y crea
        # filas para 'rm' en los mismos partidos.
        for game_id in ("g1", "g2", "g3", "g4", "g5"):
            conn.execute(
                text(
                    "UPDATE game_advanced_stats SET ortg = 115.0, drtg = 107.0 "
                    "WHERE game_id = :gid AND team_id = 'bas'"
                ),
                {"gid": game_id},
            )
            conn.execute(
                text(
                    "INSERT INTO game_advanced_stats "
                    "(game_id, team_id, ortg, drtg, net_rating, efg_pct, "
                    "ts_pct, tov_pct, orb_pct) "
                    "VALUES (:gid, 'rm', 110.0, 112.0, -2.0, 50.0, 55.0, 12.0, 20.0)"
                ),
                {"gid": game_id},
            )

    r = client.get("/api/v1/teams/bas/matchups/rm/projection")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["id"] == "bas"
    assert body["opponent"]["id"] == "rm"
    assert body["projection"] is not None
    proj = body["projection"]
    assert proj["predicted_net_rating"] is not None
    assert proj["predicted_ortg"] is not None
    assert proj["expected_margin"] == proj["predicted_net_rating"]


# ---------------------------------------------------------------------------
# Narrativa degradada y calendario vacío
# ---------------------------------------------------------------------------

def test_narrative_null_for_team_without_games(client):
    """Narrativa degradada a `null` para un equipo sin partidos en el seed.

    `bay` (Bayern Múnich) no aparece en ningún partido del seed, así que la
    narrativa debe degradarse a `null` (no inventa contenido).
    """
    r = client.get("/api/v1/teams/bay/narrative")
    assert r.status_code == 200
    body = r.json()
    assert body["team_id"] == "bay"
    assert body["narrative"] is None


def test_games_empty_for_rival_without_games(client):
    """Calendario vacío para un rival sin partidos en el seed (no error).

    `bay` no aparece en ningún partido del seed → lista vacía, no error.
    """
    r = client.get("/api/v1/teams/bay/games")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Dificultad de calendario con next_n por defecto
# ---------------------------------------------------------------------------

def test_schedule_difficulty_default_next_n(client):
    """`next_n` por defecto (5) considera los 5 próximos rivales del seed."""
    r = client.get("/api/v1/teams/bas/schedule-difficulty")
    assert r.status_code == 200
    body = r.json()
    assert body["games_considered"] == 5
    assert len(body["opponents"]) == 5


# ---------------------------------------------------------------------------
# InvalidFilter → 400 problem+json
# ---------------------------------------------------------------------------

def test_invalid_filter_is_problem_json():
    """`InvalidFilter` se traduce a 400 `application/problem+json`.

    Ningún router de producción lanza `InvalidFilter` hoy (el mapa de errores
    lo contempla), así que se valida el contrato de errores con una app de
    prueba que reutiliza `register_exception_handlers` y una ruta que lo lanza.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def _boom():
        raise InvalidFilter("season_label no aplicable")

    with TestClient(app) as c:
        r = c.get("/boom")
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 400
    assert body["type"].endswith("invalid-filter")
    assert body["title"]
    assert "detail" in body
    assert "instance" in body
    assert "request_id" in body
