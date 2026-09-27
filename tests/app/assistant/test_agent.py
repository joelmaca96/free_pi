"""Tests del bucle de agente con un `LLMClient` falso (§12.4).

El falso implementa el MISMO `Protocol` que los adaptadores reales, así que
estos tests no caducan al cambiar de proveedor: es exactamente la propiedad
que hace barata la migración de §16 y la única forma de comprobar el bucle
sin red y en CI.

Lo que se prueba es el enlazado (que los `tool_result` vuelvan bien atados a
sus `tool_call`), los topes, y que un fallo de herramienta no tumbe el turno.
"""
from typing import List

import pytest

from app.assistant.agent import Agent
from app.assistant.llm.base import LLMError, LLMResponse, ToolCall
from app.assistant.tools import ToolCatalog


class FakeLLM:
    """Reproduce una transcripción grabada, turno a turno.

    Guarda además lo que se le pasó en cada llamada: es la única forma de
    comprobar desde fuera que el historial se construye como debe.
    """

    label = "falso · guion"

    def __init__(self, script: List[LLMResponse]):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools, *, system, on_text=None):
        self.calls.append({"messages": list(messages), "tools": tools, "system": system})
        response = self.script.pop(0) if self.script else LLMResponse(text="(fin del guion)")
        if on_text is not None and response.text:
            on_text(response.text)
        return response


def _agent(ctx, script, **kwargs) -> Agent:
    return Agent(FakeLLM(script), ToolCatalog(ctx), "prompt de prueba", **kwargs)


def test_a_question_without_tools_returns_the_text(ctx):
    agent = _agent(ctx, [LLMResponse(text="No tengo ese dato.")])
    turn = agent.run("¿Quién ganó la Euroliga de 1998?")

    assert turn.text == "No tengo ese dato."
    assert turn.invocations == []
    assert turn.iterations == 1


def test_a_tool_call_is_executed_and_its_result_goes_back_to_the_model(ctx):
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_game", arguments={"player_id": "howard"})]),
            LLMResponse(text="Howard hizo 19 puntos en 31.2 minutos."),
        ],
    )
    turn = agent.run("¿Qué tal jugó Howard su último partido?")

    assert turn.text.startswith("Howard hizo 19")
    assert [inv.name for inv in turn.invocations] == ["player_game"]
    assert turn.iterations == 2

    # El resultado vuelve como mensaje de rol `tool` atado a su `tool_call_id`.
    second_request = agent.client.calls[1]["messages"]
    tool_message = next(m for m in second_request if m["role"] == "tool")
    assert tool_message["tool_call_id"] == "c1"
    assert '"pts": 19' in tool_message["content"]


def test_parallel_calls_all_come_back_in_the_same_batch(ctx):
    """Repartirlos en mensajes distintos enseña al modelo a dejar de paralelizar."""
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="player_averages", arguments={"player_id": "howard"}),
                    ToolCall(id="c2", name="player_averages", arguments={"player_id": "kotsar"}),
                ]
            ),
            LLMResponse(text="Comparados."),
        ],
    )
    turn = agent.run("Compara a Howard y Kotsar")

    assert len(turn.invocations) == 2
    # Las dos se ejecutan de verdad (en hilos distintos): si el fixture no
    # aguantara la concurrencia, esto saldría como error de herramienta y el
    # resto de aserciones pasaría igual sin haber probado nada.
    assert not any(inv.error for inv in turn.invocations)
    messages = agent.client.calls[1]["messages"]
    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    # Y van justo detrás del mensaje del asistente que las pidió, sin nada en medio.
    assistant_index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
    assert [m["role"] for m in messages[assistant_index + 1 :]] == ["tool", "tool"]


def test_a_failing_tool_comes_back_as_an_error_result_not_as_a_hole(ctx):
    """Un `tool_call` sin su `tool_result` deja el historial inconsistente."""
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_game", arguments={"player_id": "nadie"})]),
            LLMResponse(text="No encuentro a ese jugador."),
        ],
    )
    turn = agent.run("¿Qué tal jugó Nadie?")

    assert turn.invocations[0].error
    tool_message = next(m for m in agent.client.calls[1]["messages"] if m["role"] == "tool")
    assert '"error"' in tool_message["content"]


def test_unparseable_arguments_come_back_as_a_tool_error(ctx):
    """JSON inválido del modelo no puede tumbar el turno (§8.1)."""
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="player_game", arguments={}, arguments_error="JSON roto")
                ]
            ),
            LLMResponse(text="Lo reintento."),
        ],
    )
    turn = agent.run("x")

    assert turn.invocations[0].result["error"] == "argumentos ilegibles"
    assert turn.invocations[0].name == "player_game"


def test_the_iteration_cap_stops_a_model_stuck_in_a_loop(ctx):
    stuck = [
        LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name="get_context", arguments={})])
        for i in range(10)
    ]
    agent = _agent(ctx, stuck, max_iterations=3)
    turn = agent.run("dime algo")

    assert turn.iterations == 3
    assert "tope de 3 vueltas" in turn.stopped_reason
    assert "sin vueltas" in turn.text  # el usuario ve una frase, no un silencio


