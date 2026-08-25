"""Fábrica del cliente de LLM: lee `ASSISTANT_LLM_*` y devuelve el adaptador que toque.

Cambiar de proveedor es cambiar variables de entorno, no código (§8): ni el
agente, ni las herramientas, ni la interfaz saben quién hay detrás. Ver §16
para el procedimiento de migración que esta indirección hace barato.

Variables (documentadas en `.env.example`):
    ASSISTANT_LLM_PROVIDER   openai_compat (por defecto) | anthropic
    ASSISTANT_LLM_BASE_URL   endpoint `.../v1` del proveedor compatible
    ASSISTANT_LLM_MODEL      nombre del modelo en ese proveedor
    ASSISTANT_LLM_API_KEY    vacía con modelo local; la clave del proveedor si la hay
    ASSISTANT_LLM_TEMPERATURE  0.2 por defecto (§8.4)
    ASSISTANT_LLM_MAX_TOKENS   tope de salida por turno
    ASSISTANT_LLM_TIMEOUT      segundos de espera por petición
"""
import os
from typing import Optional

from .base import LLMClient, LLMError, LLMResponse, Message, ToolCall, ToolSpec

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "ToolCall",
    "ToolSpec",
    "build_llm_client",
    "provider_label",
]

_DEFAULT_PROVIDER = "openai_compat"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def provider_label() -> str:
    """Proveedor y modelo configurados, para la cabecera del chat (§9.1).

    Con la configuración por entorno, no saber contra qué modelo estás
    hablando es una fuente segura de confusión al comparar respuestas — por
    eso se muestra siempre, aunque el proveedor no esté disponible.
    """
    provider = os.getenv("ASSISTANT_LLM_PROVIDER", _DEFAULT_PROVIDER).strip() or _DEFAULT_PROVIDER
    model = os.getenv("ASSISTANT_LLM_MODEL", "").strip() or "(sin modelo)"
    return f"{provider} · {model}"


def build_llm_client() -> Optional[LLMClient]:
    """Cliente configurado, o `None` si no hay configuración suficiente.

    Devuelve `None` en vez de lanzar: un asistente sin proveedor configurado
    no es un error de la aplicación — la página lo explica con un `st.info` y
    las otras cuatro pestañas siguen funcionando (§9.1). El asistente no es
    un requisito de arranque de la interfaz.

    Raises:
        LLMError: si hay configuración pero es inutilizable (SDK que falta,
            clave rechazada al construir el cliente). Eso sí es un fallo que
            merece contarse, no un "no configurado".
    """
    provider = os.getenv("ASSISTANT_LLM_PROVIDER", _DEFAULT_PROVIDER).strip() or _DEFAULT_PROVIDER
    model = os.getenv("ASSISTANT_LLM_MODEL", "").strip()
    api_key = os.getenv("ASSISTANT_LLM_API_KEY", "").strip()
    temperature = _env_float("ASSISTANT_LLM_TEMPERATURE", 0.2)
    max_tokens = _env_int("ASSISTANT_LLM_MAX_TOKENS", 1500)
    timeout = _env_float("ASSISTANT_LLM_TIMEOUT", 90.0)

    if not model:
        return None

    if provider == "anthropic":
        if not api_key:
            return None
        from .anthropic import AnthropicClient

        return AnthropicClient(
            model=model, api_key=api_key, temperature=temperature, max_tokens=max_tokens, timeout=timeout
        )

    if provider != _DEFAULT_PROVIDER:
        raise LLMError(
            f"ASSISTANT_LLM_PROVIDER={provider!r} no existe. Valores válidos: "
            f"{_DEFAULT_PROVIDER!r} (local/Groq/Cerebras/Cloudflare/OpenRouter) o 'anthropic'."
        )

    base_url = os.getenv("ASSISTANT_LLM_BASE_URL", "").strip()
    if not base_url:
        return None

    from .openai_compat import OpenAICompatClient

    return OpenAICompatClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
