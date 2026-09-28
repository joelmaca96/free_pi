"""Tests de `ingest.acb.profiles`: ficha de jugador desde la web de acb.com.

Sin red. El parser se prueba contra un recorte REAL de la página
(`fixtures/acb_player_profile_30002444.html`, descargada el 2026-09-28), que
conserva las dos trampas del HTML: el bloque esqueleto con las mismas
etiquetas y los valores vacíos ANTES del bueno, y las clases CSS con sufijo
hash. La parte de base de datos, contra la BD en memoria con el seed.
"""
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

from ingest.acb import profiles
from ingest.acb.profiles import (
    apply_profile,
    normalize_position,
    parse_birth_date,
    parse_height_cm,
    parse_info_grid,
    parse_profile,
    run,
    select_candidates,
)

FIXTURE = Path(__file__).parent / "fixtures" / "acb_player_profile_30002444.html"
LICENSE = "30002444"


@pytest.fixture(scope="module")
def html():
    return FIXTURE.read_text(encoding="utf-8")


# --- parser -------------------------------------------------------------------


def test_the_fixture_really_has_the_skeleton_duplicates(html):
    """Si alguien "limpia" el fixture y quita el esqueleto, el test de abajo deja de probar nada."""
    assert html.count(">Altura<") == 2


def test_the_filled_values_win_over_the_empty_skeleton(html):
    grid = parse_info_grid(html)

    assert grid == {
        "Posición": "Ala-pívot",
        "Altura": "2,03 m",
        "Fecha nacimiento": "07/03/1995 (31 años)",
        "Lugar nacimiento": "Chesterfield (Virginia), EE.UU.",
        "Nacionalidad": "EE.UU.",
        "Licencia": "EXT",
    }


def test_the_profile_comes_out_in_db_units(html):
    assert parse_profile(html) == {
        "position": "Ala-pívot",
        "height_cm": 203,
        "birth_date": "1995-03-07",   # día/mes/año -> ISO
        "nationality": "EE.UU.",      # tal cual: ya en castellano
    }


def test_the_hashed_class_suffix_does_not_matter(html):
    """El hash de los CSS modules cambia con cada despliegue de acb.com."""
    rehashed = html.replace("REJd4q", "zz99XY")

    assert parse_profile(rehashed)["height_cm"] == 203


def test_a_page_without_the_grid_gives_nothing():
    """Licencia desconocida (acb.com redirige al listado de equipos) o maqueta nueva."""
    assert parse_info_grid("<html><body><h1>Equipos</h1></body></html>") == {}
    assert parse_profile("<html></html>") == {
        "position": None, "height_cm": None, "birth_date": None, "nationality": None,
    }


def test_only_the_skeleton_gives_nothing(html):
    skeleton_only = html.split("<!-- ... markup no relacionado eliminado ... -->")[0]

    assert parse_info_grid(skeleton_only) == {}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Base", "Base"), ("Escolta", "Escolta"), ("Alero", "Alero"),
        ("Ala-pívot", "Ala-pívot"), ("Ala Pívot", "Ala-pívot"), ("ALA-PIVOT", "Ala-pívot"),
        ("Pívot", "Pívot"), ("pivot", "Pívot"),
        ("Base-escolta", None),   # sexta etiqueta: no se escribe, la app no la conoce
        ("", None), (None, None),
    ],
)
def test_positions_map_to_the_app_vocabulary(raw, expected):
    assert normalize_position(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2,03 m", 203), ("1,85 m", 185), ("2.10 m", 210), ("2,03m", 203), ("198 cm", 198),
        ("0,00 m", None), ("", None), (None, None), ("-", None),
    ],
)
def test_heights(raw, expected):
    assert parse_height_cm(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("07/03/1995 (31 años)", "1995-03-07"), ("1/12/2004", "2004-12-01"),
        ("31/02/1990", None), ("", None), (None, None),
    ],
)
def test_birth_dates(raw, expected):
    assert parse_birth_date(raw) == expected


# --- base de datos ------------------------------------------------------------

