"""Adaptador de Claude (dialecto Anthropic). Existe y funciona, pero cuesta dinero.

El PoC corre a coste cero contra capa gratuita (§8.3), y la API de Anthropic
**no tiene capa gratuita**: una clave recién creada lista modelos sin problema
pero devuelve `400 credit balance is too low` en el primer mensaje. Así que
este camino está listo para el día que haya saldo, no para hoy.

Existe igualmente por el motivo de §13/§12.4: la propiedad que hace barata
cualquier migración futura (§16) es que solo `llm/` conozca el dialecto del
proveedor, y esa propiedad no se sostiene documentándola — se sostiene
teniendo un segundo dialecto traducido y probado. Las funciones puras de aquí
(`to_wire_messages`, `to_wire_tools`, `from_wire_content`) se prueban con
respuestas grabadas, sin red y sin el SDK instalado.

Diferencias reales con el dialecto OpenAI, que son todas las que hay:
- El prompt de sistema es un parámetro propio (`system=`), no un mensaje.
- El modelo devuelve una LISTA de bloques (`text` / `thinking` / `tool_use`),
  no un `content` de texto más un `tool_calls` aparte.
- El resultado de una herramienta es un bloque `tool_result` dentro de un
  mensaje de rol `user`, no un mensaje de rol `tool`.
- Los argumentos de la herramienta llegan ya como objeto (`input`), no como
  cadena JSON que haya que parsear.
- Los modelos de 2026 (Opus 5, Sonnet 5, Opus 4.7/4.8...) **rechazan
  `temperature` con un 400** y piensan por defecto. Ver `_NO_SAMPLING`.

Lo que este adaptador sí puede aprovechar y el otro no (§8.5) es
`cache_control` sobre el prefijo estable del prompt — de ahí que
`app/assistant/prompt.py` mantenga el orden estable-primero aunque hoy no
sirva de nada, porque el día que sirva no habrá que reordenar nada.
"""
import json
from typing import Any, Dict, List, Tuple

from .base import LLMError, LLMResponse, Message, TextSink, ToolCall, ToolSpec

# Modelos que rechazan los parámetros de muestreo (`temperature`, `top_p`,
# `top_k`) con un 400. No es una preferencia de estilo: mandar
# `temperature=0.2` a `claude-opus-5` tumba la petición entera. Se compara por
# prefijo porque algunos nombres llevan sufijo de fecha
# (`claude-haiku-4-5-20251001`).
_NO_SAMPLING = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
)

# Modelos con pensamiento adaptativo y `output_config.effort`. En estos el
# modelo decide cuánto razonar y el `effort` regula el gasto; en los de antes
# (Haiku 4.5, Sonnet 4.5) `effort` da error, así que ni se manda.
_ADAPTIVE_THINKING = _NO_SAMPLING + ("claude-opus-4-6", "claude-sonnet-4-6")

# Niveles válidos de `output_config.effort`, de menos a más gasto.
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def supports_sampling(model: str) -> bool:
    """¿Acepta este modelo `temperature`? (los de 2026 no, ver `_NO_SAMPLING`)."""
    return not model.startswith(_NO_SAMPLING)


def supports_adaptive_thinking(model: str) -> bool:
    """¿Acepta este modelo `thinking: adaptive` y `output_config.effort`?"""
    return model.startswith(_ADAPTIVE_THINKING)


def to_wire_system(system: str) -> List[Dict[str, Any]]:
    """Prompt de sistema en bloques, con el punto de caché tras la parte estable.

    Anthropic cachea por PREFIJO: el punto de corte guarda todo lo que hay
    antes (las herramientas se renderizan antes que el sistema, así que
    entran también) y cualquier byte que cambie por delante lo invalida todo.
    Mandar el prompt entero en un solo bloque marcado, que es lo que se hacía
    antes, significa que el contexto de pantalla que inyecta la interfaz
    (`extra_context`, §9.4) tira ~7.000 tokens de caché cada vez que cambia:
    justo lo que la caché existía para evitar.

    `app/assistant/prompt.py` ya monta el prompt estable-primero y publica
    `stable_prefix_length()` para localizar la frontera. Importarlo desde aquí
    acopla el adaptador al formato del prompt, pero es la única alternativa a
    duplicar el marcador en dos sitios y que se separen en silencio — que es
    peor, porque el síntoma no es un fallo sino una factura.
    """
    from ..prompt import stable_prefix_length  # import perezoso: evita ciclos al importar

    cut = stable_prefix_length(system)
    stable, volatile = system[:cut].rstrip(), system[cut:].strip()
    if not stable:
        # No hay parte estable que cachear (prompt sin el bloque de sesión, o
        # todo volátil). Marcar un prefijo vacío no cachea nada y complica.
        return [{"type": "text", "text": system}]
    blocks = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return blocks


