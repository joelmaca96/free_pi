"""Botón "Preguntar al asistente" con el contexto de la pantalla ya puesto.

Es el atajo que convierte el chat en parte del flujo de trabajo en vez de en
una pestaña que hay que acordarse de visitar
(`local/features/005-chatbot/01_design.md` §9.4): desde el detalle de un
partido o desde la ficha de un jugador se salta al asistente con la pregunta
escrita y con una nota de contexto para el prompt, y la primera respuesta ya
va sobre ese partido o ese jugador.

Vive en `components/` y no en cada página por el mismo motivo que
`player_dialog.py`: lo usan dos pantallas y el comportamiento tiene que ser
idéntico en las dos.
"""
from typing import Optional

import streamlit as st

_ASSISTANT_PAGE = "pages/asistente.py"


def ask_assistant_button(
    question: str,
    *,
    key: str,
    context: Optional[str] = None,
    label: str = "Preguntar al asistente",
    width: str = "stretch",
) -> None:
    """Botón que abre el asistente con `question` ya lanzada.

    Args:
        question: la pregunta que se enviará tal cual al asistente.
        key: clave del botón (única por pantalla y por entidad).
        context: nota de una línea para el prompt de sistema ("el usuario
            viene del detalle del partido acb-104714"). Ayuda al modelo a
            interpretar un "y en el segundo cuarto?" posterior sin que el
            usuario tenga que repetir de qué partido habla.
    """
    if st.button(label, key=key, width=width):
        st.session_state["assistant_pending"] = question
        if context:
            st.session_state["assistant_context"] = context
        st.switch_page(_ASSISTANT_PAGE)