FIELDS = {"position": "Ala-pívot", "height_cm": 203, "birth_date": "1995-03-07", "nationality": "EE.UU."}


def _link(engine, player_id, license_id, source="acb"):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO player_external_ids (player_id, source, external_id) VALUES (:p, :s, :e)"),
            {"p": player_id, "s": source, "e": license_id},
        )


def _row(engine, player_id):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT position, height_cm, birth_date, nationality FROM players WHERE id = :id"),
            {"id": player_id},
        ).first()


def _blank(engine, player_id):
    with engine.begin() as conn:
        conn.execute(text("UPDATE players SET position = '' WHERE id = :id"), {"id": player_id})


def test_gaps_are_filled(engine):
    _blank(engine, "moneke")
    with engine.begin() as conn:
        filled = apply_profile(conn, "moneke", FIELDS)

    assert sorted(filled) == ["birth_date", "height_cm", "nationality", "position"]
    assert tuple(_row(engine, "moneke")) == ("Ala-pívot", 203, "1995-03-07", "EE.UU.")


def test_existing_values_are_never_overwritten(engine):
    """El `gameRole` de ACB, baskonia.com y Euroliga ya escribieron: lo que hay gana."""
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE players SET height_cm = 206, nationality = 'Estados Unidos' WHERE id = 'moneke'"
        ))
        filled = apply_profile(conn, "moneke", {**FIELDS, "position": "Pívot"})

    row = _row(engine, "moneke")
    assert row.position == "Ala-pívot"        # la del seed, no 'Pívot'
    assert row.height_cm == 206
    assert row.nationality == "Estados Unidos"
    assert row.birth_date == "1995-03-07"     # el único hueco
    assert filled == ["birth_date"]


def test_it_never_creates_a_player(engine):
    before = engine.connect().execute(text("SELECT COUNT(*) FROM players")).scalar()
    with engine.begin() as conn:
        assert apply_profile(conn, "no-existe", FIELDS) == []

    assert engine.connect().execute(text("SELECT COUNT(*) FROM players")).scalar() == before


def test_candidates_need_an_acb_license_and_a_gap(engine):
    """Sin licencia de ACB no hay página que pedir; sin huecos, nada que rellenar."""
    _link(engine, "moneke", LICENSE)                      # con huecos (el seed no tiene ficha)
    _link(engine, "kotsar", "P012345", source="euroleague")  # sin licencia de ACB
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE players SET height_cm = 211, birth_date = '1996-10-04', nationality = 'Estonia'"
            " WHERE id = 'howard'"
        ))
    _link(engine, "howard", "30000001")                   # licencia pero ficha completa

    with engine.connect() as conn:
        assert select_candidates(conn) == [("moneke", LICENSE)]


def test_blank_positions_go_first(engine):
    _link(engine, "moneke", "1")
    _link(engine, "kotsar", "2")
    _blank(engine, "kotsar")

    with engine.connect() as conn:
        assert [player for player, _ in select_candidates(conn)] == ["kotsar", "moneke"]


def test_a_row_with_two_acb_licenses_is_left_alone(engine):
    """Huella de dos personas fusionadas en una fila (`find_merged_players`): la ficha de una
    de las dos solo escondería el problema."""
    _link(engine, "moneke", "1")
    _link(engine, "moneke", "2")

    with engine.connect() as conn:
        assert select_candidates(conn) == []


# --- run (red simulada) -------------------------------------------------------


class _Response:
    def __init__(self, url, body="", status=200):
        self.url = url
        self.text = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


class _Session:
    """Sirve la ficha del fixture para cada licencia en `pages`; lo demás redirige a equipos."""

    def __init__(self, pages, fail=()):
        self.pages = pages
        self.fail = set(fail)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        license_id = url.rstrip("/").rsplit("/", 1)[-1]
        if license_id in self.fail:
            import requests

            raise requests.ConnectionError("acb.com no responde")
        if license_id in self.pages:
            return _Response(f"https://acb.com/es/liga/jugadores/x-{license_id}", self.pages[license_id])
        return _Response("https://acb.com/es/liga/equipos", "<html></html>")