def test_the_token_cap_stops_the_turn(ctx):
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="get_context", arguments={})],
                usage={"total_tokens": 99_999},
            ),
            LLMResponse(text="Con lo consultado: hay 737 partidos cargados."),
        ],
        max_tokens_per_turn=1000,
    )
    turn = agent.run("dime algo")

    assert turn.stopped_reason == "tope de tokens del turno"
    assert turn.iterations == 1  # no se empieza otra vuelta de herramienta


def test_reaching_a_cap_redacts_with_what_was_already_consulted(ctx):
    """El turno agotado NO tira lo ya pagado.

    Caso real (set dorado, 2026-09-14): la pregunta del pulgar abajo traía doce
    resultados de herramienta correctos y 60.761 tokens gastados, y el usuario
    recibía "me he quedado sin tokens". Coste completo y valor cero."""
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="get_context", arguments={})],
                usage={"total_tokens": 99_999},
            ),
            LLMResponse(text="Con lo que tengo: 737 partidos cargados.", usage={"total_tokens": 500}),
        ],
        max_tokens_per_turn=1000,
    )
    turn = agent.run("dime algo")

    assert turn.text == "Con lo que tengo: 737 partidos cargados."
    assert turn.wrapped_up is True
    # `stopped_reason` NO se borra: el presupuesto se quedó corto igual, y eso
    # sigue siendo algo que mirar aunque el usuario haya recibido respuesta.
    assert turn.stopped_reason == "tope de tokens del turno"
    # El gasto del rescate se suma: esconderlo falsearía la métrica de tokens.
    assert turn.usage["total_tokens"] == 100_499


def test_the_rescue_round_is_sent_without_tools(ctx):
    """Sin herramientas es lo que impide que el rescate sea otra vuelta de bucle."""
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="get_context", arguments={})],
                usage={"total_tokens": 99_999},
            ),
            LLMResponse(text="Respuesta con lo que hay."),
        ],
        max_tokens_per_turn=1000,
    )
    agent.run("dime algo")

    last = agent.client.calls[-1]
    assert last["tools"] == []
    assert "se ha agotado el presupuesto" in last["system"]
    assert last["system"].startswith("prompt de prueba")  # el aviso va al final


def test_the_rescue_does_not_invent_a_message_in_the_history(ctx):
    """`messages` es el historial del turno siguiente: un mensaje inventado por
    el agente se quedaría ahí para siempre. Por eso el aviso va en el prompt de
    sistema y no como mensaje nuevo."""
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="get_context", arguments={})],
                usage={"total_tokens": 99_999},
            ),
            LLMResponse(text="Respuesta con lo que hay."),
        ],
        max_tokens_per_turn=1000,
    )
    turn = agent.run("dime algo")

    assert [m["role"] for m in turn.messages] == ["user", "assistant", "tool", "assistant"]
    assert turn.messages[-1]["content"] == "Respuesta con lo que hay."


def test_if_the_rescue_also_fails_the_user_still_gets_the_cap_message(ctx):
    """El rescate no puede convertir un turno agotado en una excepción nueva."""

    class FailingRescue(FakeLLM):
        def chat(self, messages, tools, *, system, on_text=None):
            if not tools:  # la vuelta de rescate
                raise LLMError("el proveedor no responde", retryable=True)
            return super().chat(messages, tools, system=system, on_text=on_text)

    agent = Agent(
        FailingRescue(
            [
                LLMResponse(
                    tool_calls=[ToolCall(id="c1", name="get_context", arguments={})],
                    usage={"total_tokens": 99_999},
                )
            ]
        ),
        ToolCatalog(ctx),
        "prompt de prueba",
        max_tokens_per_turn=1000,
    )
    turn = agent.run("dime algo")

    assert turn.wrapped_up is False
    assert "tope de tokens" in turn.text
    assert "ASSISTANT_MAX_TOKENS_PER_TURN" in turn.text


def test_history_is_carried_between_turns(ctx):
    agent = _agent(ctx, [LLMResponse(text="Primera.")])
    first = agent.run("¿Y Howard?")

    agent.client.script.append(LLMResponse(text="Segunda."))
    second = agent.run("¿y en Euroliga?", first.messages)

    roles = [m["role"] for m in second.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert second.messages[0]["content"] == "¿Y Howard?"


def test_streaming_resets_between_iterations(ctx):
    """El texto que el modelo escribe ANTES de pedir una herramienta es un paso
    intermedio, no la respuesta: la página tiene que poder descartarlo."""
    agent = _agent(
        ctx,
        [
            LLMResponse(
                text="Voy a mirarlo...",
                tool_calls=[ToolCall(id="c1", name="get_context", arguments={})],
            ),
            LLMResponse(text="Ya está."),
        ],
    )
    pieces, resets = [], []
    agent.run("x", on_text=pieces.append, on_text_reset=lambda: resets.append(len(pieces)))

    assert pieces == ["Voy a mirarlo...", "Ya está."]
    assert resets == [0, 1]  # se avisa antes de cada vuelta


def test_the_trace_callbacks_see_every_tool(ctx):
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_averages", arguments={"player_id": "howard"})]),
            LLMResponse(text="listo"),
        ],
    )
    started, ended = [], []
    agent.run("x", on_tool_start=lambda name, args: started.append(name), on_tool_end=ended.append)

    assert started == ["player_averages"]
    assert ended[0].summary  # una línea legible para el `st.status` de la traza


