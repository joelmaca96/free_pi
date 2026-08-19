"""Tests para `main.py` (lógica de selección y emparejamiento de equipos).

Cubre las funciones puras y de resolución de equipos que han sido fuente de
bugs sutiles en el pasado: `_normalize_team_name`, `_select_boxscores`
(enfrentamientos directos + últimos N) y `resolve_opponent_team`
(emparejamiento por subcadena y migración de slugs falsos).
"""
import pytest

from apps.ingest.pipeline import (
    _capture_realgm_boxscore,
    _is_realgm_url,
    _normalize_team_name,
    _select_boxscores,
    resolve_opponent_team,
)
from apps.ingest.scraper import realgm
from packages.baskonia_core.db import models
from packages.baskonia_core.db.storage import upsert_game, upsert_team


# --- _normalize_team_name --------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("Baskonia", "baskonia"),
        ("Surne Bilbao Basket", "surnebilbaobasket"),
        ("Río Breogán", "riobreogan"),  # acentos eliminados
        ("Club Joventut Badalona", "clubjoventutbadalona"),
        ("LDLC ASVEL", "ldlcasvel"),
        ("  Real  Madrid  ", "realmadrid"),  # espacios colapsados
        ("Gran-Canaria", "grancanaria"),  # guiones eliminados
    ],
)
def test_normalize_team_name(name, expected):
    """La normalización elimina acentos, espacios y guiones."""
    assert _normalize_team_name(name) == expected


# --- _select_boxscores -----------------------------------------------------

def _game(boxscore_url, opponent, **kwargs):
    """Helper para construir un dict de partido como el de parse_schedule_games."""
    game = {
        "date": "Sun, Nov 23, 2025",
        "opponent": opponent,
        "opponent_slug": None,
        "boxscore_url": boxscore_url,
        "is_home": True,
        "points": "88",
        "opp_points": "80",
        "notes": "",
        "league": "acb",
    }
    game.update(kwargs)
    return game


def test_select_boxscores_last_n():
    """Selecciona los últimos N partidos jugados de cada equipo."""
    games = [
        _game(f"/boxscores/g{i}.html", "Rival A") for i in range(5)
    ]
    selected = _select_boxscores({"vitoria": games}, ["vitoria"], last_n=3)
    # Solo los 3 últimos
    urls = [g["boxscore_url"] for g in selected]
    assert urls == ["/boxscores/g2.html", "/boxscores/g3.html", "/boxscores/g4.html"]


def test_select_boxscores_head_to_head():
    """Incluye enfrentamientos directos aunque queden fuera de los últimos N."""
    games = [
        _game("/boxscores/g0.html", "Surne Bilbao Basket"),  # enfrentamiento directo, antiguo
        _game("/boxscores/g1.html", "Real Madrid"),
        _game("/boxscores/g2.html", "Barcelona"),
    ]
    selected = _select_boxscores({"vitoria": games}, ["vitoria", "bilbao"], last_n=2)
    urls = [g["boxscore_url"] for g in selected]
    # g0 (vs Bilbao) + g1, g2 (últimos 2)
    assert "/boxscores/g0.html" in urls
    assert "/boxscores/g1.html" in urls
    assert "/boxscores/g2.html" in urls


def test_select_boxscores_dedup():
    """Un partido que cumple ambos criterios no se duplica."""
    games = [
        _game("/boxscores/g0.html", "Surne Bilbao Basket"),
        _game("/boxscores/g1.html", "Real Madrid"),
    ]
    selected = _select_boxscores({"vitoria": games}, ["vitoria", "bilbao"], last_n=5)
    urls = [g["boxscore_url"] for g in selected]
    assert len(urls) == len(set(urls))  # sin duplicados


def test_select_boxscores_skips_unplayed():
    """Los partidos sin boxscore_url (no jugados) no se seleccionan."""
    games = [
        _game("", "Real Madrid"),  # sin box score (pendiente)
        _game("/boxscores/g1.html", "Barcelona"),
    ]
    selected = _select_boxscores({"vitoria": games}, ["vitoria"], last_n=5)
    assert len(selected) == 1
    assert selected[0]["boxscore_url"] == "/boxscores/g1.html"


