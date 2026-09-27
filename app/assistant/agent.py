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
- **Llegar al tope no tira lo ya consultado.** Antes de rendirse se pide una
  última redacción SIN herramientas con todo lo que ya hay en el historial.
  Medido con el set dorado: la pregunta del pulgar abajo traía doce
  resultados de herramienta correctos y 60.761 tokens gastados, y el usuario
  recibía "me he quedado sin tokens". Coste completo y valor cero es el peor
  final posible de un turno.
- **Una llamada idéntica no se ejecuta dos veces en el mismo turno.** Medido
  en vivo con la pregunta del clutch: seis vueltas, nueve llamadas, cuatro de
  ellas `clutch_lineups` y dos `team_lineups`, 70.034 tokens para llegar a una
  respuesta correcta. Cuatro veces la misma herramienta no es exploración, es
  el modelo atascado — y cada reintento vuelve a ejecutar el SQL y a meter el
  resultado entero en el contexto. Se le devuelve el resultado ya calculado
  **diciéndole que se repite**, que es la parte que rompe el bucle: sin el
  aviso vuelve a pedirlo igual.
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
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
#: rápido de lo que parece. Es un valor que ha tenido que subir DOS veces por
#: el mismo motivo, y la historia importa porque explica cómo se detecta:
#: con 20.000 el tope saltaba en la tercera vuelta y con 60.000 en la cuarta,
#: las dos veces contradiciendo a `DEFAULT_MAX_ITERATIONS`, que promete seis,
#: y las dos veces con el mismo síntoma — un "no he podido" con las
#: herramientas ya ejecutadas y pagadas.
#:
#: MEDICIÓN DEL PREFIJO FIJO (rehacerla al añadir herramientas — la de agosto
#: se quedó vieja sin que nadie lo viera, que es el motivo de que esta lleve
#: fecha y receta):
#:
#:   2026-08     28 herramientas  prompt ~1.6k + esquemas ~3.6k = ~7.000/vuelta
#:   2026-09-12  42 herramientas  prompt ~2.7k + esquemas ~6.5k = ~9.100/vuelta
#:   2026-09-14  42 herramientas  prompt 2.680 + esquemas 6.473 = 9.153/vuelta
#:
#: Los esquemas casi doblaron en un mes, y son la parte que crece. Con ~9.150
#: por vuelta, seis vueltas son 54.918 SOLO de prefijo: con el tope en 60.000
#: quedaban ~5.000 para todos los resultados de herramienta de las seis
#: vueltas, o sea nada. Los dos topes se contradecían —`DEFAULT_MAX_ITERATIONS`
#: promete seis vueltas y este cortaba en la cuarta—, que es exactamente el
#: fallo descrito arriba para el valor viejo de 20.000, repetido un escalón más
#: arriba. Medido con el set dorado el 2026-09-14: la pregunta del pulgar abajo
#: gastó 60.761 tokens en cuatro vueltas (~15.200 por vuelta, o sea ~6.000 de
#: resultados sobre el prefijo). Seis vueltas a ese ritmo son ~91.000, y de ahí
#: sale el valor de hoy. Para rehacer la medición:
#:
#:   specs = ToolCatalog(ctx).specs()
#:   len(json.dumps([s.__dict__ for s in specs], ensure_ascii=False)) / 3.6
#:
#: (3,6 caracteres por token es la regla de bolsillo para español; sirve para
#: ver la tendencia, no para facturar.)
#:
#: Subirlo NO abre la puerta a un bucle: quien corta un bucle es
#: `DEFAULT_MAX_ITERATIONS` (y desde el 2026-09-12, la memoización de llamadas
#: repetidas). Este tope es el cinturón, no el freno.
DEFAULT_MAX_TOKENS_PER_TURN = 90_000

#: Hilos para ejecutar llamadas independientes a la vez. Pocas y muy cortas
#: (SQLite local): más hilos no acelera nada y complica el rastreo.
_MAX_PARALLEL_TOOLS = 4

#: Lo que se le dice al modelo en la vuelta de rescate (ver `Agent._wrap_up`).
#: Se insiste en que NO pida nada más y en que diga qué se queda fuera: una
#: respuesta parcial que no avisa de que es parcial es peor que el mensaje de
#: tope, porque el usuario no tiene forma de saber que le falta la mitad.
_WRAP_UP_INSTRUCTION = (
    "AVISO: se ha agotado el presupuesto de este turno. NO pidas más herramientas —no hay— y "
    "no digas que no puedes responder. Redacta la mejor respuesta posible SOLO con los "
    "resultados de herramienta que ya tienes en esta conversación, respetando las reglas de "
    "siempre (toda cifra sale de un resultado, nada de memoria propia). Si la pregunta tenía "
    "varias partes y alguna se queda sin contestar, dilo en una frase al final."
)


