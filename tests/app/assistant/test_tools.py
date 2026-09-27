"""Tests de las herramientas (§12.1). Es el grueso de la suite, y a propósito.

Cada herramienta se prueba como una función Python normal: sin LLM, sin red y
sin Streamlit. Son los tests que garantizan que **las cifras son ciertas**,
que es la única propiedad que de verdad importa en una herramienta de
scouting — el resto (que el modelo redacte bien, que elija la herramienta
correcta) se puede corregir; una cifra falsa presentada con seguridad, no.

Se comprueban tres cosas por herramienta: el valor, la procedencia (`meta`) y
la degradación cuando no hay dato.
"""
import pytest
from sqlalchemy import text

from app.assistant.tools import ToolCatalog
from app.assistant.tools.base import ToolContext
from tests.app.assistant.conftest import LEAGUE_SEASON_ID, TODAY, add_stints


@pytest.fixture()
def catalog(ctx):
    return ToolCatalog(ctx)


@pytest.fixture()
def league_catalog(league_ctx):
    return ToolCatalog(league_ctx)


# ------------------------------------------------------------------ contexto --


def test_get_context_lists_what_is_missing(catalog):
    """El contexto dice qué NO se puede contestar, no solo qué hay (§7.3)."""
    result = catalog.execute("1", "get_context", {}).result

    data = result["data"]
    assert data["own_team"] == {"id": "bas", "name": "Baskonia"}
    assert data["counts"]["games"] > 0
    assert any("tiros libres" in gap for gap in data["gaps"])
    assert any("tramos de tiempo" in gap for gap in data["gaps"])


def test_resolve_entity_tool_flags_ambiguity(catalog, engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('william-howa', 'rm', 'William Howard', 3, 'Alero')"
            )
        )
        # Con boxscore, como el Howard del seed: desde que la resolución
        # desempata por partidos cargados, un jugador sin una sola línea ya no
        # cuenta como empate y este test dejaría de probar la ambigüedad.
        conn.execute(
            text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                " VALUES ('syn-2-1', 'william-howa', 24.0, 11, 3, 2, 52.0)"
            )
        )
    result = catalog.execute("1", "resolve_entity", {"query": "Howard", "kind": "player"}).result
    assert result["data"]["ambiguous"] is True
    assert len(result["data"]["candidates"]) == 2