def to_wire_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Catálogo interno -> `tools` del dialecto Anthropic (`input_schema`, no `parameters`)."""
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


def to_wire_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    """Historial interno -> `messages` del dialecto Anthropic.

    Dos reglas que no son obvias, y las dos son requisitos duros del proveedor:

    - Los `tool_result` consecutivos se agrupan en UN solo mensaje de usuario:
      es el equivalente exacto de la regla del bucle (§3.3) de devolver todos
      los resultados paralelos juntos, y repartirlos en mensajes distintos
      enseña al modelo a dejar de paralelizar.
    - Si el turno del asistente trae `provider_state` (los bloques crudos que
      devolvió el modelo), se reenvían TAL CUAL en vez de reconstruirlos a
      partir de texto + `tool_calls`. Con pensamiento activado los bloques
      `thinking` van firmados y el proveedor exige recibirlos intactos junto
      al `tool_use` al que preceden; reconstruirlos los perdería y la
      siguiente vuelta de herramienta fallaría con un 400.
    """
    wire: List[Dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "assistant":
            replay = msg.get("provider_state")
            if replay:
                wire.append({"role": "assistant", "content": list(replay)})
                continue
            blocks: List[Dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
            wire.append({"role": "assistant", "content": blocks})
        elif msg["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}
            if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                wire[-1]["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
        else:
            wire.append({"role": "user", "content": msg.get("content", "")})
    return wire


def from_wire_content(blocks: List[Dict[str, Any]]) -> Tuple[str, List[ToolCall]]:
    """`(texto, [ToolCall])` a partir de la lista de bloques que devuelve Claude.

    Los bloques `thinking` se ignoran a propósito: son razonamiento, no
    respuesta. Se conservan aparte (ver `replayable_blocks`) porque hay que
    devolvérselos al modelo, pero no se pintan ni cuentan como texto.
    """
    text_parts: List[str] = []
    calls: List[ToolCall] = []
    for block in blocks or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            raw_input = block.get("input")
            arguments = raw_input if isinstance(raw_input, dict) else {}
            error = None if isinstance(raw_input, dict) else "los argumentos deben ser un objeto JSON"
            calls.append(
                ToolCall(id=block.get("id", ""), name=block.get("name", ""), arguments=arguments, arguments_error=error)
            )
    return "".join(text_parts), calls


def replayable_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Los bloques crudos, limpios de claves nulas, listos para reenviarse.

    `model_dump()` rellena con `None` todo campo opcional que el modelo no
    usó (`citations`, `cache_control`...). Devolverlos así suele colar, pero
    no siempre; quitarlos cuesta una línea y elimina la clase entera de fallo.
    """
    return [{key: value for key, value in block.items() if value is not None} for block in blocks or []]


