"""Puerto de proveedor de LLM: tipos comunes y `Protocol` que cumplen los adaptadores.

Ver `local/features/005-chatbot/01_design.md` §3.4. La forma interna de los
mensajes es deliberadamente la del dialecto OpenAI (rol `tool` con
`tool_call_id`, argumentos de herramienta como cadena JSON): es el dialecto
del adaptador del PoC, así que traducir cuesta cero en el camino caliente y
todo el coste se paga en `anthropic.py`, que es el que se usará algún día y
no hoy. Lo que importa no es cuál de los dos formatos gana, sino que **solo
`llm/` conozca la diferencia**.

Ningún módulo de este paquete importa el SDK de ningún proveedor a nivel de
módulo: el import va dentro del constructor de cada adaptador. Así la suite
offline (`tests/app/assistant/`) puede probar la traducción de dialectos sin
tener instalado `openai` ni `anthropic` — que es justo lo que pide §12.4.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol


class LLMError(RuntimeError):
    """Fallo del proveedor que la interfaz debe saber contar en una frase.

    Args:
        message: texto ya redactado para el usuario final (la página lo pinta
            tal cual, ver `app/pages/asistente.py`).
        retryable: `True` si tiene sentido reintentar más tarde (429, 5xx,
            timeout) — lo usa el adaptador para decidir si espera y reintenta,
            y la página para sugerir "prueba en un minuto" en vez de "revisa
            la configuración".
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ToolSpec:
    """Esquema de una herramienta tal y como se le enseña al modelo.

    `parameters` es JSON Schema (objeto con `properties`/`required`), estricto
    a propósito: sin propiedades extra y con los tipos cerrados, un modelo
    pequeño se equivoca mucho menos al rellenarlo (§8.4).
    """

    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """Una llamada a herramienta pedida por el modelo, ya con los argumentos parseados.

    `arguments` llega siempre como `dict`: el adaptador hace el `json.loads` y,
    si el modelo emitió JSON inválido, lo deja en `{}` y lo cuenta en
    `arguments_error` — el agente lo convierte en un `tool_result` de error
    para que reintente, en vez de reventar el turno (§8.1, punto 4).
    """

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    arguments_error: Optional[str] = None


@dataclass
class LLMResponse:
    """Respuesta de un turno del modelo, en la forma interna común."""

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    stop_reason: Optional[str] = None


# Mensajes en forma interna (dialecto OpenAI, ver docstring del módulo):
#   {"role": "user", "content": "..."}
#   {"role": "assistant", "content": "...", "tool_calls": [...]}
#   {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<json>"}
Message = Dict[str, Any]

# Callback de streaming: recibe cada trozo de texto según llega del proveedor.
# Si es `None`, el adaptador pide la respuesta completa de una vez.
TextSink = Optional[Callable[[str], None]]


class LLMClient(Protocol):
    """Lo único que el agente necesita saber de un proveedor.

    Deliberadamente mínimo: un método. Todo lo que un proveedor concreto
    ofrece de más (caché explícita de prefijo, bloques de razonamiento) vive
    dentro de su adaptador y no asoma por aquí — si asomara, dejaría de ser
    posible cambiar de proveedor sin tocar el agente.
    """

    #: Etiqueta legible para la cabecera del chat ("groq · llama-3.3-70b").
    label: str

    def chat(
        self,
        messages: List[Message],
        tools: List[ToolSpec],
        *,
        system: str,
        on_text: TextSink = None,
    ) -> LLMResponse:
        """Un turno de modelo.

        Args:
            messages: historial en forma interna (ver docstring del módulo).
            tools: catálogo completo de herramientas disponibles este turno.
            system: prompt de sistema ya montado (`app/assistant/prompt.py`).
            on_text: si se pasa, se llama con cada trozo de texto según llega
                (streaming). Si es `None`, no se hace streaming.

        Returns:
            `LLMResponse` con el texto final y/o las herramientas que pide.

        Raises:
            LLMError: cualquier fallo del proveedor, ya traducido a una frase
                que la interfaz puede enseñar.
        """
        ...
