"""Tests de la fuente ACB: transform-and-load con un partido de ejemplo (fixture)."""
import copy
from unittest import mock

from sqlalchemy import text

from ingest.acb.parser import parse_and_resolve
from ingest.acb.pipeline import discover_missing_games, run, run_single_game, run_upcoming
from ingest.common.loader import load_game
from ingest.common.zones import MATE_ZONE_ID

RAW_GAME = {
    "game_id": "2025123401",
    "date": "2026-02-14",
    "season": 2025,
    "competition": "ACB",
    "home_team": {"id": "acb-bas", "name": "Kosner Baskonia"},
    "away_team": {"id": "acb-uni", "name": "Unicaja"},
    "home_score": 90,
    "away_score": 85,
    "pace": 73.0,
    "narrative": "Partido de ejemplo (fixture ACB).",
    "team_stats": [
        {"team_id": "acb-bas", "efg_pct": 56.0, "ts_pct": 59.0, "tov_pct": 11.0, "orb_pct": 26.0, "ortg": 114.0, "drtg": 109.0},
        {"team_id": "acb-uni", "efg_pct": 52.0, "ts_pct": 54.0, "tov_pct": 13.0, "orb_pct": 22.0, "ortg": 109.0, "drtg": 114.0},
    ],
    "players": [
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "name": "Marcus Howard", "number": 0, "position": "Base",
         "minutes": 30.0, "pts": 22, "reb": 3, "ast": 6, "efg_pct": 60.0},
        {"player_id": "acb-p-rival1", "team_id": "acb-uni", "name": "Jugador Rival", "number": 5, "position": "Alero",
         "minutes": 28.0, "pts": 15, "reb": 4, "ast": 2, "efg_pct": 48.0,
         "photo_url": "https://static.acb.com/media/rival1.jpg"},
    ],
    "lineups": [
        {"team_id": "acb-bas", "player_ids": ["acb-p-howard"], "minutes": 5.0, "plus_minus": 3},
    ],
    "shots": [
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "x": 250.0, "y": 480.0, "made": True},
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "x": 260.0, "y": 460.0, "made": False},
    ],
    "events": [
        {"team_id": "acb-bas", "quarter": "Q4", "clock": "00:40", "label": "Triple de Howard"},
    ],
    "score_progression": [
        {"step": 0, "home": 0, "away": 0},
        {"step": 1, "home": 3, "away": 0},
    ],
}


def test_parse_and_resolve_reuses_baskonia_team_and_player(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)

    assert game.id == "acb-2025123401"
    assert game.home_team_id == "bas"  # normaliza "Kosner Baskonia" -> equipo ya existente
    assert game.boxscore[0].player_id == "howard"  # matchea por team_id+dorsal


