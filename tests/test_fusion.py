"""Tests para `scraper/fusion.py`.

Cubre la fusión y deduplicación de partidos de varias fuentes, priorizando
RealGM sobre el resto y desempatando a igual prioridad por la cantidad de
datos aportados.
"""
from apps.ingest.scraper.fusion import SOURCE_PRIORITY, merge_sources


def _game(date, opponent, is_home, **extra):
    """Construye un partido con el contrato plano de `scraper/`."""
    game = {
        "date": date,
        "opponent": opponent,
        "opponent_slug": None,
        "boxscore_url": None,
        "is_home": is_home,
        "points": None,
        "opp_points": None,
        "notes": None,
        "league": "acb",
    }
    game.update(extra)
    return game


def test_source_priority_order():
    """RealGM tiene la máxima prioridad y ACB la mínima."""
    assert SOURCE_PRIORITY["realgm"] < SOURCE_PRIORITY["bbr"]
    assert SOURCE_PRIORITY["bbr"] < SOURCE_PRIORITY["cms"]
    assert SOURCE_PRIORITY["cms"] < SOURCE_PRIORITY["acb"]


def test_merge_no_duplicates():
    """Partidos idénticos de una sola fuente no se duplican."""
    games = [_game("2025-10-02", "Real Madrid", False)]
    merged = merge_sources([("realgm", games)])
    assert len(merged) == 1


def test_merge_dedup_across_sources():
    """El mismo partido en dos fuentes se deduplica a uno."""
    realgm = [_game("2025-10-02", "Real Madrid", False, points=85, opp_points=90)]
    bbr = [_game("2025-10-02", "Real Madrid", False, points=85, opp_points=90)]
    merged = merge_sources([("realgm", realgm), ("bbr", bbr)])
    assert len(merged) == 1


def test_merge_prefers_realgm():
    """A igual partido, gana la fuente de mayor prioridad (RealGM)."""
    realgm = [_game("2025-10-02", "Real Madrid", False, points=85, opp_points=90)]
    bbr = [_game("2025-10-02", "Real Madrid", False, points=84, opp_points=91)]
    merged = merge_sources([("bbr", bbr), ("realgm", realgm)])
    assert len(merged) == 1
    assert merged[0]["points"] == 85
    assert merged[0]["opp_points"] == 90


def test_merge_prefers_more_data_at_same_priority():
    """A igual prioridad, gana la fuente con más datos (resultado/boxscore)."""
    sparse = [_game("2025-10-02", "Real Madrid", False)]
    rich = [_game("2025-10-02", "Real Madrid", False, points=85, opp_points=90, boxscore_url="/boxscores/x")]
    merged = merge_sources([("bbr", sparse), ("bbr", rich)])
    assert len(merged) == 1
    assert merged[0]["points"] == 85
    assert merged[0]["boxscore_url"] == "/boxscores/x"


def test_merge_keeps_distinct_games():
    """Partidos distintos (fecha, rival o local/visitante) se conservan."""
    g1 = _game("2025-10-02", "Real Madrid", False)
    g2 = _game("2025-10-05", "Real Madrid", True)
    g3 = _game("2025-10-02", "Barcelona", True)
    merged = merge_sources([("realgm", [g1, g2, g3])])
    assert len(merged) == 3


def test_merge_normalizes_team_names():
    """Nombres con acentos/sufijos distintos se consideran el mismo rival."""
    realgm = [_game("2025-10-02", "Real Madrid", False)]
    bbr = [_game("2025-10-02", "Real Madrid BC", False)]
    merged = merge_sources([("realgm", realgm), ("bbr", bbr)])
    assert len(merged) == 1


def test_merge_empty_sources():
    """Sin fuentes, devuelve lista vacía."""
    assert merge_sources([]) == []
