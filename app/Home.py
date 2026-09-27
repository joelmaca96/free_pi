"""Punto de entrada de la interfaz del Baskonia.

Uso (desde la raíz del repo):
    .venv/Scripts/streamlit.exe run app/Home.py

Define la navegación entre las dos pantallas (`st.navigation`/`st.Page`,
API nativa de Streamlit para apps multipágina) y comprueba una vez, al
arrancar, que el esquema de scouting existe — si no, para con un mensaje
claro en vez de dejar que cada página falle por su cuenta (criterio de
aceptación #4 de `local/features/001-interfaz-baskonia/01_design.md`).
"""
import logging

import streamlit as st

from components.branding import CREST_PATH
from data import queries
from data.db import get_read_engine

# Streamlit solo configura SUS propios loggers (`streamlit.logger.get_logger`,
# ver el paquete instalado) — el logger raíz de Python se queda sin handler,
# así que cualquier `logging.getLogger(__name__).info(...)` de este proyecto
# (p.ej. `assistant.agent`, el cronometraje del asistente) no aparece en la
# consola sin esto. `basicConfig` es un no-op si el raíz ya tiene handler, así
# que repetirlo en cada rerun de Streamlit es inofensivo.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

st.set_page_config(page_title="Baskonia · Scouting", page_icon=CREST_PATH, layout="wide")
# Escudo persistente arriba a la izquierda del sidebar en TODAS las páginas —
# `st.logo` se llama aquí (entrypoint, antes de `pg.run()`) precisamente para
# eso: fuera del `if`/`try` de más abajo, se ejecuta en cada rerun sea cual
# sea la página seleccionada.
st.logo(CREST_PATH, size="large")
# `st.logo(size="large")` es 32px como máximo — tope fijo de la API, no hay
# parámetro para pedir más (ver docstring de `st.logo`). Se sobreescribe por
# CSS para que el escudo del sidebar también se vea "más grande" (pedido de
# usuario). `[data-testid="stSidebarLogo"]` es el testid público que usa el
# propio Streamlit (ver `streamlit/static`), más estable que su clase
# generada (`st-emotion-cache-*`, cambia entre builds) — aun así esto es un
# override no soportado oficialmente: si una futura versión de Streamlit
# renombra el testid, este bloque deja de tener efecto (silenciosamente,
# sin romper nada) y el escudo vuelve a los 32px por defecto.
st.markdown(
    '<style>[data-testid="stSidebarLogo"] { height: 56px !important; width: 56px !important; }</style>',
    unsafe_allow_html=True,
)

try:
    engine = get_read_engine()
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

# Selector de temporada a nivel de aplicación: toda página que filtra por
# partido (récord, medias, carga de minutos, partidos anteriores) lee
# `st.session_state["season_id"]` en vez de asumir "la más reciente" — así
# se puede mirar una temporada pasada sin que se mezcle con la actual (ver
# local/features/002-ajustes-interfaz/01_design.md).
#
# Preseleccionada la temporada más reciente CON PARTIDOS JUGADOS, no la más
# reciente a secas: en cuanto la ingesta carga el calendario de la temporada
# siguiente, esa temporada existe en `seasons` con cero partidos en `games`,
# y arrancar ahí dejaba en blanco casi toda la aplicación (señales, carga de
# minutos, quintetos, faltas, on/off, similitud) aunque la temporada
# anterior estuviera entera en la base de datos. El calendario futuro no se
# pierde por esto: "Próximo rival" y "Calendario" leen `upcoming_matchups`
# sin filtrar por temporada (ver `queries.next_matchup`).
seasons_df = queries.list_seasons(engine)
season_labels = seasons_df.set_index("id")["label"].to_dict()
with_games = seasons_df.index[seasons_df["games"] > 0]
default_index = int(with_games[0]) if len(with_games) else 0

selected_season_id = st.sidebar.selectbox(
    "Temporada",
    options=list(season_labels.keys()),
    format_func=lambda sid: season_labels[sid],
    index=default_index,
)
st.session_state["season_id"] = selected_season_id

# OJO: el directorio se llama `screens/`, NO `pages/`. Streamlit auto-detecta
# cualquier carpeta literalmente llamada `pages/` junto al script de entrada y
# la trata como su navegación "v1" propia — cuando eso coincide con un
# `st.navigation()` manual como este, cada clic de página ejecuta SOLO el
# fichero de esa página, sin volver a correr este `Home.py` antes (así que
# `st.session_state["season_id"]`, puesto arriba, nunca llega a existir y
# cada pantalla revienta con KeyError). Renombrar a `screens/` evita el
# choque; si esto vuelve a llamarse `pages/` alguna vez, vuelve el bug.
pg = st.navigation(
    [
        st.Page("screens/estado_equipo.py", title="Estado del equipo", default=True),
        st.Page("screens/plantilla.py", title="Plantilla"),
        st.Page("screens/proximo_rival.py", title="Próximo rival"),
        st.Page("screens/partidos_anteriores.py", title="Partidos anteriores"),
        st.Page("screens/quintetos.py", title="On/off y duplas"),
        st.Page("screens/similitud.py", title="Similitud de jugadores"),
        # Chat de scouting sobre los datos cargados
        # (`local/features/005-chatbot/01_design.md`). Va la última a
        # propósito: es la única pestaña que depende de un servicio externo
        # (el proveedor de modelo), y si no está configurado se explica sola
        # sin afectar a las otras cuatro.
        st.Page("screens/asistente.py", title="Asistente"),
    ]
)
pg.run()