def test_resolve_entity_tool_reports_a_ghost_instead_of_asking(catalog, engine):
    """El caso del pulgar abajo: un duplicado sin partidos no se pregunta, se dice."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES ('howard-dup', 'rm', 'Marcus Howard', 3, 'Escolta')"
            )
        )

    result = catalog.execute("1", "resolve_entity", {"query": "Marcus Howard"}).result

    assert result["data"]["ambiguous"] is False
    assert result["data"]["candidates"][0]["id"] == "howard"
    assert "CERO partidos" in result["data"]["note"]
    assert "howard-dup" in result["data"]["note"]


def test_resolve_entity_tool_fails_usefully(catalog):
    result = catalog.execute("1", "resolve_entity", {"query": "Michael Jordan"}).result
    assert result["error"] == "sin coincidencias"
    assert "suggestion" in result


# ------------------------------------------------------------------- jugador --


def test_player_game_answers_the_first_question_of_the_brief(catalog):
    """"¿Qué tal jugó X su último partido?" — cifras, contexto y media, de una llamada."""
    invocation = catalog.execute("1", "player_game", {"player_id": "howard"})
    result = invocation.result

    line = result["data"]["line"]
    assert (line["pts"], line["minutes"]) == (19, 31.2)  # g5 del seed
    assert result["data"]["game"]["id"] == "g5"
    # La comparación con su propia media viene hecha: el modelo no la calcula.
    assert result["data"]["season_average"]["pts_avg"] == 17.0
    # Procedencia completa: partido, fecha y competición.
    assert "g5" in result["meta"]["scope"] and "ACB" in result["meta"]["scope"]
    assert result["meta"]["gp"] == 5


def test_player_game_warns_that_missing_free_throws_are_not_zero(catalog):
    """NULL significa 'partido anterior a la columna', no 'no tiró' (§2.4)."""
    result = catalog.execute("1", "player_game", {"player_id": "howard"}).result
    assert any("tiros libres" in warning for warning in result["meta"]["warnings"])


def test_player_game_falls_back_to_the_last_season_with_data(catalog):
    """Sin partidos TODAVÍA en la temporada pedida, cae a la última que sí tiene (§7.2)."""
    result = catalog.execute("1", "player_game", {"player_id": "howard", "season_id": 99}).result
    line = result["data"]["line"]
    assert (line["pts"], line["minutes"]) == (19, 31.2)  # g5 del seed, temporada 1
    assert result["meta"]["scope"].startswith("g5")
    assert any("temporada 99" in warning and "2025-2026" in warning for warning in result["meta"]["warnings"])


def test_player_game_fails_when_there_is_no_data_in_any_season_up_to_the_one_asked(catalog):
    """Pedir una temporada ANTERIOR a la única que tiene datos no cae hacia delante (§7.2)."""
    result = catalog.execute("1", "player_game", {"player_id": "howard", "season_id": 0}).result
    assert result["error"] == "sin datos"
    assert "temporada" in result["suggestion"]


def test_player_averages_cites_games_played(catalog):
    result = catalog.execute("1", "player_averages", {"player_id": "kotsar"}).result
    combined = next(row for row in result["data"]["averages"] if row["competition"] == "Combinado")
    assert combined["gp"] == 5
    assert result["meta"]["gp"] == 5


def test_player_averages_says_it_has_no_free_throws_when_the_db_has_none(catalog):
    """`efg_pct` excluye los libres por definición, así que sin ftm/fta no se
    puede hablar del juego desde la línea — y hay que decirlo, no callarlo."""
    result = catalog.execute("1", "player_averages", {"player_id": "howard"}).result

    assert result["data"]["free_throws"] == []
    assert any("no tiene tiros libres" in warning for warning in result["meta"]["warnings"])


def test_player_averages_returns_free_throws_once_the_db_has_them(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        # 8/12 en un partido y 1/1 en otro: el acierto ponderado (9/13 = 69.2%)
        # NO es la media de los porcentajes de cada partido (83.3%), que es la
        # trampa que `ft_pct` evita a propósito.
        conn.execute(text("UPDATE player_game_stats SET ftm = 8, fta = 12 WHERE game_id = 'g5' AND player_id = 'howard'"))
        conn.execute(text("UPDATE player_game_stats SET ftm = 1, fta = 1 WHERE game_id = 'g4' AND player_id = 'howard'"))
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "player_averages", {"player_id": "howard"}).result

    acb = next(row for row in result["data"]["free_throws"] if row["competition"] == "ACB")
    assert (acb["ftm"], acb["fta"]) == (9, 13)
    assert acb["ft_pct"] == pytest.approx(100 * 9 / 13, abs=0.01)
    # Jugó 3 partidos de ACB pero solo 2 traen el dato: hay que avisarlo.
    assert acb["gp_ft"] == 2 and acb["gp"] == 3
    assert any("gp_ft" in warning for warning in result["meta"]["warnings"])


def test_player_averages_says_it_has_no_box_extras_when_the_db_has_none(catalog):
    """Boxscore ampliado (Fase 1): mismo criterio de degradación que free_throws."""
    result = catalog.execute("1", "player_averages", {"player_id": "howard"}).result

    assert result["data"]["box_extras"] == []
    assert any("no tiene boxscore ampliado" in warning for warning in result["meta"]["warnings"])


def test_player_averages_returns_box_extras_once_the_db_has_them(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE player_game_stats SET stl = 2, tov = 1, blk = 1, pf = 3, oreb = 1, dreb = 3,"
                " plus_minus = 8, pir = 22 WHERE game_id = 'g5' AND player_id = 'howard'"
            )
        )
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "player_averages", {"player_id": "howard"}).result

    acb = next(row for row in result["data"]["box_extras"] if row["competition"] == "ACB")
    assert acb["gp_box_extras"] == 1 and acb["gp"] == 3
    # `AVG()` ignora los NULL: con un único partido con dato, la media es
    # ese valor tal cual, no el total repartido entre los 3 partidos jugados.
    assert acb["pir_avg"] == pytest.approx(22, abs=0.01)
    assert any("gp_box_extras" in warning for warning in result["meta"]["warnings"])


def test_player_game_warns_that_missing_box_extras_are_not_zero(catalog):
    result = catalog.execute("1", "player_game", {"player_id": "howard"}).result
    assert any("boxscore ampliado" in warning for warning in result["meta"]["warnings"])


def test_team_profile_separates_box_extras_taken_from_conceded(engine, ctx):
    """Cuántos robos/tapones/pérdidas/faltas REGISTRA el rival en ese partido
    es lo que hace falta para leer qué concede una defensa, no solo lo propio."""
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE game_advanced_stats SET stl = 6, tov = 10, blk = 2, pf = 15"
                " WHERE game_id = 'g5' AND team_id = 'bas'"
            )
        )
        conn.execute(
            text("INSERT INTO game_advanced_stats (game_id, team_id, efg_pct, ts_pct, tov_pct, orb_pct,"
                 " stl, tov, blk, pf) VALUES ('g5', 'val', 50.0, 53.0, 12.0, 22.0, 9, 14, 4, 18)")
        )
        # `Capabilities.box_extras` sonda `player_game_stats.stl` (columna
        # compartida por jugador y equipo, ver `capabilities.py`) — sin esto
        # la capacidad seguiría apagada aunque `game_advanced_stats` ya tenga
        # el dato de equipo.
        conn.execute(text("UPDATE player_game_stats SET stl = 2 WHERE game_id = 'g5' AND player_id = 'howard'"))
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "team_profile", {"team_id": "bas"}).result

    acb = next(row for row in result["data"]["box_extras"] if row["competition"] == "ACB")
    assert (acb["stl_avg"], acb["tov_avg"], acb["blk_avg"], acb["pf_avg"]) == (6, 10, 2, 15)          # lo propio
    assert (acb["opp_stl_avg"], acb["opp_tov_avg"], acb["opp_blk_avg"], acb["opp_pf_avg"]) == (9, 14, 4, 18)  # lo que concede


def test_team_profile_separates_free_throws_taken_from_conceded(engine, ctx):
    """Cuántos libres REGALA una defensa dice más de ella que cuántos lanza su
    ataque; sin las columnas `opp_*` de la vista no se podía leer."""
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(text("UPDATE player_game_stats SET ftm = 5, fta = 6 WHERE game_id = 'g5'"))
        conn.execute(
            text("INSERT INTO game_advanced_stats (game_id, team_id, efg_pct, ts_pct, tov_pct, orb_pct, ftm, fta)"
                 " VALUES ('g5', 'val', 50.0, 53.0, 12.0, 22.0, 20, 25)")
        )
        conn.execute(text("UPDATE game_advanced_stats SET ftm = 14, fta = 18 WHERE game_id = 'g5' AND team_id = 'bas'"))
    ctx.capabilities = probe(engine)

    result = ToolCatalog(ctx).execute("1", "team_profile", {"team_id": "bas"}).result

    acb = next(row for row in result["data"]["free_throws"] if row["competition"] == "ACB")
    assert (acb["ftm"], acb["fta"]) == (14, 18)          # los que lanza
    assert (acb["opp_ftm"], acb["opp_fta"]) == (20, 25)  # los que concede


def test_team_profile_falls_back_to_the_last_season_with_data(catalog):
    """Mismo criterio que ya usaba 'Próximo rival' (`queries.team_scouting_season`),
    ahora también en la herramienta del asistente (§7.2)."""
    result = catalog.execute("1", "team_profile", {"team_id": "bas", "season_id": 99}).result
    # "bas" tiene partidos en la 1 (seed) y en la 2 (liga sintética, más reciente):
    # cae a la MÁS RECIENTE con datos, no a la primera que encuentre.
    assert result["meta"]["scope"] == "temporada 2"
    assert any("temporada 99" in warning and "2026-2027" in warning for warning in result["meta"]["warnings"])


def test_player_form_needs_at_least_two_games(catalog, engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO players (id, team_id, name, number, position) VALUES ('nuevo', 'bas', 'Nuevo', 44, 'Base')")
        )
        conn.execute(
            text(
                "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                " VALUES ('g5', 'nuevo', 5.0, 2, 1, 0, 50.0)"
            )
        )
    result = catalog.execute("1", "player_form", {"player_id": "nuevo"}).result
    assert result["error"] == "muestra insuficiente"


def test_player_form_returns_z_scores(catalog):
    result = catalog.execute("1", "player_form", {"player_id": "howard", "last_n": 2}).result
    assert set(result["data"]["z"]) == {"minutes", "pts", "reb", "ast", "efg_pct"}
    assert result["data"]["gp"] == 5


# -------------------------------------------------------------------- equipo --


def test_team_style_without_enough_games_gives_numbers_but_no_percentile(catalog):
    """En la temporada del seed nadie llega a 5 partidos por competición: sin
    percentil, pero con los números crudos y diciendo por qué faltan."""
    result = catalog.execute("1", "team_style", {"team_id": "bas"}).result

    assert result["data"]["style"] == []
    assert any("Sin percentil de liga" in warning for warning in result["meta"]["warnings"])
    assert result["data"]["profile"]  # los números crudos sí están


def test_team_style_without_percentile_views_forbids_adjectives(engine, ctx):
    """Base de datos que aún no se ha reingerido: no hay vistas de percentiles.

    Es el caso real de una BD de producción por detrás de `schema.sql` (§7.3).
    No es un error: se responde igual, pero avisando de que los números van
    sin contexto y NO se pueden adjetivar.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DROP VIEW team_style_percentiles"))
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    assert ctx.capabilities.league_percentiles is False

    result = ToolCatalog(ctx).execute("1", "team_style", {"team_id": "bas"}).result
    assert any("SIN contexto" in warning for warning in result["meta"]["warnings"])
    assert result["data"]["profile"]


