"""Tests de las capas que impiden inventar: capacidades, verificador y prompt (§7).

Las tres son deterministas y sin modelo, así que se prueban enteras offline.
La que más importa es el sondeo de capacidades: es lo que evita el peor modo
de fallo del proyecto —prometer un dato que `schema.sql` promete pero la base
de datos desplegada no tiene— y lo que hace que la promesa se encienda sola
el día que se reingiere.
"""
from sqlalchemy import text

from app.assistant.capabilities import probe
from app.assistant.prompt import build_system_prompt, stable_prefix_length
from app.assistant.tools.base import ToolInvocation
from app.assistant.verify import numbers_in_text, verify_numbers


# ------------------------------------------------------------- capacidades --


def test_probe_reports_what_the_seed_actually_has(engine):
    caps = probe(engine)

    assert caps.free_throws is False       # el seed no carga ftm/fta
    assert caps.key_events is True
    assert caps.narratives is True
    assert caps.lineup_stints is False     # tabla vacía hasta reingerir
    assert caps.counts["games"] > 0
    assert caps.date_range is not None


def test_a_column_that_exists_but_is_empty_is_not_a_capability(engine):
    """`ftm` existe en el esquema y está en NULL: prometerla sería el mismo
    error una capa más abajo (§7.3)."""
    caps = probe(engine)
    assert caps.free_throws is False

    with engine.begin() as conn:
        conn.execute(text("UPDATE player_game_stats SET ftm = 3, fta = 4 WHERE game_id = 'g5'"))

    assert probe(engine).free_throws is True  # se enciende sola al haber dato


def test_missing_capabilities_are_phrased_as_limits_the_model_can_quote(engine):
    gaps = probe(engine).missing_summary()
    assert any("tiros libres" in gap for gap in gaps)
    assert any("últimos minutos" in gap or "tramos de tiempo" in gap for gap in gaps)


def test_stints_turn_the_capability_on(engine):
    from tests.app.assistant.conftest import add_stints

    add_stints(engine, [("g5", "bas", 0.0, 600.0, 10, 8, 0, ["howard", "moneke", "codi", "kotsar", "lutse"])])
    assert probe(engine).lineup_stints is True


# ------------------------------------------------------------- verificador --


def _invocation(result: dict) -> ToolInvocation:
    return ToolInvocation(call_id="c1", name="t", arguments={}, result=result)


def test_numbers_backed_by_a_tool_result_pass():
    invocations = [_invocation({"data": {"pts": 21, "minutes": 20.29, "efg_pct": 83.3}})]
    assert verify_numbers("Hizo 21 puntos en 20.29 minutos con un 83.3% de eFG.", invocations) == []


def test_an_invented_number_is_reported():
    invocations = [_invocation({"data": {"pts": 21}})]
    assert verify_numbers("Hizo 21 puntos y 12 rebotes.", invocations) == ["12"]


def test_rounding_is_tolerated():
    """El modelo redondea al citar; exigir igualdad exacta marcaría como
    inventada una cifra correcta."""
    invocations = [_invocation({"data": {"efg_pct": 57.2}})]
    assert verify_numbers("Un 57% de eFG.", invocations) == []


def test_small_ordinary_numbers_are_not_chased():
    """"los 5 jugadores", "el segundo cuarto": perseguirlos llena el aviso de
    ruido hasta que deja de leerse."""
    assert verify_numbers("Los 5 jugadores del quinteto en el 2º cuarto.", []) == []


def test_dates_and_ids_are_not_treated_as_figures():
    invocations = [_invocation({"data": {"pts": 21}})]
    answer = "En el partido acb-104714 del 2026-05-03 hizo 21 puntos."
    assert verify_numbers(answer, invocations) == []


def test_numbers_nested_deep_in_the_result_still_count():
    invocations = [_invocation({"data": {"rows": [{"lineup": {"plus_minus": 35}}]}})]
    assert verify_numbers("Ese quinteto va +35.", invocations) == []


def test_an_answer_without_figures_has_nothing_to_verify():
    assert verify_numbers("No tengo ese dato.", []) == []


def test_clock_minutes_are_verified_whole_and_not_split():
    """Caso medido en vivo: la respuesta era correcta y el aviso saltaba igual.

    `player_game_stats.minutes` guarda 20.47 y el modelo escribe "20:28", que
    es como se escribe el tiempo de juego. Partido en dos, el "28" de los
    segundos no aparecía en ningún resultado y se marcaba como inventado.
    """
    invocations = [_invocation({"data": {"pts": 21, "minutes": 20.47}})]
    assert verify_numbers("Anotó 21 puntos en 20:28.", invocations) == []


