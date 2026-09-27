"""Adaptador del dialecto OpenAI: LLM local, Groq, Cerebras, Cloudflare, OpenRouter...

Es el camino del PoC (§8.3): todos esos endpoints hablan el mismo dialecto y
solo se diferencian en `base_url`, `model` y si piden clave o no. La
traducción de dialecto vive en funciones puras (`_to_wire_messages`,
`_to_wire_tools`, `_from_wire_message`, `_merge_stream_deltas`) para poder
probarla con respuestas grabadas y sin red — ver `tests/app/assistant/
test_llm_adapters.py`.

El SDK (`openai>=1.0`) se importa DENTRO del constructor, no a nivel de
módulo: la suite offline prueba la traducción sin tenerlo instalado.
"""
import json
import time
from typing import Any, Dict, List, Optional

from .base import LLMError, LLMResponse, Message, TextSink, ToolCall, ToolSpec

# Espera entre reintentos ante un 429/5xx. Progresión corta a propósito: con
# capa gratuita el límite que salta suele ser el de tokens/minuto (§8.5), así
# que un par de esperas cortas resuelve, y esperar más deja al usuario
# mirando una pantalla parada sin saber por qué.
_RETRY_WAITS_SECONDS = (2.0, 6.0)

# Códigos que merecen reintento (cuota, congestión, corte transitorio). El
# resto son errores de configuración o de petición: reintentarlos solo gasta
# cuota y retrasa el mensaje de error real.
_RETRYABLE_STATUS = (408, 409, 429, 500, 502, 503, 504)