def test_team_style_with_league_context_labels_every_metric(league_catalog):
    """Con percentiles, cada métrica trae etiqueta calculada en Python (§6.2)."""
    result = league_catalog.execute("1", "team_style", {"team_id": "bas"}).result

    acb = next(row for row in result["data"]["style"] if row["competition"] == "ACB")
    # `bas` es el primero de `LEAGUE_TEAMS`, es decir el mejor de la liga
    # sintética: mejor ataque, mejor defensa y ritmo más bajo.
    assert acb["ortg"]["label"] == "alto"
    assert acb["ortg"]["percentile"] == 100
    assert acb["drtg"]["label"] == "alto"  # percentil invertido: alto = buena defensa
    assert acb["pace"]["label"] == "bajo"
    assert acb["ortg"]["league_teams"] == len(["bas", "rm", "fcb", "val", "gc", "uni"])


def test_team_style_always_warns_about_euroleague_estimates(catalog):
    result = catalog.execute("1", "team_style", {"team_id": "bas"}).result
    assert any("Dean Oliver" in warning for warning in result["meta"]["warnings"])


def test_head_to_head_counts_every_season(catalog):
    """Sin filtro de temporada a propósito: para preparar un partido, un cruce
    de hace dos años informa igual (`queries.head_to_head`). Aquí son la
    derrota del seed (`g3`) más los dos cruces de la liga sintética."""
    result = catalog.execute("1", "head_to_head", {"team_id": "bas", "opponent_id": "fcb"}).result
    assert result["data"]["record"] == {"wins": 2, "losses": 1}
    assert result["meta"]["gp"] == 3


def test_head_to_head_without_games_suggests_an_alternative(catalog):
    result = catalog.execute("1", "head_to_head", {"team_id": "bas", "opponent_id": "baxi"}).result
    assert result["error"] == "sin cruces"
    assert "compare" in result["suggestion"]


def test_team_roster_warns_that_the_squad_is_approximate(catalog):
    result = catalog.execute("1", "team_roster", {"team_id": "bas"}).result
    assert any("equipo ACTUAL" in warning for warning in result["meta"]["warnings"])


# --------------------------------------------------------------------- liga --


def test_league_leaders_respects_the_minimum_of_games(league_catalog, catalog):
    """Sin mínimo, el líder de cualquier media es siempre quien jugó un partido."""
    result = catalog.execute("1", "league_leaders", {"metric": "pts", "competition_id": 1}).result
    assert result["error"] == "sin datos"  # nadie llega a 5 partidos de ACB en el seed

    relaxed = catalog.execute("2", "league_leaders", {"metric": "pts", "competition_id": 1, "min_gp": 3}).result
    assert relaxed["data"]["leaders"][0]["name"] == "Marcus Howard"


def test_league_leaders_sorts_defensive_rating_upwards(league_catalog):
    """En DRtg, menos es mejor: ordenarlo descendente pondría al peor arriba."""
    result = league_catalog.execute("1", "league_leaders", {"metric": "drtg", "competition_id": 1}).result
    leaders = result["data"]["leaders"]
    assert leaders[0]["id"] == "bas"
    assert leaders[0]["value"] < leaders[-1]["value"]


def test_league_leaders_rejects_an_unknown_metric(catalog):
    result = catalog.execute("1", "league_leaders", {"metric": "puntos", "competition_id": 1}).result
    assert result["error"] == "métrica no soportada"


def test_standings_only_counts_played_games(league_catalog):
    result = league_catalog.execute("1", "standings", {"competition_id": 1}).result
    table = result["data"]
    assert table[0]["team"] == "Baskonia"
    assert table[0]["gp"] == 10  # dos vueltas contra cinco rivales
    assert any("no es la clasificación oficial" in w for w in result["meta"]["warnings"])


def test_compare_flags_entities_without_data(catalog):
    result = catalog.execute("1", "compare", {"kind": "player", "entity_ids": ["howard", "kotsar"]}).result
    assert len(result["data"]) == 2
    assert result["meta"].get("warnings") is None


def test_compare_needs_two_entities(catalog):
    result = catalog.execute("1", "compare", {"kind": "player", "entity_ids": ["howard"]}).result
    assert result["error"] == "hacen falta al menos dos"


# ---------------------------------------------------------------- quintetos --


def test_team_lineups_applies_the_minimum_of_shared_minutes(catalog):
    """Sin mínimo, el "mejor quinteto" es siempre ruido de cuarenta segundos."""
    result = catalog.execute("1", "team_lineups", {"team_id": "bas", "min_minutes": 5}).result
    assert result["data"]
    assert all(row["minutes"] >= 5 for row in result["data"])

    strict = catalog.execute("2", "team_lineups", {"team_id": "bas", "min_minutes": 500}).result
    assert strict["error"] == "sin quintetos"


