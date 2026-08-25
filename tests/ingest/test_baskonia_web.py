"""Tests del scraper/loader de la plantilla de baskonia.com.

Fixture: JSON embebido real (`<script id="serverApp-state">`), con la misma
forma verificada contra la web real (ver `scraper.py`), no HTML de tarjetas.
"""
import json

import pytest
import requests
from sqlalchemy import text

import ingest.baskonia_web.scraper as scraper_module
from ingest.baskonia_web.loader import load_roster
from ingest.baskonia_web.scraper import ScrapedPlayer, download_player_photos, parse_roster


@pytest.fixture(autouse=True)
def _reset_robots_cache(monkeypatch):
    """`download_player_photos` cachea `robots.txt` por origen a nivel de módulo (ver
    `scraper._robots_cache`) para no pedirlo una vez por foto — sin resetear entre
    tests, el primer test que lo puebla contaminaría a los siguientes."""
    monkeypatch.setattr(scraper_module, "_robots_cache", {})


def _member(member_id, role, name, last_name, position=None, dorsal=None, photo=None, birthday=None, nationality=None):
    return {
        "id": member_id,
        "attributes": {
            "team_member_role": {"data": {"attributes": {"label": role}}},
            "team_member_position": (
                {"data": {"attributes": {"label": position}}} if position else {"data": None}
            ),
            "dorsal": dorsal,
            "name": name,
            "lastName": last_name,
            "photo": {"data": {"attributes": {"url": photo}}} if photo else {"data": None},
            "birthday": birthday,
            "nationality": nationality,
        },
    }


SERVER_STATE = {
    "page-plantilla-es-baskonia": {
        "extendedPage": {
            "page": {
                "data": {
                    "attributes": {
                        "content": [
                            {"__typename": "ComponentSharedBreadcrumb"},
                            {
                                "__typename": "ComponentSharedTeamPage",
                                "team": {
                                    "data": {
                                        "attributes": {
                                            "name": "Kosner Baskonia",
                                            "members": {
                                                "data": [
                                                    _member(
                                                        "145", "Jugador", "Markus", "Howard",
                                                        position="Escolta", dorsal=0,
                                                        photo="/uploads/howard.png",
                                                        birthday="1999-03-03", nationality="EE.UU.",
                                                    ),
                                                    _member(
                                                        "150", "Jugador", "Tadas", "Sedekerskis",
                                                        position="Ala Pívot", dorsal=8,
                                                        photo="/uploads/sedekerskis.png",
                                                        birthday="1998-01-17", nationality="Lituania",
                                                    ),
                                                    _member(
                                                        "923", "Jugador", "Damion", "Baugh",
                                                        position="Base", dorsal=None,
                                                        photo="/uploads/silueta_generica_aee3.webp",
                                                    ),
                                                    _member(
                                                        "887", "Jugador", "Kobi", "Simmons",
                                                        position="Base", dorsal=25,
                                                        photo="/uploads/kobi.png",
                                                    ),
                                                    _member("500", "Entrenador", "Sergio", "Scariolo"),
                                                ]
                                            },
                                        }
                                    }
                                },
                            },
                        ]
                    }
                }
            }
        }
    }
}

FIXTURE_HTML = (
    '<html><body><script id="serverApp-state" type="application/json">'
    + json.dumps(SERVER_STATE)
    + "</script></body></html>"
)


def test_parse_roster_extracts_only_players_with_real_fields():
    players = parse_roster(FIXTURE_HTML)

    assert [p.name for p in players] == ["Markus Howard", "Tadas Sedekerskis", "Damion Baugh", "Kobi Simmons"]
    assert [p.position for p in players] == ["Escolta", "Ala-pívot", "Base", "Base"]
    assert [p.number for p in players] == [0, 8, None, 25]
    assert [p.external_id for p in players] == ["145", "150", "923", "887"]

    howard = players[0]
    # Host real de medios (Strapi, `cms.deportivoalaves.com`), NO `www.baskonia.com` —
    # ver hallazgo en el docstring de `scraper.py` (esa ruta ahí solo devuelve la SPA).
    assert howard.photo_url == "https://cms.deportivoalaves.com/uploads/howard.png"
    assert howard.birth_date == "1999-03-03"
    assert howard.nationality == "EE.UU."

    baugh = players[2]
    assert baugh.photo_url is None  # silueta genérica -> no es una foto real


