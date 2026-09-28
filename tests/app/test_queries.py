"""Tests de `app/data/queries.py`: por ahora, solo `team_scouting_season`.

Es la pieza que decide de qué temporada sale el scouting de un rival en
"Próximo rival" (`app/screens/proximo_rival.py`) cuando la temporada
seleccionada todavía no tiene partidos suyos — cae a la última temporada con
datos, sin mirar nunca hacia delante, y lo señala (`is_fallback`). Caso real
documentado en la propia función: el Baskonia arranca 2026-2027 contra el
Olympiacos, que no ha jugado nada en esa temporada todavía pero sí 43
partidos en 2025-2026.
"""
import pytest
from sqlalchemy import text

from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

from app.data.queries import game_player_report, team_scouting_season


@pytest.fixture()
def engine():
    """BD de scouting en memoria con el esquema + seed reales (temporada 2025-2026,
    `season_id=1`, con partidos de 'rm' pero ninguno de 'baxi'/'jb' — ver seed de
    `schema.sql`)."""
    eng = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _add_season(conn, season_id: int, label: str) -> None:
    conn.execute(text("INSERT INTO seasons (id, label) VALUES (:id, :label)"), {"id": season_id, "label": label})


def _add_game(conn, game_id: str, season_id: int, home: str, away: str) -> None:
    conn.execute(
        text(
            "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
            " game_date, home_score, away_score, pace)"
            " VALUES (:id, :season_id, 1, :home, :away, '2026-01-01', 80, 75, 70.0)"
        ),
        {"id": game_id, "season_id": season_id, "home": home, "away": away},
    )


def test_returns_preferred_season_directly_when_it_already_has_games(engine):
    """'rm' tiene partidos en la temporada 1 (seed, `g1`) — la propia temporada
    pedida ya sirve, sin caer a ninguna otra."""
    result = team_scouting_season.__wrapped__(engine, "rm", 1)
    assert result == {"season_id": 1, "label": "2025-2026", "is_fallback": False}


def test_falls_back_to_the_last_season_with_data_and_flags_it(engine):
    """Caso real que motivó la función: el rival no ha jugado nada en la temporada
    seleccionada (2, nueva) pero sí en la anterior (1, con `g1` del seed)."""
    with engine.begin() as conn:
        _add_season(conn, 2, "2026-2027")

    result = team_scouting_season.__wrapped__(engine, "rm", 2)

    assert result == {"season_id": 1, "label": "2025-2026", "is_fallback": True}


def test_never_looks_forward_to_a_later_season(engine):
    """'jb' no tiene partidos en la temporada 1 (seed) pero sí en la 2 (futura,
    añadida aquí) — pedir la temporada 1 NO debe devolver la 2: no es una
    aproximación razonable, sería otra cosa (ver docstring de la función)."""
    with engine.begin() as conn:
        _add_season(conn, 2, "2026-2027")
        _add_game(conn, "g-future", 2, "jb", "bas")

    result = team_scouting_season.__wrapped__(engine, "jb", 1)

    assert result is None


def test_returns_none_when_the_team_has_no_games_in_any_season_up_to_preferred(engine):
    """'baxi' no tiene ningún partido en el seed — ni la propia temporada pedida
    ni ninguna anterior sirven; no hay nada a lo que caer."""
    result = team_scouting_season.__wrapped__(engine, "baxi", 1)

    assert result is None


def test_falls_back_across_more_than_one_season_gap(engine):
    """El hueco puede ser de más de una temporada (rival con calendario futuro
    cargado pero sin jugar nada en dos temporadas seguidas) — sigue cayendo a la
    última con datos, no a la inmediatamente anterior porque sí."""
    with engine.begin() as conn:
        _add_season(conn, 2, "2026-2027")
        _add_season(conn, 3, "2027-2028")

    result = team_scouting_season.__wrapped__(engine, "rm", 3)

    assert result == {"season_id": 1, "label": "2025-2026", "is_fallback": True}


