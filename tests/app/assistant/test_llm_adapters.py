"""Tests de traducción de dialecto, con respuestas grabadas y sin red (§12.4).

Este fichero es el que mantiene honesto el puerto de LLM. El PoC usa un solo
proveedor, así que sería fácil dejar que el resto del sistema se fuera
acoplando a su dialecto sin que nada avisara — y descubrirlo el día de la
migración, cuando ya duele (§16.4).

La prueba clave es la última: **un `tool_calls` de OpenAI y un `tool_use` de
Anthropic entran y salen como la MISMA estructura interna**. Mientras eso se
cumpla, cambiar de proveedor es cambiar variables de entorno.

Ninguno de estos tests necesita los SDK instalados: las funciones de
traducción son puras y el import del SDK vive dentro del constructor de cada
cliente, no a nivel de módulo.
"""
import json

import pytest

from app.assistant.llm import anthropic as anthropic_adapter
from app.assistant.llm import openai_compat
from app.assistant.llm.base import LLMError, ToolCall, ToolSpec

TOOLS = [
    ToolSpec(
        name="player_game",
        description="Cómo jugó un jugador un partido.",
        parameters={"type": "object", "properties": {"player_id": {"type": "string"}}, "required": ["player_id"]},
    )
]

# Respuesta grabada de un endpoint compatible OpenAI pidiendo una herramienta.
OPENAI_TOOL_MESSAGE = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_abc123",
            "type": "function",
            "function": {"name": "player_game", "arguments": '{"player_id": "howard"}'},
        }
    ],
}

# La misma respuesta, en dialecto Anthropic (lista de bloques, `input` ya como
# objeto en vez de cadena JSON).
ANTHROPIC_CONTENT = [
    {"type": "text", "text": "Voy a mirarlo."},
    {"type": "tool_use", "id": "toolu_abc123", "name": "player_game", "input": {"player_id": "howard"}},
]


# ------------------------------------------------------------ dialecto OpenAI --


def test_openai_tools_use_the_function_wrapper(_=None):
    wire = openai_compat._to_wire_tools(TOOLS)
    assert wire[0]["type"] == "function"
    assert wire[0]["function"]["name"] == "player_game"
    assert wire[0]["function"]["parameters"]["required"] == ["player_id"]


def test_openai_puts_the_system_prompt_as_the_first_message():
    wire = openai_compat._to_wire_messages([{"role": "user", "content": "hola"}], "reglas")
    assert wire[0] == {"role": "system", "content": "reglas"}
    assert wire[1] == {"role": "user", "content": "hola"}


def test_openai_serializes_tool_calls_and_results():
    messages = [
        {"role": "user", "content": "¿y Howard?"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall(id="c1", name="player_game", arguments={"player_id": "howard"})]},
        {"role": "tool", "tool_call_id": "c1", "name": "player_game", "content": '{"data": 1}'},
    ]
    wire = openai_compat._to_wire_messages(messages, "reglas")

    assert wire[2]["tool_calls"][0]["function"]["arguments"] == '{"player_id": "howard"}'
    assert wire[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"data": 1}'}


def test_openai_parses_a_recorded_tool_call():
    text, calls = openai_compat._from_wire_message(OPENAI_TOOL_MESSAGE)
    assert text == ""
    assert calls == [ToolCall(id="call_abc123", name="player_game", arguments={"player_id": "howard"})]


def test_openai_reports_broken_json_instead_of_raising():
    """Un modelo pequeño emite JSON inválido de vez en cuando (§8.1)."""
    broken = {
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{player_id:"}}],
    }
    _, calls = openai_compat._from_wire_message(broken)
    assert calls[0].arguments == {}
    assert "JSON" in calls[0].arguments_error


def test_openai_rejects_arguments_that_are_not_an_object():
    _, calls = openai_compat._from_wire_message(
        {"tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "[1, 2]"}}]}
    )
    assert "objeto JSON" in calls[0].arguments_error


def test_openai_stream_deltas_are_reassembled_by_index():
    """Los `tool_calls` llegan troceados y solo el primer delta trae el `id`."""
    chunks = [
        {"choices": [{"delta": {"content": "Voy "}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "player_game", "arguments": '{"pla'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "player_form", "arguments": '{"pla'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'yer_id": "howard"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": 'yer_id": "kotsar"}'}}]}}]},
    ]
    streamed = []
    message = openai_compat._merge_stream_deltas(chunks, streamed.append)

    assert streamed == ["Voy "]
    assert [call["id"] for call in message["tool_calls"]] == ["c1", "c2"]
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"player_id": "howard"}
    assert json.loads(message["tool_calls"][1]["function"]["arguments"]) == {"player_id": "kotsar"}


@pytest.mark.parametrize(
    "status, expected",
    [
        (429, "Límite del proveedor alcanzado"),
        (401, "rechazó la clave"),
        (503, "no responde ahora mismo"),
    ],
)
def test_provider_errors_become_a_sentence_for_the_interface(status, expected):
    """Con capa gratuita el 429 es el fallo más probable del sistema (§8.5)."""
    error = Exception("boom")
    error.status_code = status
    translated = openai_compat.translate_error(error)

    assert isinstance(translated, LLMError)
    assert expected in str(translated)
    assert translated.retryable is (status in (429, 503))


# --------------------------------------------------------- dialecto Anthropic --


def test_anthropic_tools_use_input_schema():
    wire = anthropic_adapter.to_wire_tools(TOOLS)
    assert "input_schema" in wire[0] and "parameters" not in wire[0]


def test_anthropic_groups_consecutive_tool_results_in_one_user_message():
    """Es el equivalente exacto de la regla del bucle: los resultados paralelos,
    juntos. Repartirlos enseña al modelo a dejar de paralizar (§3.3)."""
    messages = [
        {"role": "user", "content": "compara"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                ToolCall(id="t1", name="player_averages", arguments={"player_id": "howard"}),
                ToolCall(id="t2", name="player_averages", arguments={"player_id": "kotsar"}),
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "player_averages", "content": "{}"},
        {"role": "tool", "tool_call_id": "t2", "name": "player_averages", "content": "{}"},
    ]
    wire = anthropic_adapter.to_wire_messages(messages)

    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert [block["type"] for block in wire[2]["content"]] == ["tool_result", "tool_result"]


def test_anthropic_parses_a_recorded_tool_use_block():
    text, calls = anthropic_adapter.from_wire_content(ANTHROPIC_CONTENT)
    assert text == "Voy a mirarlo."
    assert calls == [ToolCall(id="toolu_abc123", name="player_game", arguments={"player_id": "howard"})]


# ------------------------------------------------------- los dos, igual dentro --


def test_both_dialects_produce_the_same_internal_structure():
    """La prueba que mantiene honesto el puerto de LLM (§3.4, §16.4).

    Si esta se rompe, es que un dialecto se ha filtrado por encima de `llm/` y
    la próxima migración deja de ser media jornada.
    """
    _, from_openai = openai_compat._from_wire_message(OPENAI_TOOL_MESSAGE)
    _, from_anthropic = anthropic_adapter.from_wire_content(ANTHROPIC_CONTENT)

    assert from_openai[0].name == from_anthropic[0].name
    assert from_openai[0].arguments == from_anthropic[0].arguments
    assert from_openai[0].arguments_error is from_anthropic[0].arguments_error is None
