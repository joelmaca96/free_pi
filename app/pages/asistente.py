"""Pantalla — Asistente: chat de scouting sobre los datos cargados.

Diseño completo en `local/features/005-chatbot/01_design.md` §1 y §9. Lo que
esta pantalla garantiza, y que es lo que la separa de un chat cualquiera:

- **Procedencia siempre visible**: de qué tabla y de qué partido/temporada
  sale cada respuesta, en un pie discreto.
- **Traza auditable**: qué herramienta se llamó y qué devolvió, plegable y
  conservada en el historial. En scouting el usuario quiere poder desconfiar.
- **Aviso de cifras sin verificar**: si un número de la respuesta no aparece
  en ningún resultado de herramienta, se dice. No se bloquea la respuesta —
  se hace visible (§7.2).

Y lo que NO hace: si no hay proveedor configurado o no responde, la pestaña
se explica con un `st.info` y las otras cuatro siguen funcionando. El
asistente no es un requisito de arranque de la interfaz.
"""
import datetime as dt

import streamlit as st

from assistant import budget, feedback, prompt as prompt_module, render
from assistant.agent import Agent
from assistant.capabilities import probe
from assistant.llm import LLMError, build_llm_client, provider_label
from assistant.tools import ToolCatalog
from assistant.tools.base import ToolContext
from components.header import page_header
from data import queries
from data.db import get_read_engine

# Una por familia (§1): enseñan de qué sabe el asistente mejor que cualquier
# texto de ayuda, y las tres primeras son literalmente las tres preguntas del
# encargo — incluida la que hoy puede no ser contestable, a propósito: ver
# cómo la rechaza es parte de saber en qué se puede confiar.
_SUGGESTIONS = [
    "¿Qué tal jugó Howard su último partido?",
    "¿Qué estilo de juego tiene el próximo rival?",
    "¿Cuál es el mejor quinteto para los últimos minutos?",
    "¿Quién anota más en la ACB esta temporada?",
]

engine = get_read_engine()
season_id = st.session_state["season_id"]
own_team_id = queries.get_own_team_id(engine)
today = dt.date.today()

page_header("Asistente")


@st.cache_resource(ttl=3600, show_spinner=False)
def _capabilities(_engine, marker: str):
    """Sondeo de capacidades cacheado (§7.3).

    `marker` entra en la clave de caché para que el sondeo se rehaga si cambia
    la base de datos configurada, y no para nada más.

    El TTL de una hora —el mismo criterio que `data/queries.py`— es lo que
    hace real la promesa de §7.3: la ingesta corre por su cuenta (systemd
    timer), y cuando reingiere aparecen columnas, tablas y vistas nuevas. Sin
    caducidad, el asistente seguiría diciendo "no tengo tiros libres" hasta
    que alguien reiniciase el contenedor.
    """
    return probe(_engine)


capabilities = _capabilities(engine, str(engine.url))
seasons = queries.list_seasons(engine)
season_label = seasons.set_index("id")["label"].get(season_id, str(season_id))
own_team_name = queries.team_name(engine, own_team_id) or own_team_id

st.session_state.setdefault("assistant_history", [])
st.session_state.setdefault("assistant_messages", [])
st.session_state.setdefault("assistant_questions", 0)

# ------------------------------------------------------------------ cabecera --

header_col, actions_col = st.columns([4, 1])
with header_col:
    st.caption(
        f"Modelo: **{provider_label()}** · Temporada: **{season_label}** · "
        f"{capabilities.counts.get('games', 0)} partidos cargados"
    )
with actions_col:
    if st.button("Vaciar chat", use_container_width=True):
        st.session_state["assistant_history"] = []
        st.session_state["assistant_messages"] = []
        st.session_state["assistant_questions"] = 0
        st.rerun()

try:
    client = build_llm_client()
except LLMError as exc:
    st.error(str(exc))
    st.stop()

if client is None:
    st.info(
        "El asistente no tiene proveedor de modelo configurado. Define `ASSISTANT_LLM_BASE_URL` "
        "y `ASSISTANT_LLM_MODEL` (y `ASSISTANT_LLM_API_KEY` si el proveedor la pide) en el `.env` "
        "— ver `.env.example`. El resto de la interfaz funciona igual sin esto."
    )
    st.stop()

# --------------------------------------------------------------- historial ---