# ------------------------------------------------------- game_player_report --
# Alimenta el informe "PPT para Paolo" (`app/reports/postgame_ppt.py`): boxscore
# ampliado de un partido + triples derivados de `shots`. El seed ya trae la
# plantilla del Baskonia (`players`, team_id='bas') y las 13 zonas de cancha
# (`court_zones`, con cinco que empiezan por "Triple") — solo hace falta el
# partido y las filas de estadística propias de cada test.


def _add_player(conn, player_id: str, team_id: str, name: str) -> None:
    conn.execute(
        text("INSERT INTO players (id, team_id, name, number, position) VALUES (:id, :team_id, :name, 99, 'Base')"),
        {"id": player_id, "team_id": team_id, "name": name},
    )


def _add_player_game_stats(conn, game_id: str, player_id: str, **overrides) -> None:
    base = {
        "game_id": game_id, "player_id": player_id, "minutes": 20.0, "pts": 10, "reb": 4, "ast": 3,
        "efg_pct": 50.0, "ftm": None, "fta": None, "stl": None, "tov": None, "blk": None,
        "blk_against": None, "pf": None, "pf_drawn": None, "oreb": None, "dreb": None,
        "plus_minus": None, "pir": None, "dunks": None,
    }
    base.update(overrides)
    conn.execute(
        text(
            "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct,"
            " ftm, fta, stl, tov, blk, blk_against, pf, pf_drawn, oreb, dreb, plus_minus, pir, dunks)"
            " VALUES (:game_id, :player_id, :minutes, :pts, :reb, :ast, :efg_pct,"
            " :ftm, :fta, :stl, :tov, :blk, :blk_against, :pf, :pf_drawn, :oreb, :dreb, :plus_minus, :pir, :dunks)"
        ),
        base,
    )


def _add_shot(conn, game_id: str, player_id: str, zone_id: int, made: int) -> None:
    conn.execute(
        text(
            "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located)"
            " VALUES (:game_id, :player_id, :zone_id, 250.0, 80.0, :made, 1)"
        ),
        {"game_id": game_id, "player_id": player_id, "zone_id": zone_id, "made": made},
    )


def test_game_player_report_includes_threes_and_free_throws(engine):
    """Triples derivados de `shots` (zonas 'Triple*', ver seed de `court_zones`)
    + tiros libres ya presentes en `player_game_stats`, unidos en una fila."""
    with engine.begin() as conn:
        _add_game(conn, "g-report", 1, "bas", "rm")
        _add_player_game_stats(conn, "g-report", "howard", pts=21, ftm=3, fta=4)
        _add_shot(conn, "g-report", "howard", zone_id=6, made=1)  # Triple exterior, anotado
        _add_shot(conn, "g-report", "howard", zone_id=6, made=0)  # Triple exterior, fallado
        _add_shot(conn, "g-report", "howard", zone_id=1, made=1)  # Pintura: no es un triple

    df = game_player_report.__wrapped__(engine, "g-report", "bas")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["player_id"] == "howard"
    assert row["tpm"] == 1
    assert row["tpa"] == 2
    assert row["ftm"] == 3 and row["fta"] == 4


def test_game_player_report_excludes_dnp_and_other_team(engine):
    """Un convocado sin minutos jugados no aporta nada a un informe de puntos
    destacados (mismo criterio que 'Carga de minutos' en `estado_equipo.py`,
    que también lo descarta) y un jugador del rival no es del Baskonia aunque
    juegue el mismo partido."""
    with engine.begin() as conn:
        _add_player(conn, "rival-x", "rm", "Jugador Rival")
        _add_game(conn, "g-report-2", 1, "bas", "rm")
        _add_player_game_stats(conn, "g-report-2", "howard", minutes=0.0, pts=0)
        _add_player_game_stats(conn, "g-report-2", "moneke", minutes=18.0, pts=8)
        _add_player_game_stats(conn, "g-report-2", "rival-x", minutes=25.0, pts=15)

    df_bas = game_player_report.__wrapped__(engine, "g-report-2", "bas")

    assert df_bas["player_id"].tolist() == ["moneke"]
    # NULL de SQL == NaN en pandas para una columna sin dato: no hay tiros
    # con coordenadas en este partido, no que no lanzara ningún triple.
    assert df_bas["tpa"].isna().all()