@dataclass
class AgentTurn:
    """Todo lo que produce un turno, listo para pintar y para guardar en el historial."""

    text: str = ""
    invocations: List[ToolInvocation] = field(default_factory=list)
    messages: List[Message] = field(default_factory=list)
    iterations: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    stopped_reason: Optional[str] = None
    #: El turno llegó a un tope PERO se rescató redactando con lo ya
    #: consultado (ver `Agent._wrap_up`). Se distingue de `stopped_reason` a
    #: secas porque son dos cosas distintas para quien mide: `stopped_reason`
    #: dice que el presupuesto se quedó corto —sigue siendo algo que mirar—, y
    #: esto dice si el usuario recibió una respuesta o una disculpa.
    wrapped_up: bool = False
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
        # Resultados ya calculados EN ESTE TURNO. Vive en `run` y no en el
        # catálogo a propósito: "el mismo turno" es exactamente el alcance en
        # el que un resultado no puede haber cambiado, y un catálogo
        # reutilizado entre preguntas serviría datos viejos cuando la ingesta
        # (que corre por su cuenta) recarga la base de datos.
        tool_cache: Dict[str, ToolInvocation] = {}

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
                "stop=%s | tokens=%s | cacheados=%s",
                iteration,
                self.max_iterations,
                llm_elapsed,
                self.client.label,
                len(response.tool_calls),
                response.stop_reason,
                response.usage.get("total_tokens"),
                # Del prefijo fijo (~9.100 tokens por vuelta, ver
                # DEFAULT_MAX_TOKENS_PER_TURN), cuántos ha servido el
                # proveedor de su caché en vez de volver a procesarlos. Un
                # cero sostenido aquí significa que el orden del prompt no
                # está sirviendo para nada con este proveedor.
                response.usage.get("cached_tokens")
                or response.usage.get("cache_read_input_tokens"),
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
            invocations = self._execute_all(
                response.tool_calls, on_tool_start, on_tool_end, tool_cache
            )
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
            # Última oportunidad antes de rendirse: pedirle que REDACTE con lo
            # que ya tiene, sin herramientas.
            #
            # Medido con el set dorado: la pregunta del pulgar abajo (el
            # partido contra el Bilbao) llamó a doce herramientas correctas,
            # gastó 60.761 tokens y el usuario recibió un "me he quedado sin
            # tokens". Todo el dato estaba ya en el historial, pagado y bien
            # traído; solo faltaba escribirlo. Ese es el peor final posible:
            # coste completo, valor cero, y un usuario que vuelve a preguntar
            # lo mismo y lo paga otra vez.
            #
            # Se manda SIN herramientas a propósito: es lo que impide que el
            # rescate se convierta en otra vuelta de bucle. Y el aviso va en el
            # prompt de sistema, no como mensaje nuevo, porque `messages` se
            # devuelve como historial del turno siguiente y un mensaje
            # inventado por el agente se quedaría ahí para siempre.
            turn.text = self._wrap_up(messages, turn, on_text, on_text_reset)
            if turn.text:
                turn.wrapped_up = True
            else:
                # Los dos topes se paran por motivos distintos y se arreglan de
                # forma distinta: darles el mismo consejo ("pregúntalo más
                # concreto") manda a reformular la pregunta cuando lo que hay
                # que tocar es la configuración, y eso son diez intentos
                # perdidos.
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

    def _wrap_up(self, messages, turn: "AgentTurn", on_text, on_text_reset) -> str:
        """Una última vuelta SIN herramientas, para redactar con lo ya consultado.

        Devuelve el texto, o `""` si no se puede (el proveedor falla, o no
        escribe nada). Ese caso cae en el mensaje de tope de siempre: el
        rescate no puede convertir un turno agotado en una excepción nueva.

        El consumo de esta vuelta se suma a `turn.usage` como cualquier otra
        —es gasto real y esconderlo falsearía la métrica de tokens del banco
        de pruebas—, pero NO se comprueba contra el tope: el tope existe para
        cortar un bucle, y esto es exactamente lo contrario de un bucle.

        AL MIGRAR DE PROVEEDOR (§16.3), comprobar esto con el set dorado: se
        manda una conversación que YA contiene `tool_use`/`tool_result` pero
        sin declarar herramientas, y no todos los dialectos lo aceptan igual
        (el de Anthropic es tiquismiquis con eso). Si el proveedor lo rechaza
        no se rompe nada —cae en el `except` y el usuario ve el mensaje de
        tope de siempre, que es lo que veía antes—, pero se pierde el rescate
        sin que nadie se entere. De ahí que se registre en el log.
        """
        if on_text_reset is not None:
            on_text_reset()
        system = f"{self.system_prompt}\n\n{_WRAP_UP_INSTRUCTION}"
        try:
            response = self.client.chat(messages, [], system=system, on_text=on_text)
        except LLMError as exc:
            logger.warning("assistant.wrap_up | el rescate falló (%s)", exc)
            return ""
        _accumulate_usage(turn.usage, response.usage)
        logger.info(
            "assistant.timing | rescate sin herramientas | parada=%s | tokens=%s",
            turn.stopped_reason,
            response.usage.get("total_tokens"),
        )
        return response.text.strip()

    def _execute_all(
        self,
        calls: List[ToolCall],
        on_tool_start,
        on_tool_end,
        cache: Dict[str, ToolInvocation],
    ) -> List[ToolInvocation]:
        """Ejecuta las llamadas de una vuelta, en paralelo, conservando el orden.

        El orden importa aunque la ejecución no sea secuencial: es el orden en
        que se pintan la traza y los artefactos, y el orden en que el modelo
        pidió las cosas es el que tiene sentido para quien lee.

        `cache` son los resultados ya calculados en este turno (ver `run`). El
        acceso desde varios hilos no se sincroniza porque no hace falta: en el
        peor caso dos hilos ejecutan a la vez la misma llamada y uno pisa al
        otro con un resultado idéntico.
        """
        for call in calls:
            if on_tool_start is not None:
                on_tool_start(call.name, call.arguments)

        def _run(call: ToolCall) -> ToolInvocation:
            if call.arguments_error:
                # El modelo emitió JSON inválido. Se le devuelve como error de
                # herramienta para que reintente, en vez de tumbar el turno.
                return self.catalog.invalid_arguments(call.id, call.name, call.arguments_error)

            key = _cache_key(call.name, call.arguments)
            already = cache.get(key)
            if already is not None:
                return _repeated(already, call.id)

            invocation = self.catalog.execute(call.id, call.name, call.arguments)
            # Solo se guardan los aciertos: un error es barato de repetir y
            # verlo otra vez tal cual es lo que hace que el modelo reaccione a
            # su `suggestion` en vez de a un aviso de repetición.
            if not invocation.error:
                cache[key] = invocation
            return invocation

        if len(calls) == 1:
            invocations = [_run(calls[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_TOOLS, len(calls))) as pool:
                invocations = list(pool.map(_run, calls))

        for invocation in invocations:
            if on_tool_end is not None:
                on_tool_end(invocation)
        return invocations


def _cache_key(name: str, arguments: Dict[str, Any]) -> str:
    """Identidad de una llamada: la herramienta y sus argumentos, ordenados."""
    return f"{name}({json.dumps(arguments or {}, sort_keys=True, default=str)})"


def _repeated(invocation: ToolInvocation, call_id: str) -> ToolInvocation:
    """El mismo resultado, marcado como repetido, para el `call_id` nuevo.

    El `call_id` tiene que ser el de ESTA llamada: el agente empareja cada
    `tool_result` con su `tool_call` por ese id, y reutilizar el viejo deja el
    historial inconsistente (ver el docstring del módulo).

    El artefacto se deja fuera a propósito: la respuesta pintaría dos veces el
    mismo mapa de tiros. La traza sí conserva la línea, porque que el modelo
    haya insistido es información útil para quien la lee.
    """
    result = dict(invocation.result)
    meta = dict(result.get("meta") or {})
    meta["repeated_call"] = (
        "Ya has llamado a esta herramienta con estos mismos argumentos en este turno. "
        "Este es el resultado que ya tenías: no vuelvas a pedirlo. Responde con lo que hay "
        "o cambia de herramienta."
    )
    result["meta"] = meta
    return replace(
        invocation,
        call_id=call_id,
        result=result,
        artifact=None,
        summary=f"{invocation.summary} · repetida, no se ha vuelto a consultar",
    )


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
