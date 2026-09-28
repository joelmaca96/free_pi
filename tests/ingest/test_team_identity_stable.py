"""Identidad de club robusta sin alias a mano (2026-09-28).

Tres piezas, cada una con lo que NO puede torcerse:

1. `clubId` estable de ACB (`teams.acb_club_id`): un patrocinador nuevo que
   no está en `_KNOWN_TEAM_ALIASES` cae en la fila de siempre — pero un
   equipo de cantera (Liga U), que comparte `clubId` con el primer equipo,
   NO.
2. Emparejador difuso (`find_team_identity_suggestions`): encuentra "Kids&Us
   Manresa" ~ "BAXI Manresa", pero no sugiere juntar a dos clubes de la misma
   ciudad que juegan la misma liga la misma temporada, y marca como ambiguo
   lo que podría ser de dos clubes. Nunca vincula nada.
3. `tools/fix_team_identity.py` agrupa también por `acb_club_id` y se niega
   a fusionar un grupo en el que ACB dice que hay dos clubes.

Nombres de patrocinador inventados a propósito ("Patrocinador Nuevo
Manresa"): los reales ya están en `_KNOWN_TEAM_ALIASES` y el test no
probaría nada.
"""
from unittest import mock

from sqlalchemy import text

from ingest.acb import client as client_module
from ingest.acb.adapter import build_scheduled_matchup, senior_acb_club_id
from ingest.acb.pipeline import run_upcoming
from ingest.common.identity import (
    find_team_identity_collisions,
    find_team_identity_suggestions,
    resolve_or_create_team,
)
from tools import fix_team_identity


def _team(conn, team_id):
    return conn.execute(
        text("SELECT id, name, acb_club_id FROM teams WHERE id = :id"), {"id": team_id}
    ).one()


def _add_team(conn, team_id, name, acb_club_id=None, is_own_team=0):
    conn.execute(
        text(
            "INSERT INTO teams (id, name, is_own_team, acb_club_id)"
            " VALUES (:id, :name, :own, :club)"
        ),
        {"id": team_id, "name": name, "own": is_own_team, "club": acb_club_id},
    )


def _season(conn, label):
    row = conn.execute(text("SELECT id FROM seasons WHERE label = :l"), {"l": label}).first()
    if row:
        return row[0]
    return conn.execute(text("INSERT INTO seasons (label) VALUES (:l)"), {"l": label}).lastrowid


def _competition(conn, name):
    row = conn.execute(text("SELECT id FROM competitions WHERE name = :n"), {"n": name}).first()
    if row:
        return row[0]
    return conn.execute(text("INSERT INTO competitions (name) VALUES (:n)"), {"n": name}).lastrowid


_game_counter = [0]


def _add_game(conn, home, away, season="2024-2025", competition="ACB", date=None):
    _game_counter[0] += 1
    n = _game_counter[0]
    conn.execute(
        text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace)"
            " VALUES (:id, :s, :c, :h, :a, :d, 80, 70, 72.0)"
        ),
        {
            "id": f"g-{n}", "s": _season(conn, season), "c": _competition(conn, competition),
            "h": home, "a": away, "d": date or f"2025-01-{(n % 28) + 1:02d}",
        },
    )


# --- 1. clubId estable de ACB ------------------------------------------------


def test_a_new_sponsor_name_without_alias_lands_on_the_same_club_by_acb_club_id(engine):
    with engine.begin() as conn:
        first = resolve_or_create_team(conn, "acb", "4340", "Patrocinador Viejo Manresa", acb_club_id=10)
        # Edición siguiente: id de equipo nuevo Y nombre nuevo, ninguno en alias.
        second = resolve_or_create_team(conn, "acb", "4471", "Patrocinador Nuevo Manresa", acb_club_id=10)
        linked = conn.execute(
            text("SELECT external_id FROM team_external_ids WHERE team_id = :id AND source = 'acb'"),
            {"id": first},
        ).scalars().all()

    assert second == first
    assert set(linked) == {"4340", "4471"}