def test_unverified_numbers_are_reported_but_do_not_block(ctx):
    """El verificador marca, no bloquea (§7.2)."""
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_game", arguments={"player_id": "howard"})]),
            LLMResponse(text="Hizo 19 puntos y capturó 47 rebotes."),
        ],
    )
    turn = agent.run("x")

    assert turn.text.endswith("47 rebotes.")  # la respuesta llega entera
    assert turn.unverified_numbers == ["47"]


def test_artifacts_are_collected_in_call_order(ctx):
    agent = _agent(
        ctx,
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="player_game", arguments={"player_id": "howard"}),
                    ToolCall(id="c2", name="player_averages", arguments={"player_id": "howard"}),
                ]
            ),
            LLMResponse(text="listo"),
        ],
    )
    turn = agent.run("x")
    assert [artifact["type"] for artifact in turn.artifacts] == ["shot_chart", "table"]


def test_a_tool_without_artifact_produces_none(ctx):
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_form", arguments={"player_id": "howard"})]),
            LLMResponse(text="listo"),
        ],
    )
    assert _run_artifacts(agent) == []


def _run_artifacts(agent: Agent):
    return agent.run("x").artifacts


# ---------------------------------------------------------------------------
# Llamadas repetidas. Medido en vivo con la pregunta del clutch: cuatro
# `clutch_lineups` y dos `team_lineups` en un mismo turno, seis vueltas
# agotadas y 70.034 tokens para llegar a una respuesta correcta.
# ---------------------------------------------------------------------------


def _repeat_script(name: str, arguments: dict) -> List[LLMResponse]:
    """El modelo pide dos veces exactamente lo mismo y luego contesta."""
    return [
        LLMResponse(tool_calls=[ToolCall(id="c1", name=name, arguments=dict(arguments))]),
        LLMResponse(tool_calls=[ToolCall(id="c2", name=name, arguments=dict(arguments))]),
        LLMResponse(text="listo"),
    ]


def test_an_identical_call_is_not_executed_twice(ctx):
    agent = _agent(ctx, _repeat_script("player_averages", {"player_id": "howard"}))
    turn = agent.run("x")

    # Las dos llamadas siguen en la traza: que el modelo insista es información.
    assert [inv.name for inv in turn.invocations] == ["player_averages", "player_averages"]
    assert turn.invocations[0].result["data"] == turn.invocations[1].result["data"]
    assert "repetida" in turn.invocations[1].summary
    assert "repeated_call" not in (turn.invocations[0].result.get("meta") or {})


def test_the_repeated_result_tells_the_model_to_stop_asking(ctx):
    """Sin el aviso, devolver lo mismo en silencio invita a pedirlo otra vez."""
    agent = _agent(ctx, _repeat_script("player_averages", {"player_id": "howard"}))
    agent.run("x")

    tool_message = [m for m in agent.client.calls[2]["messages"] if m["role"] == "tool"][-1]
    assert tool_message["tool_call_id"] == "c2"
    assert "no vuelvas a pedirlo" in tool_message["content"].lower()


def test_a_repeated_call_does_not_paint_its_artifact_twice(ctx):
    agent = _agent(ctx, _repeat_script("player_game", {"player_id": "howard"}))
    turn = agent.run("x")

    assert [artifact["type"] for artifact in turn.artifacts] == ["shot_chart"]


def test_different_arguments_are_not_the_same_call(ctx):
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_averages", arguments={"player_id": "howard"})]),
            LLMResponse(tool_calls=[ToolCall(id="c2", name="player_averages", arguments={"player_id": "kotsar"})]),
            LLMResponse(text="listo"),
        ],
    )
    turn = agent.run("x")

    assert all("repetida" not in inv.summary for inv in turn.invocations)
    assert turn.invocations[0].result["data"] != turn.invocations[1].result["data"]


def test_a_failed_call_is_retried_rather_than_served_from_the_cache(ctx):
    """Un error tiene que volver tal cual: su `suggestion` es lo que hace reaccionar."""
    agent = _agent(ctx, _repeat_script("player_averages", {"player_id": "no-existe"}))
    turn = agent.run("x")

    assert all(inv.error for inv in turn.invocations)
    assert all("repetida" not in inv.summary for inv in turn.invocations)


def test_the_cache_does_not_leak_between_turns(ctx):
    """"El mismo turno" es el alcance: otra pregunta vuelve a consultar."""
    agent = _agent(
        ctx,
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="player_averages", arguments={"player_id": "howard"})]),
            LLMResponse(text="uno"),
            LLMResponse(tool_calls=[ToolCall(id="c2", name="player_averages", arguments={"player_id": "howard"})]),
            LLMResponse(text="dos"),
        ],
    )
    first = agent.run("x")
    second = agent.run("y", first.messages)

    assert "repetida" not in second.invocations[0].summary
