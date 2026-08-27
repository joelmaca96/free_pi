"""Cliente que envuelve dos `LLMClient`: si el principal falla, prueba el de respaldo.

Existe por un caso muy concreto de la capa gratuita (§8.5): un proveedor
puntual devuelve "servicio sobrecargado" (o cualquier otro fallo) y, sin
esto, el asistente entero se cae con ese mensaje aunque haya un segundo
modelo gratuito disponible y funcionando.

Solo se activa si `ASSISTANT_LLM_FALLBACK_MODEL` está puesto en el `.env`
(ver `build_llm_client` en `__init__.py`): sin esa variable, la fábrica
devuelve el cliente principal tal cual y esta clase ni se instancia — cero
cambio de comportamiento para quien no la usa.
"""
import logging
from typing import List

from .base import LLMClient, LLMError, LLMResponse, Message, TextSink, ToolSpec

logger = logging.getLogger(__name__)


class FallbackLLMClient:
    """Cumple `LLMClient`: delega en `primary` y, si falla, en `fallback`.

    Deliberadamente NO reintenta con el de respaldo si el fallo del
    principal llega a mitad de un streaming con texto ya enseñado al
    usuario — mezclar la mitad de una respuesta de un modelo con el resto
    de otro es peor que propagar el error tal cual (§9.2).
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient):
        self._primary = primary
        self._fallback = fallback
        self.label = f"{primary.label} (respaldo: {fallback.label})"

    def chat(
        self,
        messages: List[Message],
        tools: List[ToolSpec],
        *,
        system: str,
        on_text: TextSink = None,
    ) -> LLMResponse:
        streamed_anything = []
        sink = None
        if on_text is not None:

            def sink(piece: str) -> None:  # noqa: E306 - closure a propósito
                streamed_anything.append(True)
                on_text(piece)

        try:
            return self._primary.chat(messages, tools, system=system, on_text=sink)
        except LLMError as primary_error:
            if streamed_anything:
                raise
            logger.warning(
                "proveedor principal (%s) ha fallado, probando el de respaldo (%s): %s",
                self._primary.label,
                self._fallback.label,
                primary_error,
            )
            try:
                return self._fallback.chat(messages, tools, system=system, on_text=on_text)
            except LLMError as fallback_error:
                raise LLMError(
                    "Han fallado los dos proveedores configurados. "
                    f"Principal ({self._primary.label}): {primary_error} · "
                    f"Respaldo ({self._fallback.label}): {fallback_error}",
                    retryable=primary_error.retryable and fallback_error.retryable,
                ) from fallback_error
