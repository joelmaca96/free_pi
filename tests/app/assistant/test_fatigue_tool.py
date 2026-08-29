"""Tests de la herramienta `fatigue_profile` (§12.1, propuesta 04).

Necesita su propio `today` por test —a diferencia del resto de `test_tools.py`,
que usa el `ctx`/`catalog` fijos del `conftest`—: "cómo llega el equipo AHORA
MISMO" solo se puede afirmar con números exactos si se controla qué día es
"ahora". El seed de `schema.sql` (g1..g5, del Baskonia, 29-dic-2025 a
18-ene-2026) ya tiene la mezcla de competiciones y huecos de descanso que
hace falta — ver `tests/app/test_fatigue.py` para las mismas cifras probadas
directamente sobre `queries.py`.
"""
import datetime as dt

from app.assistant.capabilities import probe
from app.assistant.tools import ToolCatalog
from app.assistant.tools.base import ToolContext


def _catalog_at(engine, today: dt.date, season_id: int = 1) -> ToolCatalog:
    ctx = ToolContext(engine=engine, season_id=season_id, own_team_id="bas", today=today, capabilities=probe(engine))
    return ToolCatalog(ctx)


def test_fatigue_profile_computes_days_since_the_last_game(engine):
    catalog = _catalog_at(engine, dt.date(2026, 1, 25))  # 7 días después de g5 (18-ene)
    result = catalog.execute("1", "fatigue_profile", {"team_id": "bas"}).result
    assert result["data"]["days_since_last_game"] == 7
    assert result["data"]["last_game"]["competition"] == "ACB"


def test_fatigue_profile_counts_recent_games_in_7_and_14_days(engine):
    # A fecha de 20-ene: g4 (15-ene, hace 5) y g5 (18-ene, hace 2) caen en la
    # ventana de 7 días; g3 (10-ene, hace 10) solo entra en la de 14.
    catalog = _catalog_at(engine, dt.date(2026, 1, 20))
    result = catalog.execute("1", "fatigue_profile", {"team_id": "bas"}).result
    assert result["data"]["games_last_7_days"] == 2
    assert result["data"]["games_last_14_days"] == 3


def test_fatigue_profile_top_recent_load_flags_players_above_their_average(engine):
    catalog = _catalog_at(engine, dt.date(2026, 1, 18))  # la fecha de g5
    result = catalog.execute("1", "fatigue_profile", {"team_id": "bas"}).result
    top = result["data"]["top_recent_load"]
    assert top  # hay jugadores con carga en la ventana
    assert all("above_season_average" in row and "rolling_minutes" in row for row in top)


def test_fatigue_profile_short_vs_long_rest_matches_the_bucket_math(engine):
    # g2 (7 días), g3 (5) y g4 (5) descanso -> tramo '≥5'; g5 (3) -> '3-4'.
    # Los dos tramos "largos" (agrupados en long_rest) suman los 4; el grupo
    # short_rest (≤1, 2) se queda sin ningún partido.
    catalog = _catalog_at(engine, dt.date(2026, 1, 20))
    result = catalog.execute("1", "fatigue_profile", {"team_id": "bas"}).result
    short_vs_long = result["data"]["short_vs_long_rest"]
    assert short_vs_long["short_rest"] == {"gp": 0, "net_rating": None}
    assert short_vs_long["long_rest"]["gp"] == 4
    assert short_vs_long["long_rest"]["net_rating"] is not None


def test_fatigue_profile_fails_usefully_without_games(engine):
    # `bas` SIEMPRE tiene partidos en la temporada 1 (el seed), así que
    # cualquier `season_id` que se le pida cae ahí por `_team_season` — hace
    # falta un equipo sin NINGÚN partido en NINGUNA temporada para llegar de
    # verdad al "sin datos" (mismo caso que prueba `test_tools.py` para
    # `team_profile`).
    catalog = _catalog_at(engine, dt.date(2026, 1, 20))
    result = catalog.execute("1", "fatigue_profile", {"team_id": "no-existe"}).result
    assert result["error"] == "sin datos"
    assert "suggestion" in result


def test_fatigue_profile_fails_usefully_when_only_future_games_are_loaded(engine):
    """Calendario cargado (`upcoming_matchups`) pero CERO partidos jugados
    todavía en `games` — caso real de pretemporada (ver
    `queries.team_scouting_season`, motivado por el mismo escenario)."""
    from sqlalchemy import text

    # Equipo NUEVO (no el `bas` del seed, que ya tiene 5 partidos pasados):
    # solo un partido, fechado en el futuro respecto al `today` del test.
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO teams (id, name, is_own_team) VALUES ('zzz', 'Equipo Nuevo', 0)"))
        conn.execute(
            text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                " game_date, home_score, away_score, pace) VALUES"
                " ('future-g', 1, 1, 'zzz', 'rm', '2099-01-01', 0, 0, 70.0)"
            )
        )
    catalog = _catalog_at(engine, dt.date(2026, 1, 20))
    result = catalog.execute("1", "fatigue_profile", {"team_id": "zzz"}).result
    assert result["error"] == "sin partidos jugados"


def test_fatigue_profile_always_carries_the_no_medical_data_disclaimer(engine):
    catalog = _catalog_at(engine, dt.date(2026, 1, 20))
    result = catalog.execute("1", "fatigue_profile", {"team_id": "bas"}).result
    assert any("predicción de lesión" in w for w in result["meta"]["warnings"])


def test_fatigue_profile_reports_provenance_and_sample_size(engine):
    catalog = _catalog_at(engine, dt.date(2026, 1, 20))
    result = catalog.execute("1", "fatigue_profile", {"team_id": "bas"}).result
    assert result["meta"]["source"]
    assert result["meta"]["gp"] == 5  # los 5 partidos del seed, todos ya jugados a esa fecha
