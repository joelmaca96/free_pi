"""Tests de `ingest.common.identity.find_team_identity_collisions` (§5 de
`doc/features/propuestas/04_fatiga_y_calendario.md`): detectar un club creado
como dos `teams.id` distintos porque cada fuente lo nombró distinto — el bug
real que tuvo el Barça (`barca` desde ACB, `fcb` desde Euroliga) antes de que
`tools/fix_barca_identity.py` los fusionara.
"""
from sqlalchemy import text

from ingest.common.identity import find_team_identity_collisions


def test_no_collisions_in_the_seed(engine):
    """El seed de `schema.sql` no tiene ningún club duplicado."""
    with engine.connect() as conn:
        assert find_team_identity_collisions(conn) == []


def test_detects_two_teams_with_the_same_normalized_name(engine):
    """El seed ya trae 'fcb' ('FC Barcelona'): el duplicado real era justo un
    segundo `team_id` para el mismo club — aquí, 'barca-dup' ('Barça')."""
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO teams (id, name, is_own_team) VALUES ('barca-dup', 'Barça', 0)"))

    with engine.connect() as conn:
        collisions = find_team_identity_collisions(conn)
    assert collisions == [("barca-dup", "fcb")]


def test_does_not_flag_teams_with_genuinely_different_names(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO teams (id, name, is_own_team) VALUES ('lele', 'Lenovo Tenerife', 0)"))

    with engine.connect() as conn:
        collisions = find_team_identity_collisions(conn)
    assert collisions == []
