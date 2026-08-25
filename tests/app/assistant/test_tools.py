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

from app.assistant.tools import ToolCatalog
from tests.app.assistant.conftest import LEAGUE_SEASON_ID, add_stints


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
    result = catalog.execute("1", "resolve_entity", {"query": "Howard", "kind": "player"}).result
    assert result["data"]["ambiguous"] is True
    assert len(result["data"]["candidates"]) == 2


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


def test_player_game_in_a_season_without_data_fails_with_a_suggestion(catalog):
    result = catalog.execute("1", "player_game", {"player_id": "howard", "season_id": 99}).result
    assert result["error"] == "sin datos"
    assert "temporada" in result["suggestion"]


def test_player_averages_cites_games_played(catalog):
    result = catalog.execute("1", "player_averages", {"player_id": "kotsar"}).result
    combined = next(row for row in result["data"] if row["competition"] == "Combinado")
    assert combined["gp"] == 5
    assert result["meta"]["gp"] == 5


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