def test_acb_transform_and_load_produces_coherent_rows(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT home_score, away_score FROM games WHERE id = 'acb-2025123401'")).first()
        assert row == (90, 85)
        pts = conn.execute(
            text("SELECT pts FROM player_game_stats WHERE game_id='acb-2025123401' AND player_id='howard'")
        ).scalar_one()
        assert pts == 22
        # net_rating derivado de ortg-drtg (ambos presentes en el fixture)
        net_rating = conn.execute(
            text("SELECT net_rating FROM game_advanced_stats WHERE game_id='acb-2025123401' AND team_id='bas'")
        ).scalar_one()
        assert net_rating == 5.0


def test_acb_transform_and_load_is_idempotent(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM games WHERE id='acb-2025123401'")).scalar_one() == 1
        assert conn.execute(
            text("SELECT COUNT(*) FROM player_game_stats WHERE game_id='acb-2025123401'")
        ).scalar_one() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM shots WHERE game_id='acb-2025123401'")).scalar_one() == 2
        # el rival ("Unicaja") no se duplica al re-ejecutar. `scalar_one()` ya
        # falla solo si "acb-uni" quedara enlazado a mas de un equipo; no se
        # comprueba `teams.name` porque el fixture semilla "Unicaja Málaga"
        # (mismo club, `_KNOWN_TEAM_ALIASES` lo funde ahí en vez de crear una
        # fila nueva "Unicaja" - ver `packages/baskonia_core/names.py`).
        conn.execute(
            text("SELECT team_id FROM team_external_ids WHERE source='acb' AND external_id='acb-uni'")
        ).scalar_one()


def test_acb_boxscore_photo_fills_in_for_a_rival_without_one(engine):
    """`headshotImageUrl` del boxscore rellena `photo_url` de un rival sin foto (§ fotos de rivales)."""
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME)
        load_game(conn, game)

    with engine.connect() as conn:
        rival_id = conn.execute(
            text("SELECT player_id FROM player_external_ids WHERE source='acb' AND external_id='acb-p-rival1'")
        ).scalar_one()
        photo_url = conn.execute(text("SELECT photo_url FROM players WHERE id = :id"), {"id": rival_id}).scalar_one()
        assert photo_url == "https://static.acb.com/media/rival1.jpg"


def test_acb_boxscore_photo_never_overwrites_an_existing_one(engine):
    """La foto de acb.com RELLENA, nunca pisa la ya puesta por `ingest/baskonia_web`."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE players SET photo_url = 'https://baskonia.com/howard-oficial.jpg' WHERE id = 'howard'"))

    raw_game = copy.deepcopy(RAW_GAME)
    raw_game["players"][0]["photo_url"] = "https://static.acb.com/media/howard-distinta.jpg"

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw_game)
        load_game(conn, game)

    with engine.connect() as conn:
        photo_url = conn.execute(text("SELECT photo_url FROM players WHERE id = 'howard'")).scalar_one()
        assert photo_url == "https://baskonia.com/howard-oficial.jpg"


def test_dunks_get_the_mate_zone_directly_not_through_geometry(engine):
    """Un tiro `located=False` (mate sin coordenadas medidas, ver `adapter.py`) recibe
    `MATE_ZONE_ID` directamente, sin pasar por `classify_zone` — su centinela reescalado
    (250, 455) cae DENTRO de 'Pintura', así que si pasara por geometría se clasificaría
    ahí (zona 1), no como mate (ver `ingest/common/raw_game.py`)."""
    raw_game = copy.deepcopy(RAW_GAME)
    raw_game["shots"].append(
        {"player_id": "acb-p-howard", "team_id": "acb-bas", "x": 250.0, "y": 455.0, "made": True, "located": False}
    )

    with engine.begin() as conn:
        game = parse_and_resolve(conn, raw_game)

    dunk = game.shots[-1]
    assert dunk.located is False
    assert dunk.zone_id == MATE_ZONE_ID


RAW_GAME_WITH_PBP = {
    "game_id": "2025999999",
    "date": "2026-02-20",
    "season": 2025,
    "competition": "ACB",
    "home_team": {"id": "acb-bas", "name": "Kosner Baskonia"},
    "away_team": {"id": "acb-uni", "name": "Unicaja"},
    "home_score": 4,
    "away_score": 0,
    "pace": 70.0,
    "players": [
        {"player_id": f"acb-h{i}", "team_id": "acb-bas", "name": f"Local {i}", "number": 60 + i,
         "minutes": 40.0, "pts": 0, "reb": 0, "ast": 0, "efg_pct": 0.0, "starter": True}
        for i in range(1, 6)
    ] + [
        {"player_id": f"acb-a{i}", "team_id": "acb-uni", "name": f"Visitante {i}", "number": 70 + i,
         "minutes": 40.0, "pts": 0, "reb": 0, "ast": 0, "efg_pct": 0.0, "starter": True}
        for i in range(1, 6)
    ],
    "shots": [],
    "play_by_play": [
        {"team_id": "acb-bas", "type": "score", "quarter": "Q1", "clock": "08:00", "points": 4},
    ],
}


def test_acb_parse_and_resolve_reconstructs_lineups_from_play_by_play(engine):
    with engine.begin() as conn:
        game = parse_and_resolve(conn, RAW_GAME_WITH_PBP)

    assert len(game.lineups) == 2  # un quinteto por equipo, sin sustituciones
    home_lineup = next(l for l in game.lineups if len(l.player_ids) == 5 and l.plus_minus == 4)
    away_lineup = next(l for l in game.lineups if l.plus_minus == -4)
    assert len(home_lineup.player_ids) == 5
    assert len(away_lineup.player_ids) == 5


def test_acb_pipeline_run_uses_client_and_reports_summary(engine):
    fake_client = mock.Mock()
    fake_client.fetch_season_game_ids.return_value = ["2025123401"]
    fake_client.fetch_game.return_value = RAW_GAME

    summary = run(engine, season=2025, client=fake_client)

    assert summary == {"loaded": ["2025123401"], "failed": []}
    fake_client.fetch_season_game_ids.assert_called_once_with(2025)


def test_acb_pipeline_run_isolates_failures():
    fake_client = mock.Mock()
    fake_client.fetch_season_game_ids.return_value = ["good", "bad"]

    def fetch_game(game_id, **kwargs):
        if game_id == "bad":
            raise RuntimeError("boom")
        return RAW_GAME

    fake_client.fetch_game.side_effect = fetch_game

    from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db

    engine = create_scouting_engine("sqlite:///:memory:")
    init_scouting_db(engine)
    try:
        summary = run(engine, season=2025, client=fake_client)
    finally:
        engine.dispose()

    assert summary["failed"] == ["bad"]
    assert summary["loaded"] == ["good"]


# --- Refresco por-partido y discovery (feature 014) -------------------------


def _fake_acb_client(match_ids=("2025123401",)):
    """Cliente ACB simulado: calendario en memoria + `fetch_game` fijo."""
    client = mock.Mock()
    client.fetch_season_finished_matches.return_value = [{"id": mid} for mid in match_ids]
    client.fetch_game.return_value = RAW_GAME
    return client


def test_acb_run_single_game_carga_solo_el_partido_pedido(engine):
    """Descarga un único partido tras listar el calendario (requisito de `fetch_game`)."""
    client = _fake_acb_client(("2025123401", "2025123402"))

    summary = run_single_game(engine, season=2025, game_id="2025123401", client=client)

    assert summary == {"loaded": ["2025123401"], "failed": []}
    client.fetch_season_finished_matches.assert_called_once_with(2025)
    # `advanced_stats_team_ids` (Fase 4): vacío porque la BD del fixture no
    # tiene `team_external_ids` de ACB para el Baskonia ni próximo rival
    # cargado — `_advanced_stats_scope` degrada a set() sin fallar.
    client.fetch_game.assert_called_once_with("2025123401", advanced_stats_team_ids=set())

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM games WHERE id='acb-2025123401'")
        ).scalar_one() == 1


def test_acb_run_single_game_es_idempotente(engine):
    """Refrescar dos veces el mismo partido no duplica filas."""
    client = _fake_acb_client()
    run_single_game(engine, season=2025, game_id="2025123401", client=client)
    summary = run_single_game(engine, season=2025, game_id="2025123401", client=client)

    assert summary["loaded"] == ["2025123401"]
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM player_game_stats WHERE game_id='acb-2025123401'")
        ).scalar_one() == 2


def test_acb_run_single_game_partido_desconocido_queda_en_failed(engine):
    """Un id que no está en el calendario de la temporada no lanza: va a `failed`."""
    client = _fake_acb_client(("2025123401",))

    summary = run_single_game(engine, season=2025, game_id="no-existe", client=client)

    assert summary == {"loaded": [], "failed": ["no-existe"]}
    client.fetch_game.assert_not_called()


def test_acb_run_single_game_captura_el_fallo_de_la_fuente(engine):
    """Si la fuente falla (500, red), el partido queda en `failed` sin propagar."""
    client = _fake_acb_client()
    client.fetch_game.side_effect = RuntimeError("500 del servidor")

    summary = run_single_game(engine, season=2025, game_id="2025123401", client=client)

    assert summary == {"loaded": [], "failed": ["2025123401"]}


def test_acb_discover_missing_games_compara_calendario_con_bd(engine):
    """Devuelve los finalizados que faltan en `games`, sin descargar ningún partido."""
    client = _fake_acb_client(("2025123401", "2025123402"))
    run_single_game(engine, season=2025, game_id="2025123401", client=client)
    client.fetch_game.reset_mock()

    missing = discover_missing_games(engine, season=2025, client=client)

    assert missing == ["2025123402"]
    client.fetch_game.assert_not_called()  # discovery no descarga partidos


# --- Calendario futuro (upcoming_matchups, feature 003-vista-plantilla, escudos) ---

_SCHEDULED_TEAMS_BY_ID = {
    "4463": {"name": "Kosner Baskonia", "logo_url": "https://static.acb.com/baskonia.png"},
    "4467": {"name": "Barça", "logo_url": "https://static.acb.com/barca.png"},
    "4477": {"name": "Unicaja", "logo_url": None},
}

_SCHEDULED_MATCHES = [
    # Baskonia (4463) de local ante Barça (4467).
    {"id": 1, "homeTeamId": 4463, "awayTeamId": 4467, "startDateTime": "2026-09-26T18:00:00Z", "matchStatus": "NOT_STARTED"},
    # Baskonia de visitante ante Unicaja (4477, sin escudo verificado).
    {"id": 2, "homeTeamId": 4477, "awayTeamId": 4463, "startDateTime": "2026-10-03T19:00:00Z", "matchStatus": "NOT_STARTED"},
    # Partido entre otros dos equipos: no debe entrar en upcoming_matchups del Baskonia.
    {"id": 3, "homeTeamId": 4467, "awayTeamId": 4477, "startDateTime": "2026-09-27T18:00:00Z", "matchStatus": "NOT_STARTED"},
]


def _fake_scheduled_client(matches=None, teams_by_id=None):
    client = mock.Mock()
    client.fetch_season_scheduled_matches.return_value = (
        matches if matches is not None else _SCHEDULED_MATCHES,
        teams_by_id if teams_by_id is not None else _SCHEDULED_TEAMS_BY_ID,
    )
    return client


def test_run_upcoming_carga_solo_los_partidos_del_baskonia(engine):
    client = _fake_scheduled_client()

    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 2  # el partido Barça-Unicaja se descarta
    with engine.connect() as conn:
        # Filtrado por `season_id` a propósito: `schema.sql` siembra filas de
        # ejemplo en `upcoming_matchups` con `season_id` NULL (contenido de
        # muestra del esquema original, no de esta temporada) — sin este
        # filtro el recuento incluiría también esas filas ajenas.
        rows = conn.execute(
            text(
                "SELECT is_home, match_date FROM upcoming_matchups"
                " WHERE season_id = :season_id ORDER BY match_date"
            ),
            {"season_id": summary["season_id"]},
        ).all()
    assert rows == [(1, "2026-09-26"), (0, "2026-10-03")]


def test_run_upcoming_resuelve_el_equipo_propio_por_la_edicion_actual_no_por_un_external_id_viejo(engine):
    """Regresión: ACB asigna un `id` de equipo NUEVO cada vez que cambia el
    patrocinador — el Baskonia real ya tenía en vivo tres `external_id` de
    temporadas anteriores (`4398`/`4462`/`4425`) sin que ninguno coincidiera
    con el de la edición 2026-2027 (`4463`). Antes de la corrección,
    `_own_team_acb_external_id` cogía cualquiera de los ya vinculados con
    `LIMIT 1` y el filtro de partidos del Baskonia se quedaba en 0 en
    silencio (sin excepción). Se siembra aquí ese mismo escenario: varios
    external_id de `acb` ya vinculados al Baskonia, NINGUNO igual al `4463`
    del catálogo de esta edición.
    """
    with engine.begin() as conn:
        for stale_id in ("4398", "4462", "4425"):
            conn.execute(
                text("INSERT INTO team_external_ids (team_id, source, external_id) VALUES ('bas', 'acb', :eid)"),
                {"eid": stale_id},
            )

    client = _fake_scheduled_client()
    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 2  # no 0 — se resuelve por nombre de la edición, no por un id viejo


def test_run_upcoming_backfillea_el_escudo_del_baskonia_y_del_rival(engine):
    """Verifica el escudo verificado en vivo de ACB (`teams[].logo`, ver
    `AcbClient.fetch_season_scheduled_matches`) — primera fuente real de
    escudo del proyecto."""
    client = _fake_scheduled_client()

    run_upcoming(engine, season=2026, client=client)

    with engine.connect() as conn:
        bas_logo = conn.execute(text("SELECT logo_url FROM teams WHERE id = 'bas'")).scalar_one()
        barca_logo = conn.execute(
            text("SELECT t.logo_url FROM teams t JOIN team_external_ids tei ON tei.team_id = t.id"
                 " WHERE tei.source='acb' AND tei.external_id='4467'")
        ).scalar_one()

    assert bas_logo == "https://static.acb.com/baskonia.png"
    assert barca_logo == "https://static.acb.com/barca.png"


def test_run_upcoming_es_idempotente_reemplaza_en_vez_de_acumular(engine):
    client = _fake_scheduled_client()

    run_upcoming(engine, season=2026, client=client)
    summary = run_upcoming(engine, season=2026, client=client)

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM upcoming_matchups WHERE season_id = :season_id"),
            {"season_id": summary["season_id"]},
        ).scalar_one()
    assert count == 2  # no 4 — la segunda llamada reemplaza el calendario de esa temporada


def test_run_upcoming_descarta_partidos_sin_fecha_confirmada(engine):
    matches = [
        {"id": 9, "homeTeamId": 4463, "awayTeamId": 4467, "startDateTime": None, "matchStatus": "NOT_STARTED"},
    ]
    client = _fake_scheduled_client(matches=matches)

    summary = run_upcoming(engine, season=2026, client=client)

    assert summary["upcoming"] == 0


def test_free_throws_reach_the_database_and_the_average_views(engine):
    """De la fuente a `ft_pct`, que es el recorrido que estaba roto.

    Los adapters ya sumaban los tiros libres para derivar `ft_rate` y luego los
    tiraban, así que `player_game_stats.fta` seguía en NULL por muchas veces
    que se reingiriera. Este test recorre el camino entero: contrato común ->
    `parse_and_resolve` -> `load_game` -> vistas de medias.

    Se comprueba también que `ft_pct` es el acierto PONDERADO POR VOLUMEN
    (SUM(ftm)/SUM(fta)) y no la media de los porcentajes de cada partido: un
    1/1 no puede pesar lo mismo que un 8/12 (ver `schema.sql`).
    """
    raw = {
        **RAW_GAME,
        "game_id": "2025123499",
        "team_stats": [
            {**RAW_GAME["team_stats"][0], "ftm": 14, "fta": 18},
            {**RAW_GAME["team_stats"][1], "ftm": 16, "fta": 20},
        ],
        "players": [
            {**RAW_GAME["players"][0], "ftm": 8, "fta": 12},
            # Sin tiros libres en la fuente: tiene que quedar NULL, no 0.
            RAW_GAME["players"][1],
        ],
    }

    with engine.begin() as conn:
        load_game(conn, parse_and_resolve(conn, raw))

    with engine.connect() as conn:
        howard = conn.execute(
            text("SELECT ftm, fta FROM player_game_stats WHERE player_id = 'howard' AND game_id = 'acb-2025123499'")
        ).first()
        assert (howard.ftm, howard.fta) == (8, 12)

        rival = conn.execute(
            text(
                "SELECT ftm, fta FROM player_game_stats pgs JOIN players p ON p.id = pgs.player_id"
                " WHERE p.name = 'Jugador Rival' AND pgs.game_id = 'acb-2025123499'"
            )
        ).first()
        assert (rival.ftm, rival.fta) == (None, None)

        team = conn.execute(
            text("SELECT ftm, fta FROM game_advanced_stats WHERE game_id = 'acb-2025123499' AND team_id = 'bas'")
        ).first()
        assert (team.ftm, team.fta) == (14, 18)

        # Vista de medias: `gp_ft` cuenta solo los partidos CON dato, y el
        # rival del partido entra como `opp_*` por el self-join.
        row = conn.execute(
            text("SELECT gp, gp_ft, ftm, fta, ft_pct FROM player_stats_combined WHERE player_id = 'howard'")
        ).first()
        assert (row.gp_ft, row.ftm, row.fta) == (1, 8, 12)
        assert row.ft_pct == round(100 * 8 / 12, 6) or abs(row.ft_pct - 100 * 8 / 12) < 1e-6

        opp = conn.execute(
            text("SELECT opp_ftm, opp_fta FROM team_stats_combined WHERE team_id = 'bas'")
        ).first()
        assert (opp.opp_ftm, opp.opp_fta) == (16, 20)


# --- Tiros con reloj/contexto y tiempos muertos (2026-09-28) -----------------
# Formas de payload fieles a lo verificado en vivo en el partido 104465:
# `shotPoints[i]` trae `quarter`/`minute`/`second`/`scoreHome`/`scoreAway`
# (marcador DESPUÉS del tiro) y el mismo tiro aparece en `plays` con el mismo
# reloj; 113 = tiempo muerto de equipo con `playerLicenseId=None`.


def _acb_boxscore_two_players():
    def team(team_id, name, player_id):
        row = {
            "player": {"id": player_id, "firstName": "Nombre", "lastName": f"Apellido{player_id}",
                       "nickname": None, "shirtNumber": "7", "gameRole": "Base"},
            "playTime": "30:00", "isStarted": True, "points": 5,
            "twoPointersMade": 1, "twoPointersAttempted": 2, "threePointersMade": 1, "threePointersAttempted": 2,
            "freeThrowsMade": 0, "freeThrowsAttempted": 0, "totalRebounds": 2, "assists": 0,
        }
        totals = {
            "points": 5, "twoPointersMade": 1, "twoPointersAttempted": 2, "threePointersMade": 1,
            "threePointersAttempted": 2, "freeThrowsMade": 0, "freeThrowsAttempted": 0, "turnovers": 1,
            "offRebounds": 1, "defRebounds": 1, "assists": 0, "steals": 1, "blocks": 0,
        }
        return {"team": {"id": team_id, "fullName": name},
                "statsByPeriods": [{"quarter": 0, "stats": {"players": [row], "team": {}, "total": totals}}]}

    return {"matchFinished": True, "teamBoxscores": [team(10, "Local ACB", 501), team(20, "Visitante ACB", 601)]}


def _play(order, play_type, local, minute, second, player, home, away, quarter=1):
    return {"order": order, "playType": play_type, "local": local, "quarter": quarter, "minute": minute,
            "second": second, "playerLicenseId": player, "scoreHome": home, "scoreAway": away, "playerStats": {}}


_ACB_PLAYS = [
    _play(1, 599, True, 10, 0, 501, 0, 0),
    _play(2, 599, False, 10, 0, 601, 0, 0),
    _play(10, 97, False, 9, 50, 601, 0, 0),    # visitante falla de 2
    _play(11, 104, True, 9, 48, 501, 0, 0),    # rebote defensivo local
    _play(12, 93, True, 9, 44, 501, 2, 0),     # canasta a 4 s del rebote: contraataque
    _play(13, 106, False, 9, 30, 601, 2, 0),   # pérdida visitante...
    _play(14, 103, True, 9, 30, 501, 2, 0),    # ...con robo local, mismo segundo
    _play(15, 98, True, 9, 20, 501, 2, 0),     # triple fallado tras pérdida
    _play(16, 101, True, 9, 18, 501, 2, 0),    # rebote ofensivo
    _play(17, 94, True, 9, 15, 501, 5, 0),     # triple: segunda oportunidad + tras pérdida
    _play(18, 113, False, 9, 15, None, 5, 0),  # tiempo muerto del visitante
    _play(19, 92, False, 9, 0, 601, 5, 1),     # libre anotado
    _play(20, 96, False, 9, 0, 601, 5, 1),     # libre fallado
    _play(21, 100, True, 8, 30, 501, 7, 1),    # mate
    _play(22, 121, True, 10, 0, None, 7, 1, quarter=2),  # inicio de periodo: no es evento tipado
]

_ACB_SHOT_POINTS = [
    {"id": 1, "posX": 2000, "posY": -1500, "playType": 97, "quarter": 1, "minute": 9, "second": 50,
     "local": False, "scoreHome": 0, "scoreAway": 0, "playerLicenseId": 601},
    {"id": 2, "posX": 1000, "posY": 500, "playType": 93, "quarter": 1, "minute": 9, "second": 44,
     "local": True, "scoreHome": 2, "scoreAway": 0, "playerLicenseId": 501},
    {"id": 3, "posX": 7000, "posY": 0, "playType": 98, "quarter": 1, "minute": 9, "second": 20,
     "local": True, "scoreHome": 2, "scoreAway": 0, "playerLicenseId": 501},
    {"id": 4, "posX": 7000, "posY": 500, "playType": 94, "quarter": 1, "minute": 9, "second": 15,
     "local": True, "scoreHome": 5, "scoreAway": 0, "playerLicenseId": 501},
    {"id": 5, "posX": 0, "posY": 0, "playType": 92, "quarter": 1, "minute": 9, "second": 0,
     "local": False, "scoreHome": 5, "scoreAway": 1, "playerLicenseId": 601},
    {"id": 6, "posX": 0, "posY": 0, "playType": 100, "quarter": 1, "minute": 8, "second": 30,
     "local": True, "scoreHome": 7, "scoreAway": 1, "playerLicenseId": 501},
]


def _acb_raw_with_shots_and_pbp():
    from ingest.acb.adapter import build_raw_game

    match = {"id": 104465, "homeTeamId": 10, "awayTeamId": 20, "homeScore": 7, "awayScore": 1,
             "startDateTime": "2025-10-05T18:00:00Z"}
    return build_raw_game(
        match, _acb_boxscore_two_players(), season=2025,
        shots={"shotPoints": copy.deepcopy(_ACB_SHOT_POINTS)}, play_by_play={"plays": copy.deepcopy(_ACB_PLAYS)},
    )


def test_acb_shots_carry_the_clock_and_score_of_match_shots():
    raw = _acb_raw_with_shots_and_pbp()
    by_clock = {shot["clock"]: shot for shot in raw["shots"]}
    assert len(raw["shots"]) == 5  # el libre (92) sigue fuera de `shots`
    assert by_clock["09:44"]["quarter"] == "Q1"
    assert (by_clock["09:44"]["home_score"], by_clock["09:44"]["away_score"]) == (2, 0)
    assert by_clock["08:30"]["located"] is False  # el mate, igual que antes


def test_acb_play_events_include_shots_and_team_timeouts():
    raw = _acb_raw_with_shots_and_pbp()
    types = [event["event_type"] for event in raw["play_events"]]
    for expected in ("fg2_made", "fg2_missed", "fg3_made", "fg3_missed", "ft_made", "ft_missed", "timeout"):
        assert expected in types
    timeout = next(event for event in raw["play_events"] if event["event_type"] == "timeout")
    assert timeout["team_id"] == "20" and timeout["player_id"] is None
    assert (timeout["quarter"], timeout["clock"]) == ("Q1", "09:15")
    dunk = next(event for event in raw["play_events"] if event["event_detail"] == "dunk")
    assert dunk["event_type"] == "fg2_made"
    # Quinteto inicial (599) e inicio de periodo (121) no son eventos tipados.
    assert len(raw["play_events"]) == 12


def test_acb_shot_context_is_derived_and_loaded(engine):
    raw = _acb_raw_with_shots_and_pbp()
    with engine.begin() as conn:
        load_game(conn, parse_and_resolve(conn, raw))

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT game_clock, quarter, seconds, home_score, away_score,"
            " is_fastbreak, is_second_chance, is_off_turnover"
            " FROM shots WHERE game_id = 'acb-104465' ORDER BY seconds"
        )).all()
        timeouts = conn.execute(text(
            "SELECT COUNT(*) FROM play_events WHERE game_id = 'acb-104465' AND event_type = 'timeout'"
            " AND player_id IS NULL"
        )).scalar_one()
    by_clock = {row[0]: tuple(row) for row in rows}
    assert by_clock["09:44"][1:] == ("Q1", 16.0, 2, 0, 1, 0, 0)   # contraataque
    assert by_clock["09:20"][5:] == (0, 0, 1)                     # tras pérdida (10 s: ya no es contraataque)
    assert by_clock["09:15"][5:] == (0, 1, 1)                     # segunda oportunidad + tras pérdida
    assert by_clock["09:50"][5:] == (0, 0, 0)                     # posesión sin inicio conocido
    assert timeouts == 1
