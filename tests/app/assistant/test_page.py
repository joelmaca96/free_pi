"""Test de la página del chat de punta a punta, con `st.testing.v1.AppTest`.

Ejecuta el script real de `app/screens/asistente.py` (sin navegador y sin red)
con un cliente de LLM falso, y comprueba lo que hace que esta pantalla sea
una herramienta de scouting y no un chat cualquiera:

- la respuesta se pinta,
- el **artefacto** que pidió la herramienta se pinta debajo (mapa de tiros),
- la **procedencia** aparece en el pie,
- la **traza** queda guardada y consultable,
- y una cifra que no salga de ningún resultado de herramienta se **marca**.

Es el único test que cubre el cableado de la página (session_state, orden de
pintado, historial). Todo lo demás —herramientas, bucle, guardas— se prueba
sin Streamlit en los otros ficheros.
"""
import sys
from pathlib import Path

import pytest

_APP_DIR = Path(__file__).resolve().parents[3] / "app"

pytest.importorskip("streamlit.testing.v1", reason="AppTest necesita Streamlit >= 1.28")

from streamlit.testing.v1 import AppTest  # noqa: E402

from app.assistant.llm.base import LLMResponse, ToolCall  # noqa: E402

PAGE = str(_APP_DIR / "screens" / "asistente.py")
QUESTION = "¿Qué tal jugó Howard su último partido?"


class ScriptedLLM:
    """Pide `player_game` y luego redacta. Mismo `Protocol` que los adaptadores."""

    label = "falso · guion"

    def __init__(self, answer: str):
        self.answer = answer
        self.turns = 0

    def chat(self, messages, tools, *, system, on_text=None):
        self.turns += 1
        if self.turns == 1:
            return LLMResponse(
                tool_calls=[ToolCall(id="c1", name="player_game", arguments={"player_id": "howard"})]
            )
        if on_text is not None:
            on_text(self.answer)
        return LLMResponse(text=self.answer)


@pytest.fixture()
def page(engine, monkeypatch):
    """Página apuntada a la base de datos del fixture, no a `data/baskonia.db`.

    `create_scouting_engine` lee `config.DATABASE_URL` en cada llamada, así que
    basta con parchear el atributo del módulo. Y hay que vaciar
    `st.cache_resource`: `get_read_engine` cachea el engine por proceso y sin
    esto la página se quedaría con el de otro test.
    """
    import streamlit as st

    from packages.baskonia_core import config

    # `app/` en sys.path: es como arranca Streamlit de verdad (`streamlit run
    # app/Home.py`), y de lo que dependen los `from assistant import ...` y
    # `from data import ...` de la página.
    if str(_APP_DIR) not in sys.path:
        sys.path.insert(0, str(_APP_DIR))

    monkeypatch.setattr(config, "DATABASE_URL", str(engine.url))
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _run(answer: str) -> AppTest:
    import assistant.llm as llm_module

    llm_module.build_llm_client = lambda: ScriptedLLM(answer)
    app = AppTest.from_file(PAGE, default_timeout=90)
    app.session_state["season_id"] = 1
    app.run()
    return app


def test_without_a_provider_the_tab_explains_itself_and_does_not_break(page, monkeypatch):
    """El asistente no es un requisito de arranque: sin proveedor, un `st.info`
    y las otras cuatro pestañas siguen funcionando (§9.1)."""
    import assistant.llm as llm_module

    llm_module.build_llm_client = lambda: None
    app = AppTest.from_file(PAGE, default_timeout=60)
    app.session_state["season_id"] = 1
    app.run()

    assert not app.exception
    assert any("no tiene proveedor de modelo configurado" in info.value for info in app.info)


def test_the_header_says_which_model_is_behind(page):
    """Con configuración por entorno, no saber contra qué modelo hablas es una
    fuente segura de confusión al comparar respuestas (§9.1)."""
    app = _run("Howard hizo 21 puntos.")
    assert any("Modelo:" in caption.value for caption in app.caption)


def test_an_answer_shows_text_artifact_provenance_and_trace(page):
    app = _run("Howard hizo 21 puntos en 20.5 minutos con un 83.3% de eFG.")
    app.chat_input[0].set_value(QUESTION).run()

    assert not app.exception
    texts = [markdown.value for markdown in app.markdown]
    captions = [caption.value for caption in app.caption]

    assert QUESTION in texts                                   # la pregunta
    assert any("21 puntos" in text for text in texts)          # la respuesta
    assert any("tiros de campo" in caption for caption in captions)   # el mapa de tiros
    assert any(caption.startswith("Fuente:") for caption in captions)  # la procedencia
    assert any("`player_game`" in text for text in texts)      # la traza


def test_a_figure_that_no_tool_returned_is_flagged(page):
    """El verificador marca, no bloquea: la respuesta llega entera y con aviso (§7.2)."""
    app = _run("Howard hizo 21 puntos y capturó 47 rebotes.")
    app.chat_input[0].set_value(QUESTION).run()

    assert any("47 rebotes" in markdown.value for markdown in app.markdown)
    assert any("Cifras sin verificar" in warning.value for warning in app.warning)


def test_the_budget_stops_the_assistant_with_a_clear_message(page, monkeypatch):
    monkeypatch.setenv("ASSISTANT_SESSION_LIMIT", "1")
    app = _run("Howard hizo 21 puntos.")
    app.session_state["assistant_questions"] = 5
    app.chat_input[0].set_value(QUESTION).run()

    assert any("tope de 1 preguntas" in warning.value for warning in app.warning)
    # Y no se ha llegado a preguntar nada al modelo.
    assert not app.session_state["assistant_history"]


def test_a_question_prefilled_from_another_screen_is_answered(page):
    """Entrada contextual de §9.4: "Partidos anteriores" y la ficha de jugador
    dejan aquí la pregunta antes de saltar."""
    app = _run("Howard hizo 21 puntos.")
    app.session_state["assistant_pending"] = QUESTION
    app.session_state["assistant_context"] = "viene del detalle del partido g5"
    app.run()

    assert not app.exception
    assert app.session_state["assistant_history"][0]["question"] == QUESTION


def test_clearing_the_chat_empties_the_history(page):
    app = _run("Howard hizo 21 puntos.")
    app.chat_input[0].set_value(QUESTION).run()
    assert app.session_state["assistant_history"]

    next(button for button in app.button if button.label == "Vaciar chat").click().run()

    assert app.session_state["assistant_history"] == []
    assert app.session_state["assistant_messages"] == []