def test_team_lineups_warns_that_the_team_was_inferred(catalog):
    """El seed no trae `lineups.team_id`: se deduce y hay que decirlo (§2.3)."""
    result = catalog.execute("1", "team_lineups", {"team_id": "bas", "min_minutes": 5}).result
    assert any("deducido" in warning for warning in result["meta"]["warnings"])


def test_team_lineups_says_it_cannot_slice_the_final_minutes(catalog):
    """El comportamiento de §2.3 mientras no haya tramos: nombrar la limitación."""
    result = catalog.execute("1", "team_lineups", {"team_id": "bas", "min_minutes": 5}).result
    assert any("últimos minutos" in warning for warning in result["meta"]["warnings"])


def test_clutch_lineups_is_not_registered_without_stints(ctx):
    """Una herramienta que existe y siempre falla es peor que su ausencia (§4.4)."""
    assert "clutch_lineups" not in ToolCatalog(ctx).tools


def test_clutch_lineups_appears_and_answers_once_there_are_stints(engine, ctx):
    """La tercera pregunta del encargo, contestada de verdad."""
    starters = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
    bench = ["howard", "nikos", "moneke", "lutse", "costello"]
    add_stints(
        engine,
        [
            # Tramo final y apretado con el quinteto titular: +6.
            ("g5", "bas", 2150.0, 2400.0, 12, 6, 2, starters),
            # Otro tramo final apretado, del quinteto del banquillo: -4.
            ("g4", "bas", 2200.0, 2400.0, 5, 9, -1, bench),
            # Tramo final pero con el partido roto (+20): fuera del filtro.
            ("g3", "bas", 2200.0, 2400.0, 10, 2, 20, starters),
            # Tramo largo pero del primer cuarto: fuera de la ventana.
            ("g2", "bas", 0.0, 500.0, 30, 10, 0, starters),
        ],
    )
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)
    assert "clutch_lineups" in catalog.tools

    result = catalog.execute("1", "clutch_lineups", {"team_id": "bas"}).result
    rows = result["data"]

    assert rows[0]["plus_minus"] == 6
    assert rows[0]["seconds"] == 250.0
    # El tramo del primer cuarto NO suma sus 500 segundos al quinteto titular.
    assert rows[0]["stints"] == 1
    assert "últimos 5 min" in result["meta"]["scope"]


def test_clutch_lineups_clips_a_stint_to_the_window(engine, ctx):
    """Un tramo que empieza en el minuto 30 no aporta diez minutos de 'últimos cinco'."""
    starters = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
    add_stints(engine, [("g5", "bas", 1800.0, 2400.0, 20, 10, 0, starters)])
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    result = ToolCatalog(ctx).execute("1", "clutch_lineups", {"team_id": "bas"}).result

    # Ventana = últimos 300 s (2100..2400): solo cuentan 300, no 600.
    assert result["data"][0]["seconds"] == 300.0


def test_clutch_lineups_returns_the_per_40_like_its_sibling_tools(engine, ctx):
    """Sin la columna, el modelo dividía él mismo ("+64 por 40") y el
    verificador marcaba la cifra en una respuesta correcta: dos veces en 48
    intentos del set dorado. `team_lineups` y compañía ya la devolvían."""
    starters = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
    add_stints(engine, [("g5", "bas", 2100.0, 2400.0, 12, 6, 0, starters)])
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    row = ToolCatalog(ctx).execute("1", "clutch_lineups", {"team_id": "bas"}).result["data"][0]

    # +6 en 5 minutos -> +48 por 40.
    assert row["plus_minus"] == 6
    assert row["plus_minus_per_40"] == pytest.approx(48.0)


def test_clutch_lineups_still_ranks_by_raw_difference_not_by_rate(engine, ctx):
    """En el tramo final hay pocos segundos por quinteto, y ordenar por la tasa
    pondría arriba al que menos jugó. El por-40 se devuelve, pero no manda."""
    long_sample = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
    short_sample = ["howard", "nikos", "moneke", "lutse", "costello"]
    add_stints(
        engine,
        [
            # +8 en 300 s -> +64 por 40.
            ("g5", "bas", 2100.0, 2400.0, 14, 6, 0, long_sample),
            # +5 en 60 s -> +200 por 40: más tasa, mucha menos muestra.
            ("g4", "bas", 2340.0, 2400.0, 5, 0, 0, short_sample),
        ],
    )
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    rows = ToolCatalog(ctx).execute("1", "clutch_lineups", {"team_id": "bas"}).result["data"]

    assert rows[0]["plus_minus"] == 8
    assert rows[1]["plus_minus_per_40"] > rows[0]["plus_minus_per_40"]


# --------------------------------------------------------- on/off y duplas --
# Propuesta 07 (`doc/features/propuestas/07_onoff_y_duplas.md`). Dos tramos
# con rosters distintos (solapan solo en "howard" y "moneke") para poder
# afirmar con números exactos on/off, muestra suficiente/insuficiente y el
# ranking de parejas/tríos, sin depender de una BD real.
_ON_OFF_STARTERS = ["howard", "moneke", "codi", "sedekerskis", "kotsar"]
_ON_OFF_BENCH = ["howard", "nikos", "moneke", "lutse", "costello"]


def _add_on_off_stints(engine) -> None:
    add_stints(
        engine,
        [
            # 100 min con los titulares, +20 de diferencia -> +8.0 por 40.
            ("g5", "bas", 0.0, 6000.0, 60, 40, 0, _ON_OFF_STARTERS),
            # 200 min con el banquillo, -20 de diferencia -> -4.0 por 40.
            ("g4", "bas", 0.0, 12000.0, 80, 100, 0, _ON_OFF_BENCH),
        ],
    )


def test_player_on_off_is_not_registered_without_stints(ctx):
    """Mismo criterio que clutch_lineups (§4.4): sin tramos, ni se enseña."""
    assert "player_on_off" not in ToolCatalog(ctx).tools
    assert "player_combos" not in ToolCatalog(ctx).tools


