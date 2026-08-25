"""Adaptador de Claude (dialecto Anthropic). Fase 5: existe, pero no es el camino del PoC.

El PoC corre a coste cero contra capa gratuita (§8.3), así que este fichero
**no** se usa hoy. Existe igualmente, y por un motivo concreto de §13/§12.4:
la propiedad que hace barata cualquier migración futura (§16) es que solo
`llm/` conozca el dialecto del proveedor, y esa propiedad no se sostiene
documentándola — se sostiene teniendo un segundo dialecto traducido y probado.
Las funciones puras de aquí (`to_wire_messages`, `to_wire_tools`,
`from_wire_content`) se prueban con respuestas grabadas, sin red y sin el SDK
instalado.

Diferencias reales con el dialecto OpenAI, que son todas las que hay:
- El prompt de sistema es un parámetro propio (`system=`), no un mensaje.
- El modelo devuelve una LISTA de bloques (`text` / `tool_use`), no un
  `content` de texto más un `tool_calls` aparte.
- El resultado de una herramienta es un bloque `tool_result` dentro de un
  mensaje de rol `user`, no un mensaje de rol `tool`.
- Los argumentos de la herramienta llegan ya como objeto (`input`), no como
  cadena JSON que haya que parsear.

Lo que este adaptador sí puede aprovechar y el otro no (§8.5) es
`cache_control` sobre el prefijo estable del prompt — de ahí que
`app/assistant/prompt.py` mantenga el orden estable-primero aunque hoy no
sirva de nada, porque el día que sirva no habrá que reordenar nada.
"""
import json
from typing import Any, Dict, List, Optional

from .base import LLMError, LLMResponse, Message, TextSink, ToolCall, ToolSpec


def to_wire_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Catálogo interno -> `tools` del dialecto Anthropic (`input_schema`, no `parameters`)."""
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


def to_wire_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    """Historial interno -> `messages` del dialecto Anthropic.

    Los `tool_result` consecutivos se agrupan en UN solo mensaje de usuario:
    es el equivalente exacto de la regla del bucle (§3.3) de devolver todos
    los resultados paralelos juntos, y repartirlos en mensajes distintos
    enseña al modelo a dejar de paralelizar.
    """
    wire: List[Dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "assistant":
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


def from_wire_content(blocks: List[Dict[str, Any]]) -> tuple:
    """`(texto, [ToolCall])` a partir de la lista de bloques que devuelve Claude."""
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


class AnthropicClient:
    """Cliente de Claude vía SDK oficial. No se usa en el PoC (ver docstring del módulo)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        temperature: float = 0.2,
        timeout: float = 90.0,
        max_tokens: int = 1500,
    ):
        try:
            from anthropic import Anthropic  # import perezoso: el PoC no instala este SDK
        except ImportError as exc:  # pragma: no cover - depende del entorno, no de la lógica
            raise LLMError(
                "Falta la dependencia `anthropic`. El PoC no la instala a propósito "
                "(ASSISTANT_LLM_PROVIDER=openai_compat); añádela si vas a usar Claude."
            ) from exc

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
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
        # `cache_control` sobre el bloque de sistema: es estable entre turnos
        # (§8.6), así que marcarlo aquí es lo que convierte el prompt fijo de
        # ~4k tokens en lectura de caché a 0.1x en vez de entrada completa.
        payload: Dict[str, Any] = {
            "model": self.model,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": to_wire_messages(messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
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

        text, calls = from_wire_content(data.get("content") or [])
        return LLMResponse(
            text=text,
            tool_calls=calls,
            usage=data.get("usage") or {},
            stop_reason=data.get("stop_reason"),
        )

    def _stream(self, payload: Dict[str, Any], on_text: TextSink) -> Dict[str, Any]:
        with self._client.messages.stream(**payload) as stream:
            for piece in stream.text_stream:
                on_text(piece)
            return stream.get_final_message().model_dump()


def _translate(exc: Exception) -> LLMError:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return LLMError("Límite de la API de Claude alcanzado. Prueba dentro de un minuto.", retryable=True)
    if status in (401, 403):
        return LLMError("La API de Claude rechazó la clave (revisa ASSISTANT_LLM_API_KEY).")
    return LLMError(f"No se ha podido hablar con Claude: {exc}")


def dump_tool_result(payload: Any) -> str:
    """Serializa un `tool_result` igual que el otro adaptador (JSON compacto, sin escapar acentos)."""
    return json.dumps(payload, ensure_ascii=False)
