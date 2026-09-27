"""Tests de `tools/fix_team_identity.py`: fusionar un club partido en varias filas.

Es una migración de datos que BORRA filas de `teams`, así que lo que se prueba
aquí es sobre todo lo que no se puede permitir que se tuerza — las tres
trampas que invalidan el "borra la fila que tiene 0 partidos":

1. Una fila con 0 partidos no está vacía: puede tener plantilla entera
   (`bilbao` tenía 26 jugadores) y borrarla los deja huérfanos.
2. Puede tener el CALENDARIO FUTURO (`casademont-z-2`: 0 partidos jugados, 2
   `upcoming_matchups` de 2026-27) y borrarla rompe "Próximo rival".
3. El superviviente no es el primero ni el que no lleva sufijo: de
   `morabanc-and`, `-2` y `-3`, el de los 34 partidos es `morabanc-and-2`.

Y una cuarta que no es de borrado sino de fusión: los jugadores se MUEVEN, no
se cruzan por dorsal como en `fix_barca_identity.py` — aquí las filas
duplicadas son temporadas distintas del mismo club y el dorsal se reutiliza
entre temporadas (ver el docstring del módulo).

Todo sobre una BD en memoria con el esquema y el seed reales.
"""
import pytest
from sqlalchemy import text

from ingest.common.identity import find_team_identity_collisions
from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db
from tools import fix_team_identity


@pytest.fixture()
def engine():
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _add_team(conn, team_id, name, logo_url=None, is_own_team=0):
    conn.execute(
        text("INSERT INTO teams (id, name, is_own_team, logo_url) VALUES (:id, :name, :own, :logo)"),
        {"id": team_id, "name": name, "own": is_own_team, "logo": logo_url},
    )


def _add_game(conn, game_id, team_id, date="2026-02-01"):
    """Un partido del club contra el Real Madrid. La fecha importa: `games` tiene
    `UNIQUE (season_id, competition_id, game_date, home_team_id, away_team_id)`."""
    conn.execute(
        text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace)"
            " VALUES (:id, 1, 1, :team, 'rm', :date, 80, 70, 72.0)"
        ),
        {"id": game_id, "team": team_id, "date": date},
    )


def _add_player(conn, player_id, team_id, name, number):
    conn.execute(
        text(
            "INSERT INTO players (id, team_id, name, number, position)"
            " VALUES (:id, :team, :name, :number, 'Alero')"
        ),
        {"id": player_id, "team": team_id, "name": name, "number": number},
    )


def _merge(engine):
    with engine.connect() as conn:
        plan = fix_team_identity._plan(conn)
    with engine.begin() as conn:
        fix_team_identity._apply(conn, plan)
    return plan


def _team_ids(engine):
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT id FROM teams")).all()}


def test_the_seed_alone_has_nothing_to_merge(engine):
    with engine.connect() as conn:
        assert fix_team_identity._plan(conn) == []


def test_survivor_is_the_row_with_the_games_even_if_it_carries_a_numeric_suffix(engine):
    """Trampa 3, con el caso real: el MoraBanc con los partidos es `morabanc-and-2`."""
    with engine.begin() as conn:
        _add_team(conn, "morabanc-and", "MoraBanc Andorra Undercoverlab")
        _add_team(conn, "morabanc-and-2", "MoraBanc Andorra")
        _add_team(conn, "morabanc-and-3", "MoraBanc And")
        _add_game(conn, "gx", "morabanc-and-2")

    with engine.connect() as conn:
        plan = fix_team_identity._plan(conn)

    assert len(plan) == 1
    assert plan[0]["survivor"]["id"] == "morabanc-and-2"
    assert {loser["id"] for loser in plan[0]["losers"]} == {"morabanc-and", "morabanc-and-3"}