def normalize_usage(raw: Dict[str, Any]) -> Dict[str, int]:
    """Consumo del proveedor -> el mismo dict, más un `total_tokens` calculado.

    Anthropic no manda `total_tokens`, y ese es justo el nombre que mira el
    tope de tokens por turno del agente (§11.3). Sin esto el tope no salta
    nunca: no falla nada visible, simplemente deja de haber tope — que con la
    app publicada por el túnel es peor que un error.
    """
    usage = {key: int(value) for key, value in (raw or {}).items() if isinstance(value, int)}
    if "total_tokens" not in usage:
        usage["total_tokens"] = sum(
            usage.get(key, 0)
            for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
    return usage


class AnthropicClient:
    """Cliente de Claude vía SDK oficial (`anthropic>=1.0`).

    Args:
        model: id exacto del modelo (`claude-opus-5`, `claude-haiku-4-5`...).
            Compruébalo con `tools/list_models.py`, que se lo pregunta a la API.
        api_key: clave `sk-ant-...` de console.anthropic.com.
        temperature: solo se manda a los modelos que la aceptan (ver
            `supports_sampling`); en los demás se omite en vez de dar un 400.
        effort: cuánto se le deja razonar a los modelos que piensan. `low`
            para preguntas de una vuelta, `high` cuando importa acertar. Es la
            palanca de coste más directa que tiene este adaptador.
        max_tokens: tope de salida por turno. Con pensamiento activado los
            tokens de razonamiento salen de aquí, así que quedarse corto
            trunca la respuesta a media frase.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        temperature: float = 0.2,
        timeout: float = 90.0,
        max_tokens: int = 1500,
        effort: str = "medium",
    ):
        try:
            from anthropic import Anthropic  # import perezoso, ver docstring de `base.py`
        except ImportError as exc:  # pragma: no cover - depende del entorno, no de la lógica
            raise LLMError(
                "Falta la dependencia `anthropic` (declarada en app/requirements.txt). "
                "Instálala para poder usar Claude."
            ) from exc

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.effort = effort if effort in _EFFORT_LEVELS else "medium"
        self.label = f"anthropic · {model}"
        self._client = Anthropic(api_key=api_key, timeout=timeout)

    def chat(
        self,
        messages: List[Message],
        tools: List[ToolSpec],
        *,
        system: str,
        on_text: TextSink = None,
    ) -> LLMResponse:
        # El punto de caché va tras la parte estable del prompt (§8.6), que es
        # lo que convierte el prefijo fijo de ~7k tokens en lectura a 0.1x en
        # vez de entrada completa. Ver `to_wire_system`.
        payload: Dict[str, Any] = {
            "model": self.model,
            "system": to_wire_system(system),
            "messages": to_wire_messages(messages),
            "max_tokens": self.max_tokens,
        }
        if supports_sampling(self.model):
            payload["temperature"] = self.temperature
        if supports_adaptive_thinking(self.model):
            # Se le deja pensar y se regula el gasto con `effort`, en vez de
            # apagar el pensamiento: con `thinking: disabled` estos modelos
            # escriben a veces la llamada a herramienta en el texto visible en
            # lugar de emitir un bloque `tool_use` — el turno "funciona", la
            # herramienta no se ejecuta y nadie se entera.
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": self.effort}
        if tools:
            payload["tools"] = to_wire_tools(tools)

        try:
            if on_text is None:
                data = self._client.messages.create(**payload).model_dump()
            else:
                data = self._stream(payload, on_text)
        except LLMError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc

        blocks = data.get("content") or []
        text, calls = from_wire_content(blocks)
        return LLMResponse(
            text=text,
            tool_calls=calls,
            usage=normalize_usage(data.get("usage") or {}),
            stop_reason=data.get("stop_reason"),
            # Solo hace falta conservar los bloques del turno que se va a
            # reenviar, que es el que pide herramientas: el último turno, el de
            # texto, ya no vuelve a viajar con bloques firmados dentro.
            provider_state=replayable_blocks(blocks) if calls else None,
        )

    def _stream(self, payload: Dict[str, Any], on_text: TextSink) -> Dict[str, Any]:
        with self._client.messages.stream(**payload) as stream:
            for piece in stream.text_stream:
                on_text(piece)
            return stream.get_final_message().model_dump()


def _translate(exc: Exception) -> LLMError:
    """Excepción del SDK -> `LLMError` con una frase que la interfaz pueda enseñar."""
    status = getattr(exc, "status_code", None)
    if status == 400 and "credit balance" in str(exc).lower():
        # El fallo con el que se estrena toda clave nueva: la API de Claude no
        # tiene capa gratuita. Merece frase propia porque el 400 genérico
        # ("petición inválida") manda a revisar el código, que está bien.
        return LLMError(
            "La cuenta de Anthropic no tiene saldo: la API de Claude no tiene capa gratuita. "
            "Compra crédito en console.anthropic.com, o apunta ASSISTANT_LLM_* a un proveedor gratuito."
        )
    if status == 429:
        return LLMError("Límite de la API de Claude alcanzado. Prueba dentro de un minuto.", retryable=True)
    if status in (401, 403):
        return LLMError("La API de Claude rechazó la clave (revisa ASSISTANT_LLM_API_KEY).")
    if status in (500, 502, 503, 504):
        return LLMError("La API de Claude no responde ahora mismo. Prueba en un minuto.", retryable=True)
    return LLMError(f"No se ha podido hablar con Claude: {exc}")


def dump_tool_result(payload: Any) -> str:
    """Serializa un `tool_result` igual que el otro adaptador (JSON compacto, sin escapar acentos)."""
    return json.dumps(payload, ensure_ascii=False)