def _render_turn(entry: dict, index: int) -> None:
    """Pinta un turno ya respondido (texto, artefactos, traza y procedencia)."""
    st.markdown(entry["text"])
    render.render_artifacts(entry.get("artifacts") or [], engine)

    if entry.get("unverified"):
        st.warning(
            "Cifras sin verificar contra los datos consultados: "
            f"{', '.join(entry['unverified'])}. Compruébalas en la traza antes de usarlas.",
            icon=":material/warning:",
        )
    if entry.get("provenance"):
        st.caption(entry["provenance"])
    if entry.get("trace"):
        with st.expander("Cómo lo he consultado", expanded=False):
            for line in entry["trace"]:
                st.markdown(f"- {line}")

    thumbs_up, thumbs_down, _ = st.columns([1, 1, 8])
    voted = entry.get("vote")
    if thumbs_up.button("👍", key=f"up_{index}", disabled=voted is not None):
        _record_vote(entry, index, True)
    if thumbs_down.button("👎", key=f"down_{index}", disabled=voted is not None):
        _record_vote(entry, index, False)
    if voted is not None:
        st.caption("Gracias — este voto entra en el corpus de evaluación." if voted else "Anotado.")


def _record_vote(entry: dict, index: int, helpful: bool) -> None:
    feedback.record(
        question=entry["question"],
        answer=entry["text"],
        helpful=helpful,
        tools=entry.get("tools") or [],
        unverified_numbers=entry.get("unverified") or [],
        provider=provider_label(),
    )
    st.session_state["assistant_history"][index]["vote"] = helpful
    st.rerun()


for index, entry in enumerate(st.session_state["assistant_history"]):
    with st.chat_message("user"):
        st.markdown(entry["question"])
    with st.chat_message("assistant"):
        _render_turn(entry, index)

# ----------------------------------------------------------------- entrada ---

if not st.session_state["assistant_history"]:
    st.caption("Prueba con una de estas:")
    for column, suggestion in zip(st.columns(len(_SUGGESTIONS)), _SUGGESTIONS):
        if column.button(suggestion, use_container_width=True, key=f"chip_{suggestion[:20]}"):
            st.session_state["assistant_pending"] = suggestion
            st.rerun()

# Entrada contextual desde otras pantallas (§9.4): "Partidos anteriores" y el
# modal de jugador dejan aquí la pregunta ya escrita antes de saltar.
pending = st.session_state.pop("assistant_pending", None)
typed = st.chat_input("Pregunta sobre jugadores, rivales, quintetos o la liga...")
question = typed or pending

if not question:
    st.stop()

status = budget.check(st.session_state["assistant_questions"], today=today)
if not status.allowed:
    st.warning(status.reason)
    st.stop()

with st.chat_message("user"):
    st.markdown(question)

extra_context = st.session_state.pop("assistant_context", None)
system_prompt = prompt_module.build_system_prompt(
    capabilities,
    season_label=season_label,
    own_team=own_team_name,
    today=today.isoformat(),
    extra_context=extra_context,
)
catalog = ToolCatalog(
    ToolContext(
        engine=engine,
        season_id=season_id,
        own_team_id=own_team_id,
        today=today,
        capabilities=capabilities,
    )
)
agent = Agent(client, catalog, system_prompt)

with st.chat_message("assistant"):
    trace_lines = []
    trace_box = st.status("Consultando los datos...", expanded=False)
    text_slot = st.empty()
    buffer = []

    def _on_text(piece: str) -> None:
        buffer.append(piece)
        text_slot.markdown("".join(buffer) + "▌")

    def _on_text_reset() -> None:
        # El modelo puede escribir antes de pedir una herramienta; eso es un
        # paso intermedio, no la respuesta (ver `Agent.run`).
        buffer.clear()
        text_slot.empty()

    def _on_tool_start(name: str, arguments: dict) -> None:
        pretty = ", ".join(f"{k}={v!r}" for k, v in arguments.items()) or "sin argumentos"
        trace_box.write(f"**{name}**({pretty})")

    def _on_tool_end(invocation) -> None:
        line = f"`{invocation.name}` → {invocation.summary}"
        trace_lines.append(line)
        trace_box.write(f"→ {invocation.summary}")

    try:
        turn = agent.run(
            question,
            st.session_state["assistant_messages"],
            on_text=_on_text,
            on_text_reset=_on_text_reset,
            on_tool_start=_on_tool_start,
            on_tool_end=_on_tool_end,
        )
    except LLMError as exc:
        trace_box.update(label="Sin respuesta del modelo", state="error")
        text_slot.empty()
        st.error(str(exc))
        st.stop()

    trace_box.update(
        label=f"Cómo lo he consultado ({len(turn.invocations)} consultas)", state="complete"
    )
    text_slot.markdown(turn.text)

st.session_state["assistant_messages"] = turn.messages
st.session_state["assistant_questions"] += 1
budget.record(today=today)
st.session_state["assistant_history"].append(
    {
        "question": question,
        "text": turn.text,
        "artifacts": turn.artifacts,
        "trace": trace_lines,
        "provenance": render.provenance_line(turn.invocations),
        "unverified": turn.unverified_numbers,
        "tools": [inv.name for inv in turn.invocations],
        "vote": None,
    }
)
# Repintar deja el turno recién respondido dentro del historial normal, con
# sus artefactos y sus botones de voto — sin esto quedaría a medio pintar
# (texto sí, artefactos y voto no) hasta la siguiente interacción.
st.rerun()
