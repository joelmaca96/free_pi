"""`FallbackLLMClient`: si el principal falla, se reintenta con el de respaldo.

Sin red y sin SDKs: los dos "proveedores" son dobles de prueba que cumplen
`LLMClient` a mano, igual que hace `test_llm_adapters.py` con las funciones
de traducción de dialecto.
"""
import pytest

from app.assistant.llm.base import LLMError, LLMResponse
from app.assistant.llm.fallback import FallbackLLMClient


class _FakeClient:
    """Doble de `LLMClient`: responde con `response` o lanza `error`."""

    def __init__(self, label, *, response=None, error=None, text_before_error=None):
        self.label = label
        self._response = response
        self._error = error
        self._text_before_error = text_before_error
        self.calls = 0

    def chat(self, messages, tools, *, system, on_text=None):
        self.calls += 1
        if self._text_before_error and on_text is not None:
            on_text(self._text_before_error)
        if self._error is not None:
            raise self._error
        return self._response


def test_uses_primary_when_it_succeeds():
    primary = _FakeClient("principal", response=LLMResponse(text="ok"))
    fallback = _FakeClient("respaldo", response=LLMResponse(text="no debería llamarse"))
    client = FallbackLLMClient(primary, fallback)

    result = client.chat([], [], system="s")

    assert result.text == "ok"
    assert fallback.calls == 0
    assert client.label == "principal (respaldo: respaldo)"


def test_switches_to_fallback_when_primary_fails():
    primary = _FakeClient("principal", error=LLMError("sobrecargado", retryable=True))
    fallback = _FakeClient("respaldo", response=LLMResponse(text="respondo yo"))
    client = FallbackLLMClient(primary, fallback)

    result = client.chat([], [], system="s")

    assert result.text == "respondo yo"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_raises_combined_error_when_both_fail():
    primary = _FakeClient("principal", error=LLMError("falla A"))
    fallback = _FakeClient("respaldo", error=LLMError("falla B"))
    client = FallbackLLMClient(primary, fallback)

    with pytest.raises(LLMError) as exc_info:
        client.chat([], [], system="s")

    message = str(exc_info.value)
    assert "falla A" in message
    assert "falla B" in message


def test_does_not_switch_once_primary_has_streamed_text():
    # El principal ya ha enseñado texto antes de fallar a mitad de turno:
    # mezclarlo con la respuesta del respaldo sería peor que propagar el error.
    primary = _FakeClient(
        "principal", error=LLMError("corte a mitad"), text_before_error="iba a decir..."
    )
    fallback = _FakeClient("respaldo", response=LLMResponse(text="no debería llamarse"))
    client = FallbackLLMClient(primary, fallback)

    seen = []
    with pytest.raises(LLMError, match="corte a mitad"):
        client.chat([], [], system="s", on_text=seen.append)

    assert seen == ["iba a decir..."]
    assert fallback.calls == 0
