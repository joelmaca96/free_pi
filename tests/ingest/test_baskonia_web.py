"""Tests del scraper/loader de la plantilla de baskonia.com.

Fixture: JSON embebido real (`<script id="serverApp-state">`), con la misma
forma verificada contra la web real (ver `scraper.py`), no HTML de tarjetas.
"""
import json

from sqlalchemy import text

from ingest.baskonia_web.loader import load_roster
from ingest.baskonia_web.scraper import parse_roster


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
    assert howard.photo_url == "https://www.baskonia.com/uploads/howard.png"
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
