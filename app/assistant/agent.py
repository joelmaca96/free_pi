"""El bucle de agente: petición -> herramientas -> petición, escrito aquí y no importado.

El SDK de Anthropic trae `tool_runner`, que haría esto por nosotros. Sería la
opción cómoda **si Anthropic fuese el único proveedor**, pero el PoC arranca
contra un endpoint gratuito y el proveedor tiene que poder cambiarse sin
reescribir el agente (§3.3). Un `tool_runner` acoplado al SDK obliga a
tirarlo entero el día que apuntas a otro sitio; este bucle son ochenta líneas
y sobrevive a cualquier adaptador que cumpla `LLMClient`.

Las trampas conocidas de un bucle así se evitan todas en este fichero, y cada
una tiene su motivo:

- **Los resultados de llamadas paralelas van TODOS en el mismo mensaje.**
  Repartirlos en mensajes distintos le enseña al modelo a dejar de
  paralelizar, y a partir de ahí cada pregunta cuesta el doble de vueltas.
- **Una herramienta que falla devuelve un resultado marcado como error**, no
  desaparece. Un `tool_call` sin su `tool_result` deja el historial
  inconsistente y varios proveedores lo rechazan de plano.
- **Tope de iteraciones y de tokens por turno** (§11.3). Con la app publicada
  por el túnel, un bucle sin tope es una factura o una cuota agotada.
"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .llm.base import LLMClient, LLMError, Message, TextSink, ToolCall
from .tools import ToolCatalog, ToolInvocation, serialize_result
from .verify import verify_numbers

logger = logging.getLogger(__name__)

#: Vueltas de herramienta como mucho. Seis cubre de sobra el caso más largo
#: que se ha visto (resolver dos entidades + perfil de las dos + comparar) y
#: corta en seco un bucle en el que el modelo se atasca reintentando.
DEFAULT_MAX_ITERATIONS = 6

#: Tokens por turno. No es un límite de coste hoy (el PoC no paga), es un
#: límite de cuota: es lo que evita que una pregunta rara se coma la cuota de
#: tokens/minuto del proveedor gratuito y deje al resto sin asistente.
#:
#: Se cuenta el `total_tokens` de cada vuelta, o sea incluyendo el prompt que
#: se REENVÍA entero cada vez. Eso es lo correcto (el proveedor cobra la
#: cuota de tokens/minuto igual), pero hace que el número se gaste mucho más
#: rápido de lo que parece: medido con el catálogo real (§ medición 2026-08),
#: el prefijo fijo son ~7.000 tokens (prompt de sistema ~1.6k + 28 esquemas de
#: herramienta ~3.6k), así que CADA vuelta cuesta ~7.400 tokens y apenas
#: crece. Con el valor viejo de 20.000 el tope saltaba en la tercera vuelta,
#: contradiciendo a `DEFAULT_MAX_ITERATIONS`, que promete seis: la respuesta
#: era un "no he podido" con las herramientas ya ejecutadas y pagadas.
#: 60.000 es lo que hace falta para que las seis vueltas quepan de verdad.
DEFAULT_MAX_TOKENS_PER_TURN = 60_000

#: Hilos para ejecutar llamadas independientes a la vez. Pocas y muy cortas
#: (SQLite local): más hilos no acelera nada y complica el rastreo.
_MAX_PARALLEL_TOOLS = 4


@dataclass
class AgentTurn:
    """Todo lo que produce un turno, listo para pintar y para guardar en el historial."""

    text: str = ""
    invocations: List[ToolInvocation] = field(default_factory=list)
    messages: List[Message] = field(default_factory=list)
    iterations: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    stopped_reason: Optional[str] = None
    #: Cifras de la respuesta que no aparecen en ningún `tool_result` de este
    #: turno (§7.2). No se bloquea la respuesta: se hace visible.
    unverified_numbers: List[str] = field(default_factory=list)

    @property
    def artifacts(self) -> List[Dict[str, Any]]:
        """Artefactos a pintar, en orden de llamada (§9.3)."""
        return [inv.artifact for inv in self.invocations if inv.artifact]


def max_iterations_from_env() -> int:
    raw = os.getenv("ASSISTANT_MAX_TOOL_ITERATIONS", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_ITERATIONS
    except ValueError:
        return DEFAULT_MAX_ITERATIONS


def max_tokens_per_turn_from_env() -> int:
    """Tope de tokens del turno, configurable como ya lo era el de vueltas.

    Los dos topes tienen que poder moverse juntos: subir las vueltas sin subir
    los tokens no da vueltas de más, solo cambia cuál de los dos frena.
    """
    raw = os.getenv("ASSISTANT_MAX_TOKENS_PER_TURN", "").strip()
    try:
        return max(1_000, int(raw)) if raw else DEFAULT_MAX_TOKENS_PER_TURN
    except ValueError:
        return DEFAULT_MAX_TOKENS_PER_TURN


class Agent:
    """Ata un `LLMClient` cualquiera con el catálogo de herramientas.

    No sabe qué proveedor hay debajo ni qué pinta la interfaz: recibe un
    cliente que cumple el `Protocol` y devuelve un `AgentTurn`. Eso es lo que
    permite probarlo entero con un cliente falso, sin red y en CI (§12.4).
    """

    def __init__(
        self,
        client: LLMClient,
        catalog: ToolCatalog,
        system_prompt: str,
        *,
        max_iterations: Optional[int] = None,
        max_tokens_per_turn: Optional[int] = None,
    ):
        self.client = client
        self.catalog = catalog
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations or max_iterations_from_env()
        self.max_tokens_per_turn = max_tokens_per_turn or max_tokens_per_turn_from_env()

    def run(
        self,
        question: str,
        history: Optional[List[Message]] = None,
        *,
        on_text: TextSink = None,
        on_text_reset=None,
        on_tool_start=None,
        on_tool_end=None,
    ) -> AgentTurn:
        """Responde una pregunta, llamando a las herramientas que hagan falta.

        Args:
            question: la pregunta del usuario, tal cual.
            history: mensajes de turnos anteriores en forma interna.
            on_text: callback de streaming del texto según llega (§9.2).
            on_text_reset: se llama al empezar cada vuelta. El modelo puede
                escribir texto ANTES de pedir una herramienta ("voy a mirar
                su último partido"); ese texto es un paso intermedio, no la
                respuesta, y sin este aviso la página lo dejaría pegado
                delante de la respuesta final.
            on_tool_start: `fn(name, arguments)` justo antes de ejecutar —
                la página abre con esto el `st.status` de la traza.
            on_tool_end: `fn(ToolInvocation)` al terminar cada herramienta.

        Returns:
            `AgentTurn` con el texto, la traza, los artefactos y los mensajes
            nuevos (para encadenar el siguiente turno).

        Raises:
            LLMError: si el proveedor falla. Se deja subir a propósito: la
                página sabe contarlo en una frase y el agente no tiene nada
                mejor que hacer con ello.
        """
        messages: List[Message] = list(history or [])
        messages.append({"role": "user", "content": question})

        turn = AgentTurn()
        spent_tokens = 0
        turn_started = time.monotonic()

        for iteration in range(1, self.max_iterations + 1):
            turn.iterations = iteration
            if on_text_reset is not None:
                on_text_reset()
            llm_started = time.monotonic()
            response = self.client.chat(
                messages, self.catalog.specs(), system=self.system_prompt, on_text=on_text
            )
            llm_elapsed = time.monotonic() - llm_started
            spent_tokens += int(response.usage.get("total_tokens", 0) or 0)
            _accumulate_usage(turn.usage, response.usage)
            logger.info(
                "assistant.timing | llm vuelta=%d/%d | %.2fs | modelo=%s | tool_calls=%d | "
                "stop=%s | tokens=%s",
                iteration,
                self.max_iterations,
                llm_elapsed,
                self.client.label,
                len(response.tool_calls),
                response.stop_reason,
                response.usage.get("total_tokens"),
            )

            if not response.tool_calls:
                turn.text = response.text
                messages.append({"role": "assistant", "content": response.text})
                break

            # `provider_state` viaja pegado al mensaje sin que el agente mire
            # qué hay dentro: es el trozo opaco que algún proveedor necesita
            # recibir intacto en la vuelta siguiente (ver `LLMResponse`).
            assistant_message: Message = {
                "role": "assistant",
                "content": response.text,
                "tool_calls": response.tool_calls,
            }
            if response.provider_state is not None:
                assistant_message["provider_state"] = response.provider_state
            messages.append(assistant_message)
            tools_started = time.monotonic()
            invocations = self._execute_all(response.tool_calls, on_tool_start, on_tool_end)
            logger.info(
                "assistant.timing | tools vuelta=%d/%d | %.2fs | n=%d | %s",
                iteration,
                self.max_iterations,
                time.monotonic() - tools_started,
                len(invocations),
                ", ".join(inv.name for inv in invocations),
            )
            turn.invocations.extend(invocations)
            # TODOS los resultados, en el mismo bloque y en el mismo orden en
            # que se pidieron. Ver docstring del módulo.
            for invocation in invocations:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": invocation.call_id,
                        "name": invocation.name,
                        "content": serialize_result(invocation.result),
                    }
                )

            if spent_tokens > self.max_tokens_per_turn:
                turn.stopped_reason = "tope de tokens del turno"
                break
        else:
            turn.stopped_reason = f"tope de {self.max_iterations} vueltas de herramienta"

        if turn.stopped_reason and not turn.text:
            # Los dos topes se paran por motivos distintos y se arreglan de
            # forma distinta: darles el mismo consejo ("pregúntalo más
            # concreto") manda a reformular la pregunta cuando lo que hay que
            # tocar es la configuración, y eso son diez intentos perdidos.
            turn.text = _stopped_message(turn.stopped_reason)
            messages.append({"role": "assistant", "content": turn.text})

        turn.messages = messages
        turn.unverified_numbers = verify_numbers(turn.text, turn.invocations)
        _log_sql_escapes(question, turn.invocations)
        logger.info(
            "assistant.timing | turno completo | %.2fs | %d vueltas | parada=%s | pregunta=%r",
            time.monotonic() - turn_started,
            turn.iterations,
            turn.stopped_reason or "-",
            question,
        )
        return turn

    def _execute_all(self, calls: List[ToolCall], on_tool_start, on_tool_end) -> List[ToolInvocation]:
        """Ejecuta las llamadas de una vuelta, en paralelo, conservando el orden.

        El orden importa aunque la ejecución no sea secuencial: es el orden en
        que se pintan la traza y los artefactos, y el orden en que el modelo
        pidió las cosas es el que tiene sentido para quien lee.
        """
        for call in calls:
            if on_tool_start is not None:
                on_tool_start(call.name, call.arguments)

        def _run(call: ToolCall) -> ToolInvocation:
            if call.arguments_error:
                # El modelo emitió JSON inválido. Se le devuelve como error de
                # herramienta para que reintente, en vez de tumbar el turno.
                return self.catalog.invalid_arguments(call.id, call.name, call.arguments_error)
            return self.catalog.execute(call.id, call.name, call.arguments)

        if len(calls) == 1:
            invocations = [_run(calls[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_TOOLS, len(calls))) as pool:
                invocations = list(pool.map(_run, calls))

        for invocation in invocations:
            if on_tool_end is not None:
                on_tool_end(invocation)
        return invocations


def _stopped_message(reason: str) -> str:
    """Qué contarle al usuario cuando el turno se corta por un tope."""
    if "tokens" in reason:
        return (
            "La pregunta ha consumido el tope de tokens del turno antes de que pudiera "
            "redactar la respuesta. Prueba a acotarla (menos jugadores, menos partidos), "
            "o sube ASSISTANT_MAX_TOKENS_PER_TURN en el .env si tu proveedor te lo permite."
        )
    return (
        f"Me he quedado sin vueltas de herramienta antes de poder responder ({reason}). "
        "Prueba a preguntarlo de forma más concreta, o sube ASSISTANT_MAX_TOOL_ITERATIONS."
    )


def _accumulate_usage(total: Dict[str, int], usage: Dict[str, Any]) -> None:
    """Suma el consumo de cada vuelta. Claves distintas por proveedor: se suma lo que haya."""
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            total[key] = int(total.get(key, 0) + value)


def _log_sql_escapes(question: str, invocations: List[ToolInvocation]) -> None:
    """Registra cada `run_sql` junto a la pregunta que lo originó (§4.6).

    Ese registro es la lista priorizada de qué herramienta propia falta
    escribir: si una consulta libre se repite, es que hay un corte del dato
    que merece SQL fijo y probado en vez de improvisado.
    """
    for invocation in invocations:
        if invocation.name == "run_sql":
            logger.info(
                "run_sql | pregunta=%r | proposito=%r | sql=%r | error=%s",
                question,
                invocation.arguments.get("purpose"),
                invocation.arguments.get("sql"),
                invocation.error,
            )


__all__ = [
    "Agent",
    "AgentTurn",
    "LLMError",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_TOKENS_PER_TURN",
    "max_iterations_from_env",
    "max_tokens_per_turn_from_env",
]