def test_load_roster_reuses_existing_players_by_number_and_deactivates_missing(engine):
    players = parse_roster(FIXTURE_HTML)

    with engine.begin() as conn:
        summary = load_roster(conn, players)

    with engine.connect() as conn:
        # Howard (dorsal 0) y Sedekerskis (dorsal 8) ya estaban en el seed: se reutiliza su id.
        assert "howard" in summary["active"]
        assert "sedekerskis" in summary["active"]
        row = conn.execute(
            text("SELECT photo_url, birth_date, nationality FROM players WHERE id = 'howard'")
        ).first()
        assert row.birth_date == "1999-03-03"
        assert row.nationality == "EE.UU."

        # Jugadores nuevos (sin dorsal coincidente en el seed) se crean.
        assert conn.execute(text("SELECT COUNT(*) FROM players WHERE name = 'Damion Baugh'")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM players WHERE name = 'Kobi Simmons'")).scalar_one() == 1

        # El resto del seed (moneke, codi, nikos, kotsar, costello, lutse) ya no aparece
        # en la plantilla scrapeada -> se desactiva, no se borra.
        for old_id in ("moneke", "codi", "nikos", "kotsar", "costello", "lutse"):
            active = conn.execute(text("SELECT active FROM players WHERE id = :id"), {"id": old_id}).scalar_one()
            assert active == 0
            assert old_id in summary["deactivated"]

        assert conn.execute(text("SELECT COUNT(*) FROM players")).scalar_one() == 10  # 8 seed + 2 nuevos


def test_load_roster_is_idempotent_on_rerun(engine):
    players = parse_roster(FIXTURE_HTML)
    with engine.begin() as conn:
        load_roster(conn, players)
    with engine.begin() as conn:
        load_roster(conn, players)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM players")).scalar_one() == 10
        assert conn.execute(
            text("SELECT COUNT(*) FROM player_external_ids WHERE source='baskonia_web'")
        ).scalar_one() == 4


# ==================================================== download_player_photos ==

class _FakePhotoResponse:
    def __init__(self, status_code=200, content=b"", content_type="image/png", text=""):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type} if content_type else {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakePhotoSession:
    """Simula `robots.txt` (permite todo salvo `disallow_paths`) + descarga de fotos por URL."""

    def __init__(self, photos_by_url=None, fail_urls=None, disallow_paths=()):
        self.headers = {}
        self.photos_by_url = photos_by_url or {}
        self.fail_urls = fail_urls or set()
        self.disallow_paths = disallow_paths
        self.requested_urls = []

    def get(self, url, timeout=30):
        self.requested_urls.append(url)
        if url.endswith("/robots.txt"):
            body = "User-agent: *\n" + "\n".join(f"Disallow: {p}" for p in self.disallow_paths)
            return _FakePhotoResponse(200, text=body)
        if url in self.fail_urls:
            raise requests.ConnectionError("boom")
        if url in self.photos_by_url:
            content, content_type = self.photos_by_url[url]
            return _FakePhotoResponse(200, content=content, content_type=content_type)
        return _FakePhotoResponse(404, text="not found")


def _player(external_id, photo_url):
    return ScrapedPlayer(
        external_id=external_id, name=f"Jugador {external_id}", position="Base", number=1,
        photo_url=photo_url, birth_date=None, nationality=None,
    )


def test_download_player_photos_saves_new_files_and_sets_local_path(tmp_path):
    players = [
        _player("145", "https://www.baskonia.com/uploads/howard.png"),
        _player("150", "https://www.baskonia.com/uploads/sedekerskis.jpg"),
        _player("923", None),  # silueta genérica -> sin photo_url, no debe generar tráfico
    ]
    session = _FakePhotoSession(
        photos_by_url={
            "https://www.baskonia.com/uploads/howard.png": (b"fake-png-bytes", "image/png"),
            "https://www.baskonia.com/uploads/sedekerskis.jpg": (b"fake-jpg-bytes", "image/jpeg"),
        }
    )

    download_player_photos(players, session=session, photos_dir=str(tmp_path))

    howard, sedekerskis, baugh = players
    assert howard.photo_local_path == str(tmp_path / "145.png")
    assert (tmp_path / "145.png").read_bytes() == b"fake-png-bytes"
    assert sedekerskis.photo_local_path == str(tmp_path / "150.jpg")
    assert (tmp_path / "150.jpg").read_bytes() == b"fake-jpg-bytes"

    assert baugh.photo_local_path is None
    assert not any("923" in url for url in session.requested_urls)  # nunca se pidió por red

    # No queda ningún fichero temporal ".part" a medio escribir.
    assert not list(tmp_path.glob("*.part"))


def test_download_player_photos_skips_players_already_downloaded(tmp_path):
    (tmp_path / "145.png").write_bytes(b"ya-en-disco")
    players = [_player("145", "https://www.baskonia.com/uploads/howard.png")]
    session = _FakePhotoSession()  # sin fixture de la foto -> fallaría (404) si se llegara a pedir

    download_player_photos(players, session=session, photos_dir=str(tmp_path))

    assert players[0].photo_local_path == str(tmp_path / "145.png")
    assert (tmp_path / "145.png").read_bytes() == b"ya-en-disco"  # no se sobrescribe
    assert session.requested_urls == []  # ni la foto ni robots.txt: se saltó antes de tocar la red


def test_download_player_photos_survives_network_failure_of_one_player(tmp_path):
    players = [
        _player("145", "https://www.baskonia.com/uploads/caido.png"),
        _player("150", "https://www.baskonia.com/uploads/sedekerskis.jpg"),
    ]
    session = _FakePhotoSession(
        photos_by_url={"https://www.baskonia.com/uploads/sedekerskis.jpg": (b"ok", "image/jpeg")},
        fail_urls={"https://www.baskonia.com/uploads/caido.png"},
    )

    download_player_photos(players, session=session, photos_dir=str(tmp_path))

    assert players[0].photo_local_path is None  # falló, pero no interrumpe el resto
    assert players[1].photo_local_path == str(tmp_path / "150.jpg")


def test_download_player_photos_rejects_non_image_content_type(tmp_path):
    players = [_player("145", "https://www.baskonia.com/uploads/howard.png")]
    session = _FakePhotoSession(
        photos_by_url={"https://www.baskonia.com/uploads/howard.png": (b"<html>login</html>", "text/html")}
    )

    download_player_photos(players, session=session, photos_dir=str(tmp_path))

    assert players[0].photo_local_path is None
    assert not list(tmp_path.iterdir())


def test_download_player_photos_respects_robots_disallow(tmp_path):
    players = [_player("145", "https://www.baskonia.com/uploads/howard.png")]
    session = _FakePhotoSession(
        photos_by_url={"https://www.baskonia.com/uploads/howard.png": (b"ok", "image/png")},
        disallow_paths=["/uploads/"],
    )

    download_player_photos(players, session=session, photos_dir=str(tmp_path))

    assert players[0].photo_local_path is None
    assert not list(tmp_path.iterdir())


def test_load_roster_persists_photo_local_path(engine):
    players = parse_roster(FIXTURE_HTML)
    players[0].photo_local_path = "player_photos/145.png"  # simula descarga ya hecha

    with engine.begin() as conn:
        load_roster(conn, players)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT photo_local_path FROM players WHERE id = 'howard'")).first()
        assert row.photo_local_path == "player_photos/145.png"
