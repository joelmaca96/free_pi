"""Pantalla — Plantilla: galería de jugadores + detalle a un clic.

Sustituye a la tabla "Plantilla y medias por jugador" que vivía en
"Estado del equipo" (ver `local/features/003-vista-plantilla/01_design.md`
§2). Contrato de datos completo en ese mismo diseño, §4/§5/§9.

El modal de detalle ya no se define aquí: vive en
`components/player_dialog.py`, para poder abrirlo también desde "Próximo
rival" sobre jugadores del equipo contrario (ver
`local/features/004-proximo-rival/01_design.md` §3.2). Esta página se
comporta exactamente igual que antes de esa extracción.
"""
import pandas as pd
import streamlit as st

from components.avatar import player_avatar_html
from components.header import page_header
from components.player_dialog import player_detail
from data import queries
from data.db import get_read_engine

engine = get_read_engine()
team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]

# ============================================================== galería ==

page_header("Plantilla")

roster_df = queries.roster_cards(engine, team_id, season_id)

if roster_df.empty:
    st.info("No hay jugadores activos en la plantilla.")
    st.stop()

_N_COLS = 4
rows = [roster_df.iloc[i : i + _N_COLS] for i in range(0, len(roster_df), _N_COLS)]
for row_df in rows:
    cols = st.columns(_N_COLS)
    for col, player in zip(cols, row_df.itertuples()):
        with col:
            with st.container(border=True):
                st.markdown(
                    f'<div style="text-align:center">'
                    f'{player_avatar_html(player.name, player.photo_url, local_path=player.photo_local_path)}'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**#{player.number} · {player.name}**")
                st.caption(player.position or "—")
                if pd.isna(player.pts_avg):
                    st.caption("Sin partidos todavía")
                elif int(player.stats_season_id) != season_id:
                    # Fallback a la última temporada con datos de este jugador
                    # (ver `queries.roster_cards`) — se avisa en vez de colar
                    # el dato como si fuera de la temporada en curso.
                    st.caption(f"{player.pts_avg:.1f} pts/partido · {player.stats_season_label}")
                else:
                    st.caption(f"{player.pts_avg:.1f} pts/partido")
                if st.button("Ver estadísticas", key=f"detail_{player.id}", use_container_width=True):
                    st.session_state["selected_player_id"] = player.id
                    player_detail(player.id)