def test_player_on_off_splits_context_and_flags_the_sample(engine, ctx):
    """On/Off = diferencia por 40 CON el jugador menos SIN él (§4), con el aviso de muestra."""
    _add_on_off_stints(engine)
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    result = ToolCatalog(ctx).execute("1", "player_on_off", {"team_id": "bas"}).result
    by_name = {row["player_name"]: row for row in result["data"]}

    # Sedekerskis solo estuvo en los 100 minutos de titulares: no llega a los
    # 200 minutos no negociables del §4, así que sale marcado como no fiable.
    sede = by_name["Tadas Sedekerskis"]
    assert sede["on_minutes"] == 100.0 and sede["on_per_40"] == 8.0
    assert sede["off_minutes"] == 200.0 and sede["off_per_40"] == -4.0
    assert sede["on_off"] == 12.0
    assert sede["reliable"] is False

    # Rogkavopoulos solo estuvo en los 200 minutos de banquillo: SÍ llega al
    # mínimo, con el signo opuesto (el equipo rindió peor con el banquillo).
    nikos = by_name["Nikos Rogkavopoulos"]
    assert nikos["on_minutes"] == 200.0 and nikos["on_off"] == -12.0
    assert nikos["reliable"] is True

    # Howard jugó los DOS tramos: no le queda ningún minuto "sin él" con el
    # que comparar, así que el On/Off no se puede calcular (huella NaN -> None
    # tras `records`, no un 0 que sugiera "neutro").
    howard = by_name["Marcus Howard"]
    assert howard["on_minutes"] == 300.0
    assert howard["off_minutes"] == 0.0
    assert howard["on_off"] is None

    assert any("no es una medida" in warning.lower() or "contexto" in warning.lower() for warning in result["meta"]["warnings"])
    assert any("no llegan a" in warning for warning in result["meta"]["warnings"])


def test_player_combos_ranks_pairs_by_plus_minus_per_40(engine, ctx):
    """Mejor y peor pareja del §2b, entre TODAS las combinaciones posibles."""
    _add_on_off_stints(engine)
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)

    best = catalog.execute("1", "player_combos", {"team_id": "bas", "size": 2, "order": "best"}).result
    assert best["data"][0]["plus_minus_per_40"] == 8.0  # cualquier pareja SOLO de titulares

    worst = catalog.execute("2", "player_combos", {"team_id": "bas", "size": 2, "order": "worst"}).result
    assert worst["data"][0]["plus_minus_per_40"] == -4.0  # cualquier pareja SOLO de banquillo

    # Howard+Moneke coincidieron en los dos tramos: se suman (300 min, +20-20=0).
    combined = {row["player_ids"]: row for row in best["data"]}
    howard_moneke = combined["howard,moneke"]
    assert howard_moneke["minutes"] == 300.0
    assert howard_moneke["plus_minus_per_40"] == 0.0
    assert howard_moneke["stints"] == 2


def test_player_combos_supports_trios(engine, ctx):
    _add_on_off_stints(engine)
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    result = ToolCatalog(ctx).execute("1", "player_combos", {"team_id": "bas", "size": 3}).result
    assert result["data"]
    assert all(row["jugadores"].count(" · ") == 2 for row in result["data"])
    assert "tríos" in result["meta"]["scope"]


def test_player_combos_fails_usefully_below_the_minimum_sample(engine, ctx):
    """Un solo tramo de medio minuto no llega ni de lejos a los 100 minutos no negociables."""
    add_stints(engine, [("g5", "bas", 0.0, 30.0, 1, 0, 0, _ON_OFF_STARTERS)])
    from app.assistant.capabilities import probe

    ctx.capabilities = probe(engine)
    result = ToolCatalog(ctx).execute("1", "player_combos", {"team_id": "bas"}).result
    assert result["error"] == "sin combinaciones con muestra"


def test_team_foul_quarter_profile_is_not_registered_without_play_events(ctx):
    assert "team_foul_quarter_profile" not in ToolCatalog(ctx).tools


def test_team_foul_quarter_profile_answers_once_there_are_play_events(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO game_team_quarter_stats"
                " (game_id, team_id, quarter, points_for, points_against, fouls_for, fouls_against)"
                " VALUES ('g5', 'bas', 1, 20, 18, 2, 1)"
            )
        )
        # Al menos un evento tipado real para que la capacidad `play_events` se encienda.
        conn.execute(
            text(
                "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                " event_type, home_score, away_score)"
                " VALUES ('g5', 'bas', 'howard', 'Q1', '08:00', 2320, 'foul_personal', 2, 0)"
            )
        )
    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)
    assert "team_foul_quarter_profile" in catalog.tools

    result = catalog.execute("1", "team_foul_quarter_profile", {"team_id": "bas"}).result
    row = next(r for r in result["data"] if r["quarter"] == 1)
    assert (row["avg_fouls_for"], row["avg_fouls_against"]) == (2, 1)


def test_game_play_events_is_not_registered_without_play_events(ctx):
    assert "game_play_events" not in ToolCatalog(ctx).tools


def test_game_play_events_lists_typed_events_for_a_game(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                " event_type, home_score, away_score) VALUES"
                " ('g5', 'bas', 'howard', 'Q1', '08:00', 2320, 'steal', 2, 0),"
                " ('g5', 'bas', 'kotsar', 'Q1', '05:00', 2500, 'foul_personal', 4, 2)"
            )
        )
    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)
    assert "game_play_events" in catalog.tools

    result = catalog.execute("1", "game_play_events", {"game_id": "g5", "event_type": "steal"}).result
    assert len(result["data"]) == 1
    assert result["data"][0]["player_name"] == "Marcus Howard"
    assert result["data"][0]["event_type"] == "steal"


def test_player_advanced_profile_is_not_registered_without_data(ctx):
    assert "player_advanced_profile" not in ToolCatalog(ctx).tools


def test_player_advanced_profile_answers_with_win_loss_context(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO player_advanced_stats (game_id, player_id, ts_pct, ppt) VALUES ('g5', 'howard', 61.2, 1.3)")
        )
    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)
    assert "player_advanced_profile" in catalog.tools

    result = catalog.execute("1", "player_advanced_profile", {"player_id": "howard"}).result
    assert result["data"][0]["ts_pct"] == 61.2
    # g5 (seed): Baskonia local 84-79 vs Valencia -> victoria del Baskonia.
    assert result["data"][0]["win"] == 1


def test_player_quarter_profile_is_not_registered_without_quarter_stats(ctx):
    assert "player_quarter_profile" not in ToolCatalog(ctx).tools