def test_without_the_club_id_the_same_rename_still_creates_a_duplicate(engine):
    """Control del test anterior: prueba que es el `clubId` lo que une, no el nombre."""
    with engine.begin() as conn:
        first = resolve_or_create_team(conn, "acb", "4340", "Patrocinador Viejo Manresa")
        second = resolve_or_create_team(conn, "acb", "4471", "Patrocinador Nuevo Manresa")
    assert second != first


def test_an_existing_row_gains_its_club_id_on_the_next_ingestion(engine):
    """Filas anteriores a la columna: se rellenan solas al volver a ver el equipo."""
    with engine.begin() as conn:
        team_id = resolve_or_create_team(conn, "acb", "4414", "BAXI Manresa")  # el `baxi` del seed
        assert _team(conn, team_id).acb_club_id is None
        again = resolve_or_create_team(conn, "acb", "4414", "BAXI Manresa", acb_club_id=10)
        assert again == team_id == "baxi"
        assert _team(conn, "baxi").acb_club_id == 10


def test_a_name_match_is_refused_when_acb_says_it_is_another_club(engine):
    """Un nombre (o alias) que junta dos `clubId` distintos no se sigue: fila aparte."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE teams SET acb_club_id = 5 WHERE id = 'gc'"))  # Gran Canaria
        other = resolve_or_create_team(conn, "acb", "9999", "Gran Canaria", acb_club_id=77)
        assert other != "gc"
        assert _team(conn, other).acb_club_id == 77
        # ...y queda delatada por nombre para que alguien la mire.
        assert ("gc", other) in [tuple(sorted(p)) for p in find_team_identity_collisions(conn)]


def test_a_row_is_never_overwritten_with_a_second_club_id(engine):
    with engine.begin() as conn:
        team_id = resolve_or_create_team(conn, "acb", "4340", "BAXI Manresa", acb_club_id=10)
        resolve_or_create_team(conn, "acb", "4340", "BAXI Manresa", acb_club_id=99)
        assert _team(conn, team_id).acb_club_id == 10


def test_an_old_link_and_the_club_id_disagreeing_become_an_exact_collision(engine):
    """El duplicado que YA existía: la fila vieja enlazada por external_id y la
    nueva que lleva el `clubId` acaban con el mismo valor -> colisión exacta."""
    with engine.begin() as conn:
        _add_team(conn, "nuevo-manres", "Patrocinador Nuevo Manresa", acb_club_id=10)
        # `baxi` (seed) se resuelve por su external_id viejo y gana el clubId.
        resolve_or_create_team(conn, "acb", "4414", "BAXI Manresa")
        resolve_or_create_team(conn, "acb", "4414", "BAXI Manresa", acb_club_id=10)
        assert ("baxi", "nuevo-manres") in find_team_identity_collisions(conn)


def test_club_id_lookup_prefers_the_row_with_games_among_unmerged_duplicates(engine):
    with engine.begin() as conn:
        _add_team(conn, "a-manresa", "Patrocinador A Manresa", acb_club_id=10)
        _add_team(conn, "z-manresa", "Patrocinador Z Manresa", acb_club_id=10)
        _add_game(conn, "z-manresa", "rm")
        resolved = resolve_or_create_team(conn, "acb", "5000", "Patrocinador B Manresa", acb_club_id=10)
    assert resolved == "z-manresa"


def test_senior_acb_club_id_skips_youth_teams_that_share_the_club_id():
    """Verificado en vivo: "Fundacion CB Canarias" (Liga U, competitionId 134)
    lleva el clubId 28 de La Laguna Tenerife."""
    senior = {"id": 4478, "competitionId": 1, "clubId": 28, "fullName": "La Laguna Tenerife"}
    youth = {"id": 4416, "competitionId": 134, "clubId": 28, "fullName": "Fundacion CB Canarias"}
    copa = {"id": 1, "competitionId": 2, "clubId": 28, "fullName": "La Laguna Tenerife"}
    assert senior_acb_club_id(senior) == 28
    assert senior_acb_club_id(copa) == 28
    assert senior_acb_club_id(youth) is None
    assert senior_acb_club_id({"id": 1, "clubId": 28}) is None  # sin competitionId: no se sabe
    assert senior_acb_club_id({"id": 1, "competitionId": 1}) is None


def test_scheduled_calendar_carries_the_club_id_to_the_matchup():
    teams = {"4471": {"name": "Kids&Us Manresa", "logo_url": None, "acb_club_id": 10}}
    match = {"homeTeamId": 4463, "awayTeamId": 4471, "startDateTime": "2026-10-01T18:00:00Z"}
    matchup = build_scheduled_matchup(match, teams, "4463")
    assert matchup["opponent_acb_club_id"] == 10


def test_client_calendar_extracts_club_ids_only_for_senior_teams(monkeypatch):
    page = {
        "matches": [],
        "selectedFilters": {"week": 100},
        "teams": [
            {"id": 4471, "competitionId": 1, "clubId": 10, "shortName": "Kids&Us Manresa", "logo": None},
            {"id": 4416, "competitionId": 134, "clubId": 28, "shortName": "Fundacion CB", "logo": None},
        ],
    }
    client = client_module.AcbClient(session=mock.Mock(headers={}))

    def fake_page(edition_id, week_id):
        if week_id in (None, 100):
            return page
        raise client_module._WeekNotFound("no")

    monkeypatch.setattr(client, "_get_matches_page", fake_page)
    _scheduled, teams_by_id = client.fetch_season_scheduled_matches(2026)
    assert teams_by_id["4471"]["acb_club_id"] == 10
    assert teams_by_id["4416"]["acb_club_id"] is None


def test_a_played_game_carries_the_club_id_from_the_boxscore_to_teams(engine):
    """Camino de los partidos jugados: `build_raw_game` -> `parse_and_resolve`."""
    import copy

    from ingest.acb.parser import parse_and_resolve
    from tests.ingest.test_acb import RAW_GAME

    raw = copy.deepcopy(RAW_GAME)
    raw["away_team"]["acb_club_id"] = 14
    with engine.begin() as conn:
        first = parse_and_resolve(conn, raw)

    renamed = copy.deepcopy(RAW_GAME)
    renamed["game_id"], renamed["season"], renamed["date"] = "2026999901", 2026, "2026-10-10"
    renamed["away_team"] = {"id": "acb-uni-2027", "name": "Patrocinador Nuevo Malaga", "acb_club_id": 14}
    for row in renamed["team_stats"] + renamed["players"]:
        if row["team_id"] == "acb-uni":
            row["team_id"] = "acb-uni-2027"
    with engine.begin() as conn:
        second = parse_and_resolve(conn, renamed)
        assert _team(conn, "uni").acb_club_id == 14

    assert first.away_team_id == second.away_team_id == "uni"


def test_run_upcoming_follows_the_club_across_a_rename_of_both_teams(engine):
    """Temporada siguiente: Baskonia y rival con id de ACB y nombre nuevos, ninguno
    en alias. Ni el equipo propio se pierde (el filtro del calendario) ni el
    rival se duplica."""
    def fake_client(own_id, own_name, rival_id, rival_name):
        client = mock.Mock()
        client.fetch_season_scheduled_matches.return_value = (
            [{"id": 1, "homeTeamId": int(own_id), "awayTeamId": int(rival_id),
              "startDateTime": "2026-09-26T18:00:00Z", "matchStatus": "NOT_STARTED"}],
            {
                own_id: {"name": own_name, "logo_url": None, "acb_club_id": 3},
                rival_id: {"name": rival_name, "logo_url": None, "acb_club_id": 10},
            },
        )
        return client

    run_upcoming(engine, season=2025, client=fake_client("4425", "Kosner Baskonia", "4414", "Viejo Manresa"))
    summary = run_upcoming(
        engine, season=2026, client=fake_client("4463", "Otro Sponsor Baskonia", "4471", "Nuevo Manresa")
    )

    assert summary["upcoming"] == 1
    with engine.connect() as conn:
        assert _team(conn, "bas").acb_club_id == 3
        rivals = conn.execute(text("SELECT id FROM teams WHERE acb_club_id = 10")).scalars().all()
    assert len(rivals) == 1


# --- 2. Emparejador difuso: solo sugerencias ----------------------------------


def test_suggests_a_sponsor_rename_in_different_seasons(engine):
    with engine.begin() as conn:
        _add_team(conn, "viejo-manres", "Patrocinador Viejo Manresa")
        _add_team(conn, "nuevo-manres", "Patrocinador Nuevo Manresa")
        _add_game(conn, "viejo-manres", "rm", season="2024-2025")
        _add_game(conn, "nuevo-manres", "rm", season="2025-2026")
        suggestions = find_team_identity_suggestions(conn)

    manresa = [s for s in suggestions if set(s["team_ids"]) == {"viejo-manres", "nuevo-manres"}]
    assert len(manresa) == 1
    assert "manresa" in manresa[0]["shared_words"]
    assert manresa[0]["ambiguous"] is False


def test_suggestions_never_link_or_merge_anything(engine):
    with engine.begin() as conn:
        _add_team(conn, "viejo-manres", "Patrocinador Viejo Manresa")
        _add_team(conn, "nuevo-manres", "Patrocinador Nuevo Manresa")
        assert find_team_identity_suggestions(conn)
        assert find_team_identity_collisions(conn) == []
        assert fix_team_identity._plan(conn) == []
        ids = conn.execute(text("SELECT COUNT(*) FROM teams")).scalar_one()
    assert ids == 11  # 9 del seed + 2, nada fusionado


def test_same_city_clubs_playing_the_same_league_the_same_season_are_not_suggested(engine):
    """La trampa de fondo: dos clubes de Estambul comparten `istanbul`, pero
    jugaron la misma Euroliga la misma temporada — no pueden ser el mismo."""
    with engine.begin() as conn:
        _add_team(conn, "efes", "Anadolu Efes Istanbul")
        _add_team(conn, "fener", "Fenerbahce Beko Istanbul")
        _add_game(conn, "efes", "rm", season="2025-2026", competition="Euroliga")
        _add_game(conn, "fener", "bas", season="2025-2026", competition="Euroliga")
        suggestions = find_team_identity_suggestions(conn)
    assert suggestions == []


def test_two_teams_that_played_each_other_are_not_suggested(engine):
    with engine.begin() as conn:
        _add_team(conn, "maccabi", "Maccabi Playtika Tel Aviv")
        _add_team(conn, "hapoel", "Hapoel IBI Tel Aviv")
        _add_game(conn, "maccabi", "hapoel", season="2025-2026", competition="Liga Israel")
        suggestions = find_team_identity_suggestions(conn)
    assert suggestions == []


def test_different_acb_club_ids_or_euroleague_codes_rule_out_a_suggestion(engine):
    with engine.begin() as conn:
        _add_team(conn, "madrid-a", "Real Madrid Uno", acb_club_id=9)
        _add_team(conn, "madrid-b", "Estudiantes Madrid", acb_club_id=31)
        _add_team(conn, "belg-a", "Crvena Zvezda Belgrade")
        _add_team(conn, "belg-b", "Partizan Mozzart Belgrade")
        conn.execute(text(
            "INSERT INTO team_external_ids (team_id, source, external_id) VALUES"
            " ('belg-a', 'euroleague', 'RED'), ('belg-b', 'euroleague', 'PAR')"
        ))
        suggestions = find_team_identity_suggestions(conn)
    pairs = {frozenset(s["team_ids"]) for s in suggestions}
    assert frozenset({"madrid-a", "madrid-b"}) not in pairs
    assert frozenset({"belg-a", "belg-b"}) not in pairs


def test_a_new_row_that_could_be_either_of_two_same_city_clubs_is_ambiguous(engine):
    """Una fila nueva sin partidos que encaja con dos clubes que, entre sí, son
    distintos (jugaron la misma liga): se sugiere, pero marcado como ambiguo."""
    with engine.begin() as conn:
        _add_team(conn, "efes", "Anadolu Efes Istanbul")
        _add_team(conn, "fener", "Fenerbahce Beko Istanbul")
        _add_team(conn, "nuevo-ist", "Patrocinador Nuevo Istanbul")
        _add_game(conn, "efes", "rm", season="2025-2026", competition="Euroliga")
        _add_game(conn, "fener", "bas", season="2025-2026", competition="Euroliga")
        suggestions = find_team_identity_suggestions(conn)

    assert {frozenset(s["team_ids"]) for s in suggestions} == {
        frozenset({"efes", "nuevo-ist"}), frozenset({"fener", "nuevo-ist"}),
    }
    assert all(s["ambiguous"] for s in suggestions)


def test_three_sponsor_rows_of_one_club_are_not_ambiguous(engine):
    """Tres nombres de Lleida en tres temporadas: compatibles entre sí -> grupo, no ambigüedad."""
    with engine.begin() as conn:
        for i, (team_id, name) in enumerate(
            [("hio", "Hiopos Lleida"), ("ama", "Amara Lleida"), ("ile", "iLERNA Lleida Nuevo")]
        ):
            _add_team(conn, team_id, name + " X")  # " X": fuera de los alias reales
            _add_game(conn, team_id, "rm", season=f"202{3 + i}-202{4 + i}")
        suggestions = find_team_identity_suggestions(conn)
    assert len(suggestions) == 3
    assert not any(s["ambiguous"] for s in suggestions)


def test_generic_words_alone_are_not_a_signal(engine):
    with engine.begin() as conn:
        _add_team(conn, "betis", "Real Betis")  # comparte solo "real" con Real Madrid
        _add_team(conn, "fund-x", "Fundacion Club Uno")
        _add_team(conn, "fund-y", "Fundacion Club Dos")
        suggestions = find_team_identity_suggestions(conn)
    assert suggestions == []


def test_exact_collisions_are_not_repeated_as_suggestions(engine):
    with engine.begin() as conn:
        _add_team(conn, "kids", "Kids&Us Manresa")  # alias -> igual que `baxi`
        suggestions = find_team_identity_suggestions(conn)
        assert ("baxi", "kids") in find_team_identity_collisions(conn)
    assert all(set(s["team_ids"]) != {"baxi", "kids"} for s in suggestions)


# --- 3. Fusión exacta por acb_club_id ----------------------------------------


def test_fix_tool_groups_rows_sharing_an_acb_club_id_even_without_alias(engine):
    with engine.begin() as conn:
        _add_team(conn, "viejo-manres", "Patrocinador Viejo Manresa", acb_club_id=10)
        _add_team(conn, "nuevo-manres", "Patrocinador Nuevo Manresa", acb_club_id=10)
        _add_game(conn, "viejo-manres", "rm")

    outcome = fix_team_identity.merge_exact_duplicates(engine)

    assert outcome["merged"] == [("viejo-manres", ["nuevo-manres"])]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM teams WHERE id = 'nuevo-manres'")).first() is None
        assert find_team_identity_collisions(conn) == []


def test_name_and_club_id_links_form_a_single_group(engine):
    """`baxi` ~ "Occident Manresa" por alias, y `baxi` ~ "Otro Manresa" por clubId: un grupo de tres."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE teams SET acb_club_id = 10 WHERE id = 'baxi'"))
        _add_team(conn, "occident", "Occident Manresa")
        _add_team(conn, "otro", "Otro Sponsor Manresa", acb_club_id=10)
        groups = fix_team_identity._collision_groups(conn)
    assert groups == [["baxi", "occident", "otro"]]


