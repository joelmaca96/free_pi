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
DEFAULT_MAX_TOKENS_PER_TURN = 20_000

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
        max_tokens_per_turn: int = DEFAULT_MAX_TOKENS_PER_TURN,
    ):
        self.client = client
        self.catalog = catalog
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations or max_iterations_from_env()
        self.max_tokens_per_turn = max_tokens_per_turn

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

        for iteration in range(1, self.max_iterations + 1):
            turn.iterations = iteration
            if on_text_reset is not None:
                on_text_reset()
            response = self.client.chat(
                messages, self.catalog.specs(), system=self.system_prompt, on_text=on_text
            )
            spent_tokens += int(response.usage.get("total_tokens", 0) or 0)
            _accumulate_usage(turn.usage, response.usage)

            if not response.tool_calls:
                turn.text = response.text
                messages.append({"role": "assistant", "content": response.text})
                break

            messages.append(
                {"role": "assistant", "content": response.text, "tool_calls": response.tool_calls}
            )
            invocations = self._execute_all(response.tool_calls, on_tool_start, on_tool_end)
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
            turn.text = (
                "Me he quedado sin vueltas de herramienta antes de poder responder "
                f"({turn.stopped_reason}). Prueba a preguntarlo de forma más concreta."
            )
            messages.append({"role": "assistant", "content": turn.text})

        turn.messages = messages
        turn.unverified_numbers = verify_numbers(turn.text, turn.invocations)
        _log_sql_escapes(question, turn.invocations)
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


__all__ = ["Agent", "AgentTurn", "LLMError", "DEFAULT_MAX_ITERATIONS", "max_iterations_from_env"]