# --- Filtro de contexto del mapa de tiros y tiempos muertos (2026-09-28) ----


def _add_context_shot(conn, zone_id, made, seconds, home, away, fastbreak=0, second=0, off_to=0, player="howard"):
    conn.execute(
        text(
            "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located, quarter, game_clock,"
            " seconds, home_score, away_score, is_fastbreak, is_second_chance, is_off_turnover)"
            " VALUES ('g5', :player, :zone_id, 250.0, 400.0, :made, 1, 'Q4', '00:00', :seconds, :home, :away,"
            " :fastbreak, :second, :off_to)"
        ),
        {"player": player, "zone_id": zone_id, "made": made, "seconds": seconds, "home": home, "away": away,
         "fastbreak": fastbreak, "second": second, "off_to": off_to},
    )


def _context_fixture(engine):
    with engine.begin() as conn:
        _add_context_shot(conn, 1, 1, 600.0, 20, 18, fastbreak=1)            # Q2, contraataque
        _add_context_shot(conn, 1, 0, 2200.0, 70, 67, second=1)              # último 5' apretado
        _add_context_shot(conn, 2, 1, 2300.0, 80, 70, off_to=1)              # último 5' pero +10
        _add_context_shot(conn, 1, 1, 2450.0, 88, 90, player="moneke")       # prórroga apretada
        # Tiro viejo sin reloj ni banderas: solo sale con "Todos".
        conn.execute(text(
            "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made) VALUES ('g5', 'howard', 1, 250, 400, 0)"
        ))


def test_team_shots_season_context_filters(engine):
    from app.data.queries import team_shots_season

    _context_fixture(engine)
    run = team_shots_season.__wrapped__
    assert len(run(engine, "bas", 1)) == 5
    assert len(run(engine, "bas", 1, "clutch")) == 2  # 2200 (+3) y la prórroga (-2); no el +10
    assert len(run(engine, "bas", 1, "fastbreak")) == 1
    assert len(run(engine, "bas", 1, "second_chance")) == 1
    assert len(run(engine, "bas", 1, "off_turnover")) == 1
    # El SQL se interpola: solo claves de la lista cerrada.
    with pytest.raises(ValueError):
        run(engine, "bas", 1, "made = 1 OR 1")


def test_shot_zone_profile_in_context_matches_the_filtered_shots(engine):
    from app.data.queries import shot_zone_profile_in_context

    _context_fixture(engine)
    team = shot_zone_profile_in_context.__wrapped__(engine, "bas", 1, "clutch")
    assert (int(team["volume"].sum()), int(team["made"].sum())) == (2, 1)
    assert float(team["fg_pct"].iloc[0]) == 50.0
    howard = shot_zone_profile_in_context.__wrapped__(engine, "bas", 1, "clutch", "howard")
    assert int(howard["volume"].sum()) == 1 and float(howard["fg_pct"].iloc[0]) == 0.0


def test_game_timeouts_reads_only_team_timeouts_in_order(engine):
    from app.data.queries import game_timeouts

    with engine.begin() as conn:
        for seconds, event_type, team in ((900.0, "timeout", "val"), (300.0, "timeout", "bas"), (400.0, "steal", "bas")):
            conn.execute(
                text(
                    "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                    " event_type, home_score, away_score) VALUES ('g5', :team, NULL, 'Q1', '05:00', :seconds,"
                    " :event_type, 10, 8)"
                ),
                {"team": team, "seconds": seconds, "event_type": event_type},
            )
    timeouts = game_timeouts.__wrapped__(engine, "g5")
    assert timeouts["team_id"].tolist() == ["bas", "val"]
    assert game_timeouts.__wrapped__(engine, "g1").empty