def test_survivor_inherits_the_club_id_of_an_absorbed_row(engine):
    with engine.begin() as conn:
        _add_team(conn, "kids", "Kids&Us Manresa", acb_club_id=10)  # alias de `baxi`
        _add_game(conn, "baxi", "rm")
    fix_team_identity.merge_exact_duplicates(engine)
    with engine.connect() as conn:
        assert _team(conn, "baxi").acb_club_id == 10


def test_a_group_where_acb_sees_two_clubs_is_blocked_and_the_rest_still_merge(engine):
    with engine.begin() as conn:
        conn.execute(text("UPDATE teams SET acb_club_id = 5 WHERE id = 'gc'"))
        _add_team(conn, "gc-otro", "Gran Canaria", acb_club_id=77)  # mismo nombre, otro club
        _add_team(conn, "kids", "Kids&Us Manresa")                  # duplicado bueno de `baxi`

    outcome = fix_team_identity.merge_exact_duplicates(engine)

    assert outcome["blocked"] == [["gc", "gc-otro"]]
    assert outcome["merged"] == [("baxi", ["kids"])]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM teams WHERE id IN ('gc', 'gc-otro')")).scalar_one() == 2
        assert conn.execute(text("SELECT 1 FROM teams WHERE id = 'kids'")).first() is None
