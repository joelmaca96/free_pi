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
    ASSISTANT_LLM_EFFORT       solo `anthropic`: cuánto razona el modelo
                               (low|medium|high|xhigh|max, `medium` por
                               defecto). Es la palanca de coste directa.

    ASSISTANT_LLM_FALLBACK_MODEL     si se pone, activa el respaldo (ver
                                     `fallback.py`): si el principal falla,
                                     se reintenta con este modelo antes de
                                     dar el turno por perdido. Vacía por
                                     defecto — sin esta variable no cambia
                                     nada del comportamiento de siempre.
    ASSISTANT_LLM_FALLBACK_PROVIDER    por defecto, el mismo que el principal.
    ASSISTANT_LLM_FALLBACK_BASE_URL    por defecto, el mismo que el principal.
    ASSISTANT_LLM_FALLBACK_API_KEY     por defecto, la misma que el principal.
    ASSISTANT_LLM_FALLBACK_MAX_TOKENS  por defecto, el mismo que el principal.
                                       Variable propia porque en la práctica
                                       CADA proveedor gratuito tiene su
                                       propio límite de tokens/minuto, y
                                       compartir el tope de salida del
                                       principal puede hacer que el propio
                                       respaldo lo supere él solo (visto con
                                       Groq: 8.000 TPM, tope de salida por
                                       encima de eso y el respaldo revienta
                                       antes de responder).
    Temperatura, timeout y esfuerzo del respaldo sí son los del principal —
    si algún día hace falta que difieran también, se añaden sus propias
    variables igual que se hizo con `_MAX_TOKENS`.
"""
import logging
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

logger = logging.getLogger(__name__)

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
    label = f"{provider} · {model}"
    fallback_model = os.getenv("ASSISTANT_LLM_FALLBACK_MODEL", "").strip()
    if fallback_model:
        label += f" (respaldo: {fallback_model})"
    return label


def _build_client(
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    effort: str,
) -> Optional[LLMClient]:
    """Un cliente concreto a partir de configuración ya resuelta (sin leer entorno).

    Extraído de `build_llm_client` para poder construir el principal y el de
    respaldo con la misma lógica (ver `ASSISTANT_LLM_FALLBACK_*`). Devuelve
    `None` cuando falta algo imprescindible, igual que antes de extraerlo.
    """
    if not model:
        return None

    if provider == "anthropic":
        if not api_key:
            return None
        from .anthropic import AnthropicClient

        return AnthropicClient(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            effort=effort or "medium",
        )

    if provider != _DEFAULT_PROVIDER:
        raise LLMError(
            f"ASSISTANT_LLM_PROVIDER={provider!r} no existe. Valores válidos: "
            f"{_DEFAULT_PROVIDER!r} (local/Groq/Cerebras/Cloudflare/OpenRouter) o 'anthropic'."
        )

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


def build_llm_client() -> Optional[LLMClient]:
    """Cliente configurado, o `None` si no hay configuración suficiente.

    Devuelve `None` en vez de lanzar: un asistente sin proveedor configurado
    no es un error de la aplicación — la página lo explica con un `st.info` y
    las otras cuatro pestañas siguen funcionando (§9.1). El asistente no es
    un requisito de arranque de la interfaz.

    Si `ASSISTANT_LLM_FALLBACK_MODEL` está puesto, el cliente devuelto es un
    `FallbackLLMClient` que prueba ese segundo modelo cuando el principal
    falla (ver `fallback.py`). Un fallback mal configurado no tumba el
    asistente: se avisa por log y se sigue solo con el principal.

    Raises:
        LLMError: si hay configuración pero es inutilizable (SDK que falta,
            clave rechazada al construir el cliente). Eso sí es un fallo que
            merece contarse, no un "no configurado".
    """
    provider = os.getenv("ASSISTANT_LLM_PROVIDER", _DEFAULT_PROVIDER).strip() or _DEFAULT_PROVIDER
    base_url = os.getenv("ASSISTANT_LLM_BASE_URL", "").strip()
    model = os.getenv("ASSISTANT_LLM_MODEL", "").strip()
    api_key = os.getenv("ASSISTANT_LLM_API_KEY", "").strip()
    temperature = _env_float("ASSISTANT_LLM_TEMPERATURE", 0.2)
    max_tokens = _env_int("ASSISTANT_LLM_MAX_TOKENS", 1500)
    timeout = _env_float("ASSISTANT_LLM_TIMEOUT", 90.0)
    effort = os.getenv("ASSISTANT_LLM_EFFORT", "").strip()

    primary = _build_client(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        effort=effort,
    )
    if primary is None:
        return None

    fallback_model = os.getenv("ASSISTANT_LLM_FALLBACK_MODEL", "").strip()
    if not fallback_model:
        return primary

    fallback_provider = os.getenv("ASSISTANT_LLM_FALLBACK_PROVIDER", "").strip() or provider
    fallback_base_url = os.getenv("ASSISTANT_LLM_FALLBACK_BASE_URL", "").strip() or base_url
    fallback_api_key = os.getenv("ASSISTANT_LLM_FALLBACK_API_KEY", "").strip() or api_key
    fallback_max_tokens = _env_int("ASSISTANT_LLM_FALLBACK_MAX_TOKENS", max_tokens)
    try:
        fallback = _build_client(
            provider=fallback_provider,
            base_url=fallback_base_url,
            model=fallback_model,
            api_key=fallback_api_key,
            temperature=temperature,
            max_tokens=fallback_max_tokens,
            timeout=timeout,
            effort=effort,
        )
    except LLMError as exc:
        logger.warning("ASSISTANT_LLM_FALLBACK_* mal configurado, se ignora (%s)", exc)
        return primary
    if fallback is None:
        logger.warning(
            "ASSISTANT_LLM_FALLBACK_MODEL=%r puesto pero falta base_url/api_key del respaldo, se ignora",
            fallback_model,
        )
        return primary

    from .fallback import FallbackLLMClient

    return FallbackLLMClient(primary, fallback)