@pytest.fixture(autouse=True)
def _robots_allowed(monkeypatch):
    """El helper de robots.txt de baskonia_web cachea por origen a nivel de módulo; aquí se
    da por permitido (acb.com lo permite, comprobado en vivo) para no depender de ese estado."""
    monkeypatch.setattr(profiles, "_is_allowed_by_robots", lambda session, url: True)


def _run(engine, session, **kwargs):
    kwargs.setdefault("cache_path", None)
    return run(engine, session=session, sleep=lambda s: None, today=date(2026, 9, 28), **kwargs)


def test_run_fills_the_profile_and_counts_it(engine, html):
    _blank(engine, "moneke")
    _link(engine, "moneke", LICENSE)
    session = _Session({LICENSE: html})

    summary = _run(engine, session)

    assert summary["fetched"] == 1 and summary["updated"] == 1
    assert summary["filled"] == {"position": 1, "height_cm": 1, "birth_date": 1, "nationality": 1}
    assert tuple(_row(engine, "moneke")) == ("Ala-pívot", 203, "1995-03-07", "EE.UU.")
    assert session.headers["User-Agent"] == profiles.USER_AGENT


def test_run_without_candidates_touches_neither_network_nor_disk(engine, tmp_path):
    session = _Session({})
    cache = tmp_path / "seen.json"

    summary = _run(engine, session, cache_path=str(cache))

    assert summary["candidates"] == 0
    assert session.calls == []
    assert not cache.exists()


def test_one_broken_player_does_not_stop_the_others(engine, html):
    _link(engine, "kotsar", "111")       # red caída
    _link(engine, "lutse", "222")        # licencia que acb.com no conoce
    _link(engine, "moneke", LICENSE)
    session = _Session({LICENSE: html}, fail={"111"})

    summary = _run(engine, session)

    assert summary["failed"] == 1
    assert summary["not_found"] == 1
    assert summary["updated"] == 1
    assert _row(engine, "moneke").height_cm == 203


def test_limit_and_delay(engine, html):
    for player, license_id in (("kotsar", "1"), ("lutse", "2"), ("moneke", "3")):
        _link(engine, player, license_id)
    session = _Session({})
    sleeps = []

    summary = run(engine, session=session, sleep=sleeps.append, cache_path=None, limit=2, delay=1.5)

    assert len(session.calls) == 2
    assert sleeps == [1.5]                 # entre peticiones, no antes de la primera
    assert summary["candidates"] == 3


def test_a_profile_already_read_is_not_asked_again_until_retry_days(engine, html, tmp_path):
    """acb.com no publica la fecha de nacimiento de todos: sin la caché, esos jugadores se
    comerían el tope de cada pasada para siempre."""
    page_without_birth = html.replace("07/03/1995 (31 años)", "")
    _link(engine, "moneke", LICENSE)
    cache = tmp_path / "seen.json"

    first = _run(engine, _Session({LICENSE: page_without_birth}), cache_path=str(cache))
    assert first["updated"] == 1
    assert json.loads(cache.read_text()) == {LICENSE: "2026-09-28"}

    second_session = _Session({LICENSE: page_without_birth})
    second = _run(engine, second_session, cache_path=str(cache))
    assert second["skipped_recent"] == 1
    assert second_session.calls == []

    later = run(engine, session=_Session({LICENSE: html}), sleep=lambda s: None, cache_path=str(cache),
                today=date(2026, 11, 1))
    assert later["fetched"] == 1
    assert _row(engine, "moneke").birth_date == "1995-03-07"


def test_a_network_failure_is_not_cached(engine, tmp_path):
    """Un fallo de red se reintenta en la siguiente pasada, no dentro de 30 días."""
    _link(engine, "moneke", LICENSE)
    cache = tmp_path / "seen.json"

    _run(engine, _Session({}, fail={LICENSE}), cache_path=str(cache))

    assert json.loads(cache.read_text()) == {}
