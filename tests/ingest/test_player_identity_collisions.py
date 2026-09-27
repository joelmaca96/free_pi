"""Tests de `ingest.common.identity.find_player_identity_collisions`: detectar un
jugador creado como dos `players.id` distintos porque cada fuente lo ingirió con
un `team_id` distinto (fichaje reciente, o la fuente sin actualizar) — el bug
real que tuvo Jabari Parker (`jabari-parke` desde ACB, `parker-jabar` desde
Euroliga), que aparecía dos veces en el mismo timeline de rotaciones
(`app/components/rotation_chart.py`, una fila con tramos y otra solo con
faltas) antes de que `tools/fix_player_identity.py` los fusionara.
"""
from sqlalchemy import text

from ingest.common.identity import find_player_identity_collisions


def test_no_collisions_in_the_seed(engine):
    """El seed de `schema.sql` no tiene ningún jugador duplicado."""
    with engine.connect() as conn:
        assert find_player_identity_collisions(conn) == []


def test_detects_two_players_with_the_same_normalized_name_on_different_teams(engine):
    """El seed ya trae 'moneke' ('Chima Moneke', team_id 'bas'): el duplicado real
    era justo un segundo `player_id` para el mismo jugador, bajo OTRO `team_id`
    (la fuente que lo ingirió aún no sabía que había fichado por el Baskonia)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('moneke-dup', 'rm', 'Chima Moneke', 95, 'Ala-pívot')"
            )
        )

    with engine.connect() as conn:
        collisions = find_player_identity_collisions(conn)
    assert collisions == [("moneke", "moneke-dup")]


def test_does_not_flag_players_with_genuinely_different_names(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('otro', 'bas', 'Otro Jugador', 99, 'Base')"
            )
        )

    with engine.connect() as conn:
        collisions = find_player_identity_collisions(conn)
    assert collisions == []


# --- homónimos ya revisados a mano ------------------------------------------


def test_a_pair_reviewed_as_two_different_people_is_not_reported(engine):
    """Hay DOS Jaime Fernández españoles en ACB a la vez: el escolta de Madrid
    (1993, 1,86 m, licencia 20204124, La Laguna Tenerife) y el ala-pívot de
    Zaragoza (2000, 2,06 m, licencia 20212348, Casademont) — comprobado en
    acb.com. Un homónimo real no se arregla, así que si se siguiera avisando de
    él en cada ingesta el aviso se volvería ruido."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position) VALUES"
                " ('jaime-fernan', 'rm', 'Jaime Fernández', 3, 'Escolta'),"
                " ('jaime-fernan-2', 'fcb', 'Jaime Fernández', 10, 'Ala-pívot')"
            )
        )

    with engine.connect() as conn:
        assert find_player_identity_collisions(conn) == []


def test_another_pair_with_the_same_name_is_still_reported(engine):
    """La excepción es ese par concreto, no el nombre: un tercer 'Jaime Fernández'
    vuelve a ser una colisión que hay que mirar."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position) VALUES"
                " ('jaime-fernan', 'rm', 'Jaime Fernández', 3, 'Escolta'),"
                " ('jaime-fernan-3', 'fcb', 'Jaime Fernández', 44, 'Alero')"
            )
        )

    with engine.connect() as conn:
        assert find_player_identity_collisions(conn) == [("jaime-fernan", "jaime-fernan-3")]


def test_every_reviewed_pair_is_stored_sorted():
    """Los pares se comparan contra `tuple(sorted(...))`: uno escrito al revés no
    casaría con nada y la excepción no haría nada, en silencio."""
    from ingest.common.identity import _REVIEWED_DISTINCT_PLAYERS

    for pair in _REVIEWED_DISTINCT_PLAYERS:
        assert pair == tuple(sorted(pair)), pair
        assert len(pair) == 2


# --- filas fusionadas (el fallo contrario, y peor) --------------------------


def test_find_merged_players_is_quiet_on_a_clean_database(engine):
    from ingest.common.identity import find_merged_players

    with engine.connect() as conn:
        assert find_merged_players(conn) == []


def test_find_merged_players_spots_a_row_renamed_to_another_person(engine):
    """`players.id` es un slug del nombre CON EL QUE SE CREÓ la fila y no se reescribe nunca:
    es el único rastro del primer ocupante. Si no comparte apellido con el nombre actual, la
    fila se renombró a otra persona — el caso real de `alberto-abal` llamándose 'Gunars
    Grinvalds' con siete licencias de ACB y 79 partidos encima."""
    from ingest.common.identity import find_merged_players

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('alberto-abal', 'rm', 'Gunars Grinvalds', 33, 'Alero')"
            )
        )
    with engine.connect() as conn:
        assert find_merged_players(conn) == [("alberto-abal", "Gunars Grinvalds")]


def test_a_truncated_or_reordered_slug_is_not_a_merge(engine):
    """Dos falsos positivos que hay que descartar o el aviso se vuelve ruido: `_slugify` corta
    a 12 caracteres ('timothe-luwa' de 'Timothé Luwawu-Cabarrot'), y Euroliga da los nombres
    con el apellido delante ('LARKIN, SHANE' -> slug `larkin-shane`, luego renombrado a
    'Shane Larkin')."""
    from ingest.common.identity import find_merged_players

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position) VALUES"
                " ('timothe-luwa', 'bas', 'Timothé Luwawu-Cabarrot', 9, 'Alero'),"
                " ('larkin-shane', 'rm', 'Shane Larkin', 0, 'Base')"
            )
        )
    with engine.connect() as conn:
        assert find_merged_players(conn) == []


def test_names_too_short_to_have_a_surname_are_skipped(engine):
    """'A. De' deja el slug `a-de`: ahí no hay apellido que comparar, así que no se puede
    afirmar nada. Contarlo como fusión sería inventarse una alarma."""
    from ingest.common.identity import find_merged_players

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO players (id, team_id, name, number, position) VALUES ('a-de', 'rm', 'A. De', 42, '')")
        )
    with engine.connect() as conn:
        assert find_merged_players(conn) == []