def test_an_invented_minutage_still_fails():
    """El minutaje se comprueba entero, así que no se ha abierto un agujero."""
    invocations = [_invocation({"data": {"pts": 21, "minutes": 20.47}})]
    assert verify_numbers("Anotó 21 puntos en 31:15.", invocations) == ["31:15"]


def test_clock_minutes_written_by_the_tool_also_pass():
    """Si la herramienta ya devuelve '20:28', copiarlo tal cual cuadra."""
    invocations = [_invocation({"data": {"pts": 21, "minutes": "20:28"}})]
    assert verify_numbers("Anotó 21 puntos en 20:28.", invocations) == []


def test_a_european_score_is_not_read_as_minutage():
    """'94:58' no es un minutaje: si sus dos mitades cuadran, no se marca."""
    invocations = [_invocation({"data": {"home": 94, "away": 58}})]
    assert verify_numbers("Ganaron 94:58.", invocations) == []


def test_unverified_figures_keep_appearance_order():
    """El minutaje se escanea aparte, pero el aviso se lee en orden de lectura."""
    invocations = [_invocation({"data": {"pts": 21}})]
    assert verify_numbers("Hizo 12 puntos en 31:15 y 44 de valoración.", invocations) == [
        "12",
        "31:15",
        "44",
    ]


def test_numbers_in_text_reads_the_answer_with_the_verifier_rules():
    """El banco de pruebas (`tools/assistant_eval.py`) pregunta lo contrario que
    `verify_numbers`: no qué cifras sobran, sino si la respuesta cita la que
    debía. Tiene que leer el texto con LA MISMA regla, o el set dorado mediría
    con un criterio distinto del que usa el asistente en producción."""
    found = numbers_in_text("Anotó 21 puntos en 20:28 el 2026-05-03 (acb-104714).")
    assert 21.0 in found
    assert 20.47 in found  # el minutaje entero, no sus dos mitades sueltas
    assert 2026.0 not in found  # la fecha no es una cifra que verificar
    assert 104714.0 not in found  # ni el id del partido


def test_numbers_in_text_survives_an_empty_answer():
    """Un turno cortado por un tope puede no tener texto; el guion lo llama igual."""
    assert numbers_in_text("") == set()


# ------------------------------------------------------------------ prompt --


def test_the_volatile_block_goes_last(engine):
    """Orden = orden de caché (§8.6): un byte volátil intercalado invalida
    todo el prefijo que va detrás."""
    caps = probe(engine)
    first = build_system_prompt(caps, season_label="2025-2026", own_team="Baskonia", today="2026-03-01")
    second = build_system_prompt(caps, season_label="2025-2026", own_team="Baskonia", today="2026-03-02")

    length = stable_prefix_length(first)
    assert length == stable_prefix_length(second)
    assert first[:length] == second[:length]  # solo cambia el final
    assert first != second


def test_the_prompt_carries_the_schema_traps(engine):
    prompt = build_system_prompt(
        probe(engine), season_label="2025-2026", own_team="Baskonia", today="2026-03-01"
    )
    assert "players.team_id` es el equipo ACTUAL" in prompt
    assert "ESTIMACIÓN propia" in prompt
    assert "lineup_stints" in prompt  # la tarjeta de esquema se genera de schema.sql


def test_the_prompt_gives_the_id_of_every_competition(engine):
    """Sin el id en el prompt, la única forma de obtenerlo era `run_sql`, y eso
    es lo que el banco de pruebas vio hacer dos veces en una sola pasada. Las
    temporadas ya lo llevaban; esta es la mitad que faltaba."""
    prompt = build_system_prompt(
        probe(engine), season_label="2025-2026", own_team="Baskonia", today="2026-09-14"
    )
    assert "Competiciones: " in prompt
    for competition in probe(engine).competitions:
        assert f"{competition['name']} (id {competition['id']})" in prompt


def test_the_prompt_lists_what_cannot_be_answered(engine):
    prompt = build_system_prompt(
        probe(engine), season_label="2025-2026", own_team="Baskonia", today="2026-03-01"
    )
    assert "LO QUE ESTA BASE DE DATOS NO PUEDE CONTESTAR" in prompt
    assert "tiros libres" in prompt


def test_screen_context_is_injected_with_the_volatile_block(engine):
    prompt = build_system_prompt(
        probe(engine),
        season_label="2025-2026",
        own_team="Baskonia",
        today="2026-03-01",
        extra_context="viene del partido acb-104714",
    )
    assert prompt.index("acb-104714") > stable_prefix_length(prompt)


def test_the_prompt_stays_small_enough_for_a_32k_window(engine):
    """~3-4k tokens de prompt fijo, para que quepan historial y resultados (§8.6)."""
    prompt = build_system_prompt(
        probe(engine), season_label="2025-2026", own_team="Baskonia", today="2026-03-01"
    )
    assert len(prompt) < 20_000  # ~5k tokens como techo holgado
