"""Cabecera compartida de pantalla: escudo del Baskonia + título.

Antes solo `screens/plantilla.py` mostraba el escudo (pequeño, 48px, junto al
título) y las otras dos pantallas arrancaban directo con `st.title(...)`.
Factorizado aquí para que el escudo salga en las tres pantallas y con el
mismo tamaño — pedido de usuario, ver conversación: "quiero que se vea el
escudo del baskonia en todas las paginas y mas grande". El escudo en sí es
un asset fijo de la app, no depende de la BD (ver `components.branding`).
"""
import streamlit as st

from components.branding import crest_data_uri

_CREST_SIZE = 96  # antes 48px en plantilla.py, luego 64px — más grande, a petición (dos rondas)


def page_header(title: str) -> None:
    """Escudo del Baskonia + `st.title(title)`, en la misma fila."""
    crest_col, title_col = st.columns([1, 8])
    with crest_col:
        st.markdown(
            f'<img src="{crest_data_uri()}" alt="Baskonia" '
            f'style="width:{_CREST_SIZE}px;height:{_CREST_SIZE}px;object-fit:contain;display:block" />',
            unsafe_allow_html=True,
        )
    with title_col:
        st.title(title)