def test_player_quarter_profile_answers_once_there_are_quarter_stats(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO player_game_quarter_stats (game_id, player_id, quarter, pts, pir)"
                " VALUES ('g5', 'howard', 1, 8, 6), ('g5', 'howard', 4, 2, -1)"
            )
        )
    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)
    assert "player_quarter_profile" in catalog.tools

    result = catalog.execute("1", "player_quarter_profile", {"player_id": "howard"}).result
    by_quarter = {row["quarter"]: row for row in result["data"]}
    assert by_quarter[1]["pts_avg"] == 8
    assert by_quarter[4]["pts_avg"] == 2
    assert result["meta"]["source"] == "player_game_quarter_stats"


# ---------------------------------------------------------------------- partido --


def test_game_boxscore_warns_when_there_is_no_metadata(catalog):
    """Sin árbitros/asistencia/pabellón (capacidad apagada), dilo en vez de callarlo (§7.1)."""
    result = catalog.execute("1", "game_boxscore", {"game_id": "g5"}).result
    assert "metadata" not in result["data"]
    assert any("metadata de partido" in warning for warning in result["meta"]["warnings"])


def test_game_boxscore_includes_metadata_once_the_db_has_it(engine, ctx):
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE games SET arena = 'Fernando Buesa Arena', attendance = 10000,"
                " referees = 'Árbitro Uno · Árbitro Dos' WHERE id = 'g5'"
            )
        )
    ctx.capabilities = probe(engine)
    catalog = ToolCatalog(ctx)

    result = catalog.execute("1", "game_boxscore", {"game_id": "g5"}).result
    assert result["data"]["metadata"]["arena"] == "Fernando Buesa Arena"
    assert result["data"]["metadata"]["attendance"] == 10000
    assert not result["meta"].get("warnings")


# ----------------------------------------------------- catálogo y ejecución --


def test_unknown_tool_returns_an_error_with_the_list_of_valid_ones(catalog):
    result = catalog.execute("1", "player_vibes", {}).result
    assert result["error"] == "herramienta desconocida"
    assert "player_game" in result["suggestion"]


def test_invented_arguments_are_rejected_with_the_valid_ones(catalog):
    """Un modelo pequeño se inventa parámetros plausibles; el mensaje debe enseñárselos."""
    result = catalog.execute("1", "player_game", {"player_id": "howard", "temporada": 1}).result
    assert result["error"] == "argumentos inválidos"
    assert "season_id" in result["suggestion"]


def test_a_tool_that_raises_does_not_bring_down_the_turn(catalog, monkeypatch):
    def boom(ctx, **kwargs):
        raise RuntimeError("la base de datos se ha ido de vacaciones")

    monkeypatch.setitem(catalog.tools, "player_game", catalog.tools["player_game"].__class__(
        name="player_game", family="player", description="x", parameters={"type": "object", "properties": {}},
        fn=boom,
    ))
    invocation = catalog.execute("1", "player_game", {})
    assert invocation.error
    assert invocation.result["error"] == "error interno de la herramienta"


def test_by_family_loading_pages_the_catalogue_without_removing_anything(ctx):
    """`by_family` cambia CUÁNDO se ve el esquema, no qué se puede preguntar (§8.4)."""
    catalog = ToolCatalog(ctx, loading="by_family")

    visible = {spec.name for spec in catalog.specs()}
    assert "get_context" in visible  # la familia de contexto siempre está
    assert "player_game" not in visible
    assert "load_tools" in visible
    # Pero la herramienta SIGUE existiendo en el sistema.
    assert "player_game" in catalog.tools

    catalog.execute("1", "load_tools", {"family": "player"})
    assert "player_game" in {spec.name for spec in catalog.specs()}


def test_serialize_result_leaves_the_artifact_out_of_the_context(catalog):
    """Los tiros crudos se pintan, no se le mandan al modelo (§9.3)."""
    from app.assistant.tools import serialize_result

    result = catalog.execute("1", "player_game", {"player_id": "howard"}).result
    assert "artifact" in result
    assert "artifact" not in serialize_result(result)


def test_player_shot_profile_breaks_down_the_zones(catalog):
    """El reparto (`share`) importa tanto como el acierto: un 60% en la pintura
    no dice nada si solo tira dos veces."""
    result = catalog.execute("1", "player_shot_profile", {"player_id": "howard", "game_id": "g5"}).result

    zones = {row["zone_label"]: row for row in result["data"]}
    assert zones["Pintura"]["attempts"] == 2
    assert zones["Pintura"]["fg_pct"] == 50.0
    assert sum(row["share"] for row in result["data"]) == pytest.approx(100.0)
    assert result["artifact"]["type"] == "shot_chart"


def test_player_shot_profile_without_shots_explains_why(catalog):
    result = catalog.execute("1", "player_shot_profile", {"player_id": "kotsar"}).result
    assert result["error"] == "sin tiros"
    assert "coordenadas" in result["suggestion"]


# ------------------------------------------------------------------ game_runs --
# "¿Dónde se decidió el partido?" contestada con la MISMA lógica que pinta la
# pestaña "Rotaciones" de "Partidos anteriores": dos definiciones distintas de
# "parcial" —una en el gráfico y otra en el chat— es la forma más rápida de
# que el asistente contradiga a la pantalla delante del entrenador.

#: Play-by-play sintético de `g5` (bas local, val visitante): un 10-0 del
#: Baskonia entre el segundo 120 y el 180, y marcador plano alrededor, para
#: que el parcial detectado sea uno y se puedan afirmar sus cifras.
_RUN_EVENTS = (
    "('g5', 'bas', NULL,     'Q1', '09:30', 30,  'dreb',     0,  0),"
    "('g5', 'val', NULL,     'Q1', '08:00', 120, 'dreb',     6,  6),"
    "('g5', 'bas', 'howard', 'Q1', '07:30', 150, 'steal',    11, 6),"
    "('g5', 'val', NULL,     'Q1', '07:00', 180, 'turnover', 16, 6),"
    "('g5', 'bas', NULL,     'Q1', '06:30', 210, 'dreb',     16, 6)"
)