def test_select_boxscores_substring_matching():
    """El emparejamiento de rivales es por subcadena (bug histórico)."""
    # El slug "bilbao" debe coincidir con el nombre de display "Surne Bilbao Basket"
    games = [
        _game("/boxscores/g0.html", "Surne Bilbao Basket"),
    ]
    selected = _select_boxscores({"vitoria": games}, ["vitoria", "bilbao"], last_n=1)
    assert len(selected) == 1


# --- resolve_opponent_team -------------------------------------------------

def test_resolve_opponent_team_creates_new(session):
    """Un rival desconocido se crea con el slug real de BBR."""
    team = resolve_opponent_team(session, "LDLC ASVEL", "villeurbanne", "acb")
    assert team.slug == "villeurbanne"
    assert team.name == "LDLC ASVEL"


def test_resolve_opponent_team_reuses_by_slug(session):
    """Un rival ya existente por slug se reutiliza."""
    existing = models.Team(slug="bilbao", name="Surne Bilbao Basket", league="acb")
    session.add(existing)
    session.flush()

    team = resolve_opponent_team(session, "Surne Bilbao Basket", "bilbao", "acb")
    assert team.id == existing.id
    # No se crea duplicado
    assert session.query(models.Team).count() == 1


def test_resolve_opponent_team_substring_match(session):
    """Un rival existente se encuentra por subcadena aunque el nombre difiera."""
    existing = models.Team(slug="bilbao", name="Surne Bilbao Basket", league="acb")
    session.add(existing)
    session.flush()

    # Nombre distinto pero que contiene el nombre normalizado del existente
    team = resolve_opponent_team(session, "Bilbao", None, "acb")
    assert team.id == existing.id


def test_resolve_opponent_team_migrates_fake_slug(session):
    """Un equipo con slug 'falso' (de ejecuciones antiguas) se migra al slug real."""
    existing = models.Team(slug="clubjoventutbadalona", name="Club Joventut Badalona", league="acb")
    session.add(existing)
    session.flush()

    team = resolve_opponent_team(session, "Joventut", "joventut", "acb")
    assert team.id == existing.id
    # El slug se migró al real
    assert team.slug == "joventut"
    # No se crea duplicado
    assert session.query(models.Team).count() == 1


def test_resolve_opponent_team_no_slug_keeps_existing_slug(session):
    """Sin slug real, un equipo existente se reutiliza sin tocar su slug."""
    existing = models.Team(slug="bilbao", name="Surne Bilbao Basket", league="acb")
    session.add(existing)
    session.flush()

    team = resolve_opponent_team(session, "Bilbao", None, "acb")
    assert team.id == existing.id
    assert team.slug == "bilbao"  # no se migra


# --- _is_realgm_url --------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://basketball.realgm.com/international/boxscore/123", True),
        ("http://realgm.com/x", True),
        ("https://www.basketball-reference.com/international/boxscores/x.html", False),
        ("https://www.acb.com/partido/1", False),
        ("", False),
    ],
)
def test_is_realgm_url(url, expected):
    """Distingue los box scores de RealGM de los de otras fuentes.

    `_capture_realgm_boxscore` solo sabe parsear RealGM: si la fusión dejó
    ganar a BBR, la URL guardada es de otro host y hay que omitirla en vez de
    intentar descargarla.
    """
    assert _is_realgm_url(url) is expected


def test_capture_realgm_boxscore_skips_foreign_url(session, monkeypatch, caplog):
    """Un `boxscore_url` que no es de RealGM no se descarga."""
    called = []
    monkeypatch.setattr(
        realgm, "fetch_game_boxscore", lambda url: called.append(url) or {"home": [], "away": []}
    )

    home = upsert_team(session, "vitoria", "Baskonia", "acb")
    away = upsert_team(session, "real-madrid", "Real Madrid", "acb")
    game = upsert_game(
        session,
        date="2025-10-05",
        league="acb",
        home_team=home,
        away_team=away,
        boxscore_url="https://www.basketball-reference.com/international/boxscores/x.html",
    )
    session.commit()

    with caplog.at_level("WARNING"):
        _capture_realgm_boxscore(session, game)

    assert called == []
    assert "no RealGM" in caplog.text
