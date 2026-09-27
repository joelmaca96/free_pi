"""Pantalla — Similitud de jugadores (propuesta 11).

Diseño completo en `doc/features/propuestas/11_similitud_de_jugadores.md`.
El buscador para el uso (2) del documento (§1: fichajes y sustituciones) —
"se lesiona nuestro cuatro: ¿quién juega como él en esta liga?"— sobre TODA
la base de datos, no solo el equipo propio. El bloque compacto del uso (1)
(preparar al rival, "¿a quién se parece de los que ya hemos defendido?") vive
directamente en `components/player_dialog.py`, donde el usuario ya está
mirando al jugador; esta pantalla es para cuando se busca desde cero.

El cálculo (percentiles + reparto de tiro por zona -> distancia) vive en
`app/analytics/similarity.py`, sin Streamlit — aquí solo se leen los
controles y se pinta.
"""
import streamlit as st

from analytics import similarity as similarity_engine
from components import similarity as similarity_component
from components.header import page_header
from components.player_dialog import player_detail
from data import queries
from data.db import get_read_engine

engine = get_read_engine()
own_team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]

page_header("Similitud de jugadores")

st.caption(
    "Busca a quién se parece un jugador dentro de TODA la base de datos, de cualquier equipo o "
    "competición — para traducir a un rival desconocido a alguien que ya se ha defendido, o para "
    "buscar sustitutos de mercado ante una baja (§1 de la propuesta 11)."
)

vectors = similarity_component.league_vectors(engine, season_id)
if vectors.empty:
    st.info(
        "Ningún jugador llega a los 5 partidos por competición que exige el cálculo de percentiles "
        "esta temporada — prueba con otra temporada en el selector de la barra lateral."
    )
    st.stop()

# Preselección desde "Ver ficha" de un resultado, o desde el bloque compacto
# de `player_dialog.py` — mismo patrón de `st.session_state` que
# `components/ask_assistant.py::ask_assistant_button` usa para el asistente.
preselected = st.session_state.pop("similarity_preselect_player_id", None)

vectors_sorted = vectors.sort_values("name")
player_ids = vectors_sorted["player_id"].tolist()
player_labels = {row.player_id: f"{row.name} · {row.team_name}" for row in vectors_sorted.itertuples()}
default_index = player_ids.index(preselected) if preselected in player_ids else 0

player_id = st.selectbox(
    "Jugador de referencia",
    options=player_ids,
    index=default_index,
    format_func=lambda pid: player_labels.get(pid, pid),
    key="similarity_finder_player",
    help="Escribe para buscar por nombre. Cualquier jugador con al menos 5 partidos esta temporada, de "
    "cualquier equipo o competición.",
)

# ------------------------------------------------------------------ filtros --
col_method, col_minutes, col_topn = st.columns(3)
with col_method:
    method = similarity_component.method_control("similarity_finder_method")
with col_minutes:
    min_minutes = st.slider(
        "Minutos mínimos del candidato", 0, 1500, int(similarity_engine.MIN_MINUTES_RECOMMENDED), 50,
        key="similarity_finder_min_minutes",
        help=(
            "Por debajo de 300 los percentiles son ruido (§4 de la propuesta): con poca muestra, "
            "cualquiera puede aparecer 'parecido' a cualquiera."
        ),
    )
with col_topn:
    top_n = st.slider("Cuántos mostrar", 3, 20, 10, key="similarity_finder_top_n")

col_comp, col_faced = st.columns(2)
with col_comp:
    competitions = queries.list_competitions(engine)
    comp_options = [None] + competitions["id"].tolist()
    comp_labels = {None: "Todas"}
    comp_labels.update(dict(zip(competitions["id"], competitions["name"])))
    competition_id = st.selectbox(
        "Competición", options=comp_options, format_func=lambda c: comp_labels.get(c, c),
        key="similarity_finder_competition",
    )
with col_faced:
    own_team_name = queries.team_name(engine, own_team_id) or own_team_id
    only_faced = st.checkbox(
        f"Solo equipos a los que {own_team_name} ya se ha enfrentado esta temporada",
        key="similarity_finder_only_faced",
        help="El filtro que hace útil el uso de preparar al rival (§2): traducir a alguien desconocido a "
        "uno que el equipo YA ha defendido.",
    )

allowed_team_ids = None
if only_faced:
    allowed_team_ids = queries.opponent_team_ids(engine, own_team_id, season_id)
    if not allowed_team_ids:
        st.warning(f"{own_team_name} no tiene partidos registrados esta temporada: se ignora este filtro.")
        allowed_team_ids = None

weights = similarity_component.weight_controls("similarity_finder")

results = similarity_engine.most_similar(
    vectors, player_id, method=method, group_weights=weights, min_minutes=min_minutes,
    competition_id=competition_id, allowed_team_ids=allowed_team_ids, top_n=top_n,
)

target_row = vectors.loc[vectors["player_id"] == player_id].iloc[0]
labels = similarity_component.competition_labels(engine)
rows = similarity_component.build_result_rows(target_row, results, labels, group_weights=weights)

st.divider()
st.markdown(f"### Jugadores más parecidos a {target_row['name']}")


def _open_player(pid: str) -> None:
    st.session_state["_similarity_finder_open_player"] = pid


similarity_component.results_list(rows, on_open=_open_player)
similarity_component.similarity_caveat()

open_player_id = st.session_state.pop("_similarity_finder_open_player", None)
if open_player_id:
    player_detail(open_player_id)

# --------------------------------------------------------- comparar perfiles --
if rows:
    st.divider()
    st.markdown("**Comparar perfiles**")
    compare_labels = {r["player_id"]: r["name"] for r in rows}
    compare_id = st.selectbox(
        "Elige con quién comparar el gráfico de percentiles",
        options=list(compare_labels),
        format_func=lambda pid: compare_labels.get(pid, pid),
        key="similarity_finder_compare",
    )
    compare_row = vectors.loc[vectors["player_id"] == compare_id].iloc[0]
    chart = similarity_component.percentile_bars(
        target_row, compare_row, target_row["name"], compare_row["name"]
    )
    if chart is not None:
        st.altair_chart(chart, width="stretch")