def _load_run_fixture(engine, ctx, with_stints=True):
    """Deja `g5` con play-by-play (y opcionalmente tramos) y vuelve a sondear."""
    from sqlalchemy import text

    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO play_events (game_id, team_id, player_id, quarter, game_clock, seconds,"
                f" event_type, home_score, away_score) VALUES {_RUN_EVENTS}"
            )
        )
    if with_stints:
        add_stints(engine, [("g5", "bas", 0.0, 400.0, 16, 6, 0, ["howard", "moneke", "codi", "nikos", "kotsar"])])
    ctx.capabilities = probe(engine)
    return ToolCatalog(ctx)


def test_game_runs_is_not_registered_without_play_events(ctx):
    """Sin marcador con reloj no hay parciales que detectar (§4.4)."""
    assert "game_runs" not in ToolCatalog(ctx).tools


def test_game_runs_answers_where_the_game_was_decided(engine, ctx):
    """El parcial, el quinteto que lo jugó y lo que pasó dentro, en una llamada."""
    catalog = _load_run_fixture(engine, ctx)
    assert "game_runs" in catalog.tools

    result = catalog.execute("1", "game_runs", {"game_id": "g5"}).result
    run = result["data"]["parciales"][0]

    assert result["data"]["team_id"] == "bas"
    assert (run["start_seconds"], run["end_seconds"], run["swing"]) == (120.0, 180.0, 10)
    assert (run["points_for"], run["points_against"]) == (10, 0)
    assert "Marcus Howard" in run["quinteto"]
    assert any("steal (Marcus Howard)" in event for event in run["eventos"])
    assert result["artifact"]["type"] == "table"


def test_game_runs_always_says_that_the_shots_are_missing(engine, ctx):
    """La mayor carencia de la función se dice en voz alta, no se deduce (§5)."""
    catalog = _load_run_fixture(engine, ctx)

    result = catalog.execute("1", "game_runs", {"game_id": "g5"}).result

    assert any("tiros" in warning for warning in result["meta"]["warnings"])


def test_game_runs_warns_when_it_cannot_name_the_five_on_court(engine, ctx):
    """Hay parcial pero no tramos: se contesta lo que se sabe, avisando de lo que no."""
    catalog = _load_run_fixture(engine, ctx, with_stints=False)

    result = catalog.execute("1", "game_runs", {"game_id": "g5"}).result

    assert result["data"]["parciales"][0]["quinteto"] is None
    assert any("quién estaba en pista" in warning for warning in result["meta"]["warnings"])


def test_game_runs_rejects_a_team_that_did_not_play_that_game(engine, ctx):
    """Un parcial 'a favor' de un equipo que no juega el partido no significa nada."""
    catalog = _load_run_fixture(engine, ctx)

    result = catalog.execute("1", "game_runs", {"game_id": "g5", "team_id": "rm"}).result

    assert result["error"] == "equipo ajeno al partido"
    assert "'bas'" in result["suggestion"]


def test_game_runs_with_a_threshold_nothing_reaches_suggests_lowering_it(engine, ctx):
    """Los umbrales son del entrenador: si no sale nada, el camino es bajarlos."""
    catalog = _load_run_fixture(engine, ctx)

    result = catalog.execute("1", "game_runs", {"game_id": "g5", "min_swing": 30}).result

    assert result["error"] == "sin parciales"
    assert "min_swing" in result["suggestion"]


# ------------------------------------------------------------- señales semanales --