def _to_wire_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Catálogo interno -> bloque `tools` del dialecto OpenAI."""
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def _to_wire_messages(messages: List[Message], system: str) -> List[Dict[str, Any]]:
    """Historial interno -> `messages` del dialecto OpenAI (el sistema va como mensaje 0)."""
    wire: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            wire.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in msg["tool_calls"]
                    ],
                }
            )
        elif msg["role"] == "tool":
            wire.append({"role": "tool", "tool_call_id": msg["tool_call_id"], "content": msg["content"]})
        else:
            wire.append({"role": msg["role"], "content": msg.get("content", "")})
    return wire


def _parse_arguments(raw: Optional[str]) -> tuple:
    """`(argumentos, error)` a partir del JSON crudo que emitió el modelo.

    Un modelo pequeño emite JSON inválido de vez en cuando; devolverlo como
    error de herramienta (para que reintente con sentido) es mucho mejor que
    tumbar el turno entero — ver `ToolCall.arguments_error` y §8.1 punto 4.
    """
    if not raw or not raw.strip():
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"argumentos JSON invalidos: {exc}"
    if not isinstance(parsed, dict):
        return {}, f"los argumentos deben ser un objeto JSON, no {type(parsed).__name__}"
    return parsed, None


def _from_wire_message(message: Dict[str, Any]) -> tuple:
    """`(texto, [ToolCall])` a partir del `message` que devuelve el proveedor."""
    text = message.get("content") or ""
    calls = []
    for raw_call in message.get("tool_calls") or []:
        function = raw_call.get("function", {})
        arguments, error = _parse_arguments(function.get("arguments"))
        calls.append(
            ToolCall(
                id=raw_call.get("id") or f"call_{len(calls)}",
                name=function.get("name", ""),
                arguments=arguments,
                arguments_error=error,
            )
        )
    return text, calls


def _merge_stream_deltas(chunks: List[Dict[str, Any]], on_text: TextSink) -> Dict[str, Any]:
    """Reensambla el `message` completo a partir de los deltas del streaming.

    Los `tool_calls` llegan troceados y se identifican por `index`, no por
    `id` (el `id` solo viene en el primer delta de cada llamada) — sin
    acumular por índice se pierden llamadas cuando el modelo pide varias a la
    vez, que es justo el caso que más interesa que funcione (§3.3).
    """
    content_parts: List[str] = []
    by_index: Dict[int, Dict[str, Any]] = {}
    for chunk in chunks:
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece:
            content_parts.append(piece)
            if on_text is not None:
                on_text(piece)
        for raw_call in delta.get("tool_calls") or []:
            index = raw_call.get("index", 0)
            slot = by_index.setdefault(index, {"id": None, "function": {"name": "", "arguments": ""}})
            if raw_call.get("id"):
                slot["id"] = raw_call["id"]
            function = raw_call.get("function") or {}
            if function.get("name"):
                slot["function"]["name"] = function["name"]
            if function.get("arguments"):
                slot["function"]["arguments"] += function["arguments"]
    return {
        "content": "".join(content_parts),
        "tool_calls": [by_index[i] for i in sorted(by_index)] or None,
    }


def normalize_usage(raw: Dict[str, Any]) -> Dict[str, int]:
    """Consumo del proveedor -> claves planas de enteros, con los tokens cacheados.

    El dialecto OpenAI esconde el acierto de caché un nivel más abajo
    (`prompt_tokens_details.cached_tokens`), y el agente solo suma valores
    enteros de primer nivel: sin aplanarlo, el dato se descarta en silencio y
    la pregunta "¿sirve de algo el orden del prompt?" no tiene respuesta en
    este proveedor. Importa porque el prompt está ordenado —estable primero,
    volátil al final (`prompt.py`)— precisamente para que el runtime pueda
    reutilizar el prefijo, y hoy son ~9.100 tokens por vuelta que se reenvían
    enteros cada vez. El adaptador de Anthropic ya lo expone plano
    (`cache_read_input_tokens`); esto es la pieza equivalente.
    """
    usage = {key: int(value) for key, value in (raw or {}).items() if isinstance(value, int)}
    details = (raw or {}).get("prompt_tokens_details") or {}
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
        usage["cached_tokens"] = details["cached_tokens"]
    return usage


def first_choice(data: Dict[str, Any]) -> Dict[str, Any]:
    """La primera `choice` de la respuesta, o un `LLMError` con lo que diga el proveedor.

    No todos los proveedores del dialecto devuelven los errores por el código
    HTTP: OpenRouter, por ejemplo, contesta 200 con un cuerpo `{"error": ...}`
    y sin `choices` cuando el modelo de turno está saturado o caído. Sin esto,
    el `data["choices"][0]` de más abajo peta con "'NoneType' object is not
    subscriptable" — un mensaje que no dice ni qué proveedor falló ni por qué,
    y que parece un bug del asistente cuando es una respuesta del servidor.
    """
    choices = data.get("choices") or []
    if choices:
        return choices[0]
    error = data.get("error")
    if isinstance(error, dict) and error.get("message"):
        raise LLMError(f"El proveedor ha devuelto un error: {error['message']}", retryable=True)
    raise LLMError(f"El proveedor ha devuelto una respuesta sin contenido: {data}")


def translate_error(exc: Optional[Exception]) -> LLMError:
    """Excepción del SDK -> `LLMError` con una frase que la interfaz pueda enseñar.

    Con capa gratuita el 429 es el fallo más probable del sistema entero
    (§8.5), y "límite del proveedor alcanzado" es accionable mientras que un
    stack trace no lo es.
    """
    status = getattr(exc, "status_code", None)
    if status == 429:
        return LLMError(
            "Límite del proveedor alcanzado (cuota gratuita). Prueba dentro de un minuto.",
            retryable=True,
        )
    if status in (401, 403):
        return LLMError("El proveedor rechazó la clave de API (revisa ASSISTANT_LLM_API_KEY).")
    if status in (500, 502, 503, 504):
        return LLMError("El proveedor no responde ahora mismo. Prueba en un minuto.", retryable=True)
    return LLMError(f"No se ha podido hablar con el modelo: {exc}")


class OpenAICompatClient:
    """Cliente para cualquier endpoint que hable el dialecto OpenAI.

    Args:
        base_url: `.../v1` del proveedor (`http://servidor:11434/v1` para
            Ollama, `https://api.groq.com/openai/v1` para Groq...).
        model: nombre del modelo en ESE proveedor.
        api_key: opcional — vacía con un modelo local. El SDK exige una
            cadena no vacía, así que se le pasa un centinela cuando no hay
            clave real; ningún endpoint local la mira.
        temperature: baja por defecto (§8.4) — con herramientas, la
            creatividad solo sirve para elegir peor.
        timeout: segundos de espera por petición.
        max_tokens: tope de salida por turno (parte del límite por turno de §11.3).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        temperature: float = 0.2,
        timeout: float = 90.0,
        max_tokens: int = 1500,
    ):
        try:
            from openai import OpenAI  # import perezoso, ver docstring del módulo
        except ImportError as exc:  # pragma: no cover - depende del entorno, no de la lógica
            raise LLMError(
                "Falta la dependencia `openai` (declarada en app/requirements.txt). "
                "Instálala para poder usar el asistente."
            ) from exc

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.label = f"{base_url} · {model}"
        self._client = OpenAI(base_url=base_url, api_key=api_key or "no-key-needed", timeout=timeout)

    def chat(
        self,
        messages: List[Message],
        tools: List[ToolSpec],
        *,
        system: str,
        on_text: TextSink = None,
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": _to_wire_messages(messages, system),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = _to_wire_tools(tools)
            payload["tool_choice"] = "auto"

        message, usage, stop_reason = self._request_with_retries(payload, on_text)
        text, calls = _from_wire_message(message)
        return LLMResponse(
            text=text, tool_calls=calls, usage=normalize_usage(usage), stop_reason=stop_reason
        )

    def _request_with_retries(self, payload: Dict[str, Any], on_text: TextSink) -> tuple:
        """Petición con espera creciente ante límite de cuota o fallo transitorio."""
        last_error: Optional[Exception] = None
        for attempt in range(len(_RETRY_WAITS_SECONDS) + 1):
            try:
                return self._request(payload, on_text)
            except LLMError as exc:
                # Ya viene traducido (error en el cuerpo, ver `first_choice`).
                # Volver a pasarlo por `translate_error` solo lo envolvería en
                # otra frase y escondería la del proveedor.
                if not exc.retryable:
                    raise
                last_error = exc
            except Exception as exc:  # el SDK tiene su propia jerarquía; se clasifica por status
                if getattr(exc, "status_code", None) not in _RETRYABLE_STATUS:
                    raise translate_error(exc) from exc
                last_error = exc
                if attempt < len(_RETRY_WAITS_SECONDS):
                    time.sleep(_RETRY_WAITS_SECONDS[attempt])
        if isinstance(last_error, LLMError):
            raise last_error
        raise translate_error(last_error)

    def _request(self, payload: Dict[str, Any], on_text: TextSink) -> tuple:
        if on_text is None:
            data = self._client.chat.completions.create(**payload).model_dump()
            choice = first_choice(data)
            return choice["message"], data.get("usage") or {}, choice.get("finish_reason")

        stream = self._client.chat.completions.create(
            **payload, stream=True, stream_options={"include_usage": True}
        )
        chunks: List[Dict[str, Any]] = []
        usage: Dict[str, int] = {}
        stop_reason = None
        for chunk in stream:
            data = chunk.model_dump()
            chunks.append(data)
            if data.get("usage"):
                usage = data["usage"]
            for choice in data.get("choices") or []:
                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]
        return _merge_stream_deltas(chunks, on_text), usage, stop_reason