def test_players_of_an_absorbed_row_change_team_instead_of_disappearing(engine):
    """Trampa 1: 0 partidos no es 0 datos."""
    with engine.begin() as conn:
        _add_team(conn, "surne-bilbao", "Surne Bilbao")
        _add_team(conn, "bilbao", "Bilbao Basket")
        _add_game(conn, "gx", "surne-bilbao")
        _add_player(conn, "un-jugador", "bilbao", "Alguien Deahi", 7)

    _merge(engine)

    with engine.connect() as conn:
        team_id = conn.execute(
            text("SELECT team_id FROM players WHERE id = 'un-jugador'")
        ).scalar_one()
    assert team_id == "surne-bilbao"
    assert "bilbao" not in _team_ids(engine)


def test_two_players_sharing_a_number_are_not_fused_into_one(engine):
    """Las filas duplicadas son TEMPORADAS distintas del mismo club y el dorsal se
    reutiliza entre temporadas: cruzarlas por dorsal (como sí valía en
    `fix_barca_identity.py`, dos fuentes de la MISMA temporada) fundiría a dos
    personas en una fila — la corrupción que `find_merged_players` existe para
    detectar y que ya no se puede deshacer."""
    with engine.begin() as conn:
        _add_team(conn, "coviran-gran", "Coviran Granada")
        _add_team(conn, "stellantis-y", "Stellantis&You Granada")
        _add_game(conn, "gx", "coviran-gran")
        _add_player(conn, "jugador-viejo", "stellantis-y", "Jugador Deantes", 9)
        _add_player(conn, "jugador-nuevo", "coviran-gran", "Otro Distinto", 9)

    _merge(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, team_id, number FROM players WHERE number = 9 ORDER BY id")
        ).all()
    assert [(row.id, row.team_id) for row in rows] == [
        ("jugador-nuevo", "coviran-gran"),
        ("jugador-viejo", "coviran-gran"),
    ]


def test_the_future_calendar_of_an_absorbed_row_survives_the_merge(engine):
    """Trampa 2: `casademont-z-2` no tiene ni un partido jugado, pero sí los dos
    de 2026-27 que alimentan 'Próximo rival'."""
    with engine.begin() as conn:
        _add_team(conn, "casademont-z", "Casademont Zaragoza")
        _add_team(conn, "casademont-z-2", "Casademont Zgz")
        _add_game(conn, "gx", "casademont-z")
        conn.execute(
            text(
                "INSERT INTO upcoming_matchups (opponent_team_id, competition_id, match_date, is_home)"
                " VALUES ('casademont-z-2', 1, '2026-10-04', 1)"
            )
        )

    _merge(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT opponent_team_id FROM upcoming_matchups WHERE match_date = '2026-10-04'")
        ).all()
    assert [row.opponent_team_id for row in rows] == ["casademont-z"]


def test_the_survivor_adopts_the_logo_it_did_not_have(engine):
    """El escudo suele venir solo en la fila del patrocinador de este año (la del
    calendario futuro), no en la que acumula el histórico de partidos."""
    with engine.begin() as conn:
        _add_team(conn, "girona", "Bàsquet Girona")
        _add_team(conn, "fiatc-girona", "FIATC Girona", logo_url="https://acb/fiatc.png")
        _add_game(conn, "gx", "girona")

    _merge(engine)

    with engine.connect() as conn:
        logo = conn.execute(text("SELECT logo_url FROM teams WHERE id = 'girona'")).scalar_one()
    assert logo == "https://acb/fiatc.png"


def test_a_logo_the_survivor_already_had_is_kept(engine):
    with engine.begin() as conn:
        _add_team(conn, "girona", "Bàsquet Girona", logo_url="https://acb/girona.png")
        _add_team(conn, "fiatc-girona", "FIATC Girona", logo_url="https://acb/fiatc.png")
        _add_game(conn, "gx", "girona")

    _merge(engine)

    with engine.connect() as conn:
        logo = conn.execute(text("SELECT logo_url FROM teams WHERE id = 'girona'")).scalar_one()
    assert logo == "https://acb/girona.png"