def _seed_weekly_signals_season(engine, season_id: int = 3) -> None:
    """Temporada nueva y aislada con un cambio de rol claro: Howard pasa de 12 a 24 minutos.

    5 partidos con estadísticas de asesor de baloncesto grandes (>= min_effect
    de `analytics.signals`) y sin varianza dentro de cada ventana, para que el
    contraste sea inequívoco sin depender de aleatoriedad ni de números
    mágicos difíciles de verificar a mano.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO seasons (id, label) VALUES (:id, 'Señales')"), {"id": season_id})
        for i in range(1, 9):  # línea base: 8 partidos a 12 minutos
            game_id = f"wkb{i}"
            conn.execute(
                text(
                    "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                    " game_date, home_score, away_score, pace) VALUES"
                    " (:id, :season_id, 1, 'bas', 'rm', :date, 80, 75, 70.0)"
                ),
                {"id": game_id, "season_id": season_id, "date": f"2026-09-{i:02d}"},
            )
            conn.execute(
                text(
                    "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                    " VALUES (:g, 'howard', 12.0, 6, 2, 1, 50.0)"
                ),
                {"g": game_id},
            )
        for i in range(1, 6):  # ventana reciente: 5 partidos a 24 minutos
            game_id = f"wkr{i}"
            conn.execute(
                text(
                    "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                    " game_date, home_score, away_score, pace) VALUES"
                    " (:id, :season_id, 1, 'bas', 'rm', :date, 80, 75, 70.0)"
                ),
                {"id": game_id, "season_id": season_id, "date": f"2026-10-{i:02d}"},
            )
            conn.execute(
                text(
                    "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                    " VALUES (:g, 'howard', 24.0, 14, 3, 2, 50.0)"
                ),
                {"g": game_id},
            )


def test_weekly_signals_tool_finds_the_minutes_role_change(engine, catalog):
    """El mismo motor que pinta `estado_equipo.py`, en JSON — un cambio de rol inequívoco pasa."""
    _seed_weekly_signals_season(engine)

    result = catalog.execute("1", "weekly_signals", {"team_id": "bas", "season_id": 3}).result

    signals = result["data"]["signals"]
    assert signals, "esperaba al menos la señal de minutos"
    minutes_signal = next(s for s in signals if s["metric"] == "minutes")
    assert minutes_signal["family"] == "player"
    assert minutes_signal["subject_name"] == "Marcus Howard"
    assert minutes_signal["effect"] == pytest.approx(12.0, abs=0.5)
    assert minutes_signal["p_value"] is not None and minutes_signal["p_value"] < 0.10
    assert minutes_signal["headline"]  # nunca vacío: la redacción por reglas siempre está
    assert "context" not in minutes_signal and "headline_template" not in minutes_signal
    assert result["data"]["window_games"] == 5
    assert any("calidad de rival" in warning for warning in result["meta"]["warnings"])


def test_weekly_signals_tool_never_errors_with_thin_data(catalog):
    """Con el seed base (5 partidos) no hay ni K=5 + 3 de referencia: respuesta correcta, no un fallo."""
    result = catalog.execute("1", "weekly_signals", {"team_id": "bas", "season_id": 1}).result

    assert "error" not in result
    assert isinstance(result["data"]["signals"], list)
    assert len(result["data"]["signals"]) <= 5


def test_weekly_signals_tool_is_registered_with_its_schema(catalog):
    assert "weekly_signals" in catalog.tools
    assert catalog.tools["weekly_signals"].parameters["required"] == ["team_id"]


# ----------------------------------------------------- similitud de jugadores --
# Propuesta 11 (`doc/features/propuestas/11_similitud_de_jugadores.md`). El
# seed base y la liga sintética de `conftest.py` no traen boxscore de jugador
# suficiente para el mínimo de la vista `player_percentiles` (5 partidos POR
# COMPETICIÓN) ni minutos suficientes para `MIN_MINUTES_RECOMMENDED` (500,
# inalcanzable en 5 partidos) — se siembra aquí una temporada propia con
# partidos de sobra para las dos cosas.
_SIMILARITY_SEASON_ID = 5
_SIMILARITY_N_GAMES = 15
_SIMILARITY_MINUTES_PER_GAME = 35.0


def _seed_similarity_games(conn, team_a: str, team_b: str, player_a: str, player_b: str, pts_a: int, pts_b: int) -> None:
    """`_SIMILARITY_N_GAMES` partidos entre `team_a`/`team_b`, con un jugador fijo por lado
    anotando siempre `pts_a`/`pts_b` puntos — suficiente para que `player_percentiles`
    (mínimo 5 partidos) y el filtro de minutos (mínimo 500) los acepten a los dos."""
    for team, player in ((team_a, player_a), (team_b, player_b)):
        conn.execute(
            text("INSERT OR IGNORE INTO teams (id, name, is_own_team) VALUES (:t, :t, 0)"), {"t": team}
        )
        conn.execute(
            text("INSERT OR IGNORE INTO players (id, team_id, name, number, position) VALUES (:p, :t, :p, 9, 'Base')"),
            {"p": player, "t": team},
        )
    for g in range(_SIMILARITY_N_GAMES):
        game_id = f"sim-{team_a}-{team_b}-{g}"
        conn.execute(
            text(
                "INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id,"
                " game_date, home_score, away_score, pace) VALUES"
                " (:g, :season, 1, :a, :b, :date, 80, 75, 70.0)"
            ),
            {"g": game_id, "season": _SIMILARITY_SEASON_ID, "a": team_a, "b": team_b, "date": f"2027-01-{g + 1:02d}"},
        )
        for player, pts in ((player_a, pts_a), (player_b, pts_b)):
            conn.execute(
                text(
                    "INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)"
                    " VALUES (:g, :p, :min, :pts, 4, 3, 50.0)"
                ),
                {"g": game_id, "p": player, "min": _SIMILARITY_MINUTES_PER_GAME, "pts": pts},
            )


def _seed_similarity_league(conn) -> None:
    """Tres equipos: `bas` se enfrenta a `sim-close` (perfil casi idéntico) pero NUNCA a
    `sim-far` (perfil muy distinto, y el que debe desaparecer con `only_faced`)."""
    conn.execute(
        text("INSERT INTO seasons (id, label) VALUES (:id, '2027-2028')"), {"id": _SIMILARITY_SEASON_ID}
    )
    _seed_similarity_games(conn, "bas", "sim-close", "bas_star", "close_star", pts_a=20, pts_b=19)
    _seed_similarity_games(conn, "sim-far-a", "sim-far", "far_a_star", "far_star", pts_a=20, pts_b=4)


@pytest.fixture()
def similarity_ctx(engine):
    """Contexto sobre la temporada sintética de similitud, mismo patrón que `league_ctx`."""
    from app.assistant.capabilities import probe

    with engine.begin() as conn:
        _seed_similarity_league(conn)
    return ToolContext(
        engine=engine, season_id=_SIMILARITY_SEASON_ID, own_team_id="bas", today=TODAY, capabilities=probe(engine),
    )


def test_similar_players_finds_the_closest_profile_and_explains_it(similarity_ctx):
    catalog = ToolCatalog(similarity_ctx)
    result = catalog.execute("1", "similar_players", {"player_id": "bas_star"}).result

    assert "error" not in result
    names = [row["name"] for row in result["data"]["similar"]]
    assert "close_star" in names
    # El perfil lejano (4 puntos frente a 20) tiene que quedar peor situado que el cercano.
    scores = {row["name"]: row["similarity_score"] for row in result["data"]["similar"]}
    if "far_star" in scores:
        assert scores["close_star"] > scores["far_star"]
    top = result["data"]["similar"][0]
    assert top["name"] == "close_star"
    assert top["closest"] and top["farthest"]
    assert result["data"]["method"] == "cosine"
    assert result["meta"]["source"]
    assert any("altura" in warning.lower() for warning in result["meta"]["warnings"])


def test_similar_players_only_faced_filters_out_teams_never_played(similarity_ctx):
    catalog = ToolCatalog(similarity_ctx)
    result = catalog.execute(
        "1", "similar_players", {"player_id": "bas_star", "only_faced": True}
    ).result

    assert "error" not in result
    names = {row["name"] for row in result["data"]["similar"]}
    assert "close_star" in names
    assert "far_star" not in names and "far_a_star" not in names


def test_similar_players_fails_usefully_for_a_player_without_enough_games(similarity_ctx):
    """`howard` existe (seed base) pero no tiene 5 partidos en UNA competición en esta temporada."""
    catalog = ToolCatalog(similarity_ctx)
    result = catalog.execute("1", "similar_players", {"player_id": "howard"}).result

    assert result["error"] == "sin datos"
    assert "suggestion" in result


def test_similar_players_rejects_an_unknown_method(similarity_ctx):
    catalog = ToolCatalog(similarity_ctx)
    result = catalog.execute(
        "1", "similar_players", {"player_id": "bas_star", "method": "manhattan"}
    ).result
    assert result["error"] == "parámetro inválido"


def test_similar_players_is_registered_with_its_schema(similarity_ctx):
    catalog = ToolCatalog(similarity_ctx)
    assert "similar_players" in catalog.tools
    assert catalog.tools["similar_players"].parameters["required"] == ["player_id"]
