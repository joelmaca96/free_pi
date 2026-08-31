"""Tests de las consultas de zona de la propuesta 08 (`app/data/queries.py`).

Usan una temporada NUEVA (`season_id=2`) para no arrastrar los recuentos de
`game_zone_stats` que ya trae el seed de `schema.sql` en la temporada 1 (todos
del Baskonia) y poder verificar los números a mano.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import (
    league_zone_baseline_counts,
    team_games_played,
    team_zone_profile,
    team_zone_profile_by_competition,
)


@pytest.fixture()
def engine():
    """BD de scouting en memoria con el esquema + seed reales (`bas`/`rm`/`fcb`... ya existen como equipos)."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_two_games(conn) -> None:
    """Temporada 2: `bas` de local vs `rm` en ACB, y de visitante vs `rm` en Euroliga."""
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (2, '2026-2027')"))
    conn.execute(text(
        "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
        " game_date, home_score, away_score, pace) VALUES"
        " ('gx1', 2, 1, 'bas', 'rm', '2026-10-01', 80, 75, 70.0)"
    ))
    conn.execute(text(
        "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
        " game_date, home_score, away_score, pace) VALUES"
        " ('gx2', 2, 2, 'rm', 'bas', '2026-10-08', 78, 82, 72.0)"
    ))
    conn.execute(text(
        "INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume) VALUES"
        " ('gx1', 'bas', 1, 60, 50),"   # ataque de bas, Pintura, partido ACB
        " ('gx1', 'rm',  1, 40, 30),"   # ataque de rm = lo que concede bas, mismo partido
        " ('gx2', 'bas', 1, 50, 20),"   # ataque de bas, Pintura, partido Euroliga
        " ('gx2', 'rm',  1, 45, 25)"    # ataque de rm = lo que concede bas, mismo partido
    ))


def test_offensive_side_is_unchanged_and_is_the_default(engine):
    """Comportamiento por defecto (sin pasar `side`) idéntico al de antes de la propuesta 08:
    ningún llamador existente debe notar el cambio."""
    with engine.begin() as conn:
        _seed_two_games(conn)

    default_side = team_zone_profile.__wrapped__(engine, "bas", 2)
    explicit_offensive = team_zone_profile.__wrapped__(engine, "bas", 2, side="offensive")

    compared_cols = ["zone_id", "zone_label", "fg_pct", "volume", "made"]
    assert default_side[compared_cols].equals(explicit_offensive[compared_cols])
    row = default_side.loc[default_side["zone_id"] == 1].iloc[0]
    assert row["volume"] == 70  # 50 (gx1) + 20 (gx2), los tiros DE bas
    assert row["fg_pct"] == pytest.approx((60 * 50 + 50 * 20) / 70)


def test_defensive_side_is_the_opponents_shots_in_the_teams_own_games(engine):
    """Lo que concede `bas`: los tiros de `rm` en esos DOS mismos partidos, no los de `rm` en general."""
    with engine.begin() as conn:
        _seed_two_games(conn)
        # Un tercer partido de `rm` SIN `bas` no debe contar como "concedido por bas".
        conn.execute(text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace) VALUES"
            " ('gx3', 2, 1, 'rm', 'fcb', '2026-10-15', 90, 88, 71.0)"
        ))
        conn.execute(text(
            "INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume) VALUES"
            " ('gx3', 'rm', 1, 99, 40)"
        ))

    profile = team_zone_profile.__wrapped__(engine, "bas", 2, side="defensive")

    row = profile.loc[profile["zone_id"] == 1].iloc[0]
    assert row["volume"] == 55  # 30 (gx1) + 25 (gx2) de rm, SIN el gx3 (bas no jugó ese)
    assert row["fg_pct"] == pytest.approx((40 * 30 + 45 * 25) / 55)


def test_unknown_side_raises_a_clear_error(engine):
    with pytest.raises(ValueError, match="side"):
        team_zone_profile.__wrapped__(engine, "bas", 2, side="ambos")


def test_by_competition_keeps_each_competition_apart(engine):
    with engine.begin() as conn:
        _seed_two_games(conn)

    offense = team_zone_profile_by_competition.__wrapped__(engine, "bas", 2, side="offensive")
    defense = team_zone_profile_by_competition.__wrapped__(engine, "bas", 2, side="defensive")

    offense_by_comp = offense.set_index("competition_id")["volume"]
    assert offense_by_comp[1] == 50   # ACB (gx1)
    assert offense_by_comp[2] == 20   # Euroliga (gx2)
    defense_by_comp = defense.set_index("competition_id")["volume"]
    assert defense_by_comp[1] == 30
    assert defense_by_comp[2] == 25


def test_league_baseline_counts_sum_both_teams_of_every_game(engine):
    """La referencia de liga no filtra por equipo: cada partido aporta las filas de LOS DOS."""
    with engine.begin() as conn:
        _seed_two_games(conn)

    league = league_zone_baseline_counts.__wrapped__(engine, 2)

    acb_paint = league.loc[(league["competition_id"] == 1) & (league["zone_id"] == 1)].iloc[0]
    assert acb_paint["volume"] == 80  # bas (50) + rm (30) en gx1
    assert acb_paint["fg_pct"] == pytest.approx((60 * 50 + 40 * 30) / 80)


def test_games_played_counts_home_and_away_but_not_a_team_that_did_not_play(engine):
    with engine.begin() as conn:
        _seed_two_games(conn)

    assert team_games_played.__wrapped__(engine, "bas", 2) == 2
    assert team_games_played.__wrapped__(engine, "rm", 2) == 2
    assert team_games_played.__wrapped__(engine, "fcb", 2) == 0