def test_the_own_team_survives_even_with_fewer_games(engine):
    """Fusionar el Baskonia DENTRO de otra fila rompería todo lo que cuelga de
    'mi equipo' (`is_own_team`), por muchos partidos que tenga la otra."""
    with engine.begin() as conn:
        _add_team(conn, "bas-dup", "Saski Baskonia")
        _add_game(conn, "gx", "bas-dup", date="2026-02-01")
        _add_game(conn, "gy", "bas-dup", date="2026-02-08")

    plan = _merge(engine)

    assert plan[0]["survivor"]["id"] == "bas"
    assert "bas-dup" not in _team_ids(engine)
    with engine.connect() as conn:
        owners = conn.execute(
            text("SELECT home_team_id FROM games WHERE id IN ('gx', 'gy')")
        ).all()
    assert {row[0] for row in owners} == {"bas"}


def test_shots_of_the_same_club_split_across_two_ids_are_added_up(engine):
    """`game_zone_stats` tiene PK `(game_id, team_id, zone_id)` y hay partidos con
    tiros del club repartidos entre sus dos ids (un jugador dado de alta bajo la
    fila vieja). No es un choque de verdad: son tiros del mismo equipo, el mismo
    partido y la misma zona, así que se suman en una fila."""
    with engine.begin() as conn:
        _add_team(conn, "coviran-gran", "Coviran Granada")
        _add_team(conn, "stellantis-y", "Stellantis&You Granada")
        _add_game(conn, "gx", "coviran-gran")
        conn.execute(
            text(
                "INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume) VALUES"
                " ('gx', 'coviran-gran', 10, 100.0, 3),"   # 3 de 3
                " ('gx', 'stellantis-y', 10, 50.0, 2),"    # 1 de 2
                " ('gx', 'stellantis-y', 6, 25.0, 4)"      # sin choque: solo cambia de equipo
            )
        )

    _merge(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT team_id, zone_id, fg_pct, volume FROM game_zone_stats WHERE game_id = 'gx' ORDER BY zone_id")
        ).all()
    assert [(row.team_id, row.zone_id, row.fg_pct, row.volume) for row in rows] == [
        ("coviran-gran", 6, 25.0, 4),
        ("coviran-gran", 10, 80.0, 5),  # 4 de 5
    ]


def test_the_same_game_loaded_under_both_ids_is_reported_instead_of_crashing(engine):
    """`games` tiene `UNIQUE (season_id, competition_id, game_date, home_team_id,
    away_team_id)`: si el mismo partido estuviera cargado bajo los dos `teams.id`
    del club, la fusión chocaría con esa clave a mitad de camino. Se detecta antes
    y se dice qué partidos son."""
    with engine.begin() as conn:
        _add_team(conn, "hiopos-lleid", "Hiopos Lleida")
        _add_team(conn, "amara-lleida", "Amara Lleida")
        _add_game(conn, "gx", "hiopos-lleid", date="2026-02-01")
        _add_game(conn, "gy", "amara-lleida", date="2026-02-01")  # el mismo partido, otro id

    with engine.connect() as conn:
        plan = fix_team_identity._plan(conn)

    assert len(plan[0]["conflicts"]) == 1
    date, ids = plan[0]["conflicts"][0]
    assert (date, sorted(ids.split("+"))) == ("2026-02-01", ["gx", "gy"])


def test_after_the_merge_the_detector_is_quiet_and_no_game_is_lost(engine):
    with engine.begin() as conn:
        _add_team(conn, "hiopos-lleid", "Hiopos Lleida")
        _add_team(conn, "amara-lleida", "Amara Lleida")
        _add_team(conn, "ilerna-lleid", "iLERNA Lleida")
        _add_game(conn, "gx", "hiopos-lleid", date="2026-02-01")
        _add_game(conn, "gy", "hiopos-lleid", date="2026-02-08")
        _add_game(conn, "gz", "amara-lleida", date="2026-02-15")

    with engine.connect() as conn:
        games_before = conn.execute(text("SELECT COUNT(*) FROM games")).scalar_one()

    _merge(engine)

    with engine.connect() as conn:
        assert find_team_identity_collisions(conn) == []
        assert conn.execute(text("SELECT COUNT(*) FROM games")).scalar_one() == games_before
        assert conn.execute(
            text("SELECT COUNT(*) FROM games WHERE home_team_id = 'hiopos-lleid'")
        ).scalar_one() == 3
