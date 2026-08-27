"""Pantalla B — Partidos anteriores.

Selector de partidos ya disputados del Baskonia + detalle del partido
elegido (marcador, parciales, avanzadas, boxscore, tiros, quintetos,
eventos clave). Contrato de datos completo en
`local/features/001-interfaz-baskonia/01_design.md` §4.
"""
import datetime as dt

import altair as alt
import streamlit as st

from components.ask_assistant import ask_assistant_button
from components.court import shot_chart, shot_chart_caption
from components.header import page_header
from data import queries
from data.db import get_read_engine

_ACCENT = "#008300"
_MUTED = "#898781"

engine = get_read_engine()
team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]
today = dt.date.today()

page_header("Partidos anteriores")

list_col, detail_col = st.columns([1, 2], gap="large")

with list_col:
    st.subheader("Selecciona un partido")
    competitions = queries.list_competitions(engine)
    comp_choice = st.selectbox("Competición", options=["Todas"] + competitions["name"].tolist())
    competition_id = None
    if comp_choice != "Todas":
        competition_id = int(competitions.loc[competitions["name"] == comp_choice, "id"].iloc[0])

    # Temporada de la que sacar los partidos: la seleccionada, o la última con
    # partidos disputados si la seleccionada aún no tiene ninguno todavía (recién
    # arrancada, ver `queries.team_scouting_season` — mismo caso real que resuelve
    # para el rival en "Próximo rival", aquí aplicado al propio Baskonia).
    season_info = queries.team_scouting_season(engine, team_id, season_id)
    games_season_id = season_info["season_id"] if season_info else season_id
    games_df = queries.list_past_games(engine, team_id, games_season_id, today, competition_id)

    if games_df.empty:
        st.info("No hay partidos ya disputados para este filtro todavía.")
        st.stop()

    if season_info and season_info["is_fallback"]:
        st.info(
            f"La temporada seleccionada todavía no tiene partidos disputados — se muestran "
            f"los de **{season_info['label']}**, la última temporada con partidos."
        )

    display_df = games_df.copy()
    display_df["Resultado"] = display_df.apply(
        lambda r: f"{'V' if r['pts_favor'] > r['pts_contra'] else 'D'}  {r['rival']}  "
        f"{r['pts_favor']}–{r['pts_contra']}",
        axis=1,
    )
    event = st.dataframe(
        display_df[["game_date", "competition", "condicion", "rival_logo_url", "Resultado"]],
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "game_date": st.column_config.TextColumn("Fecha"),
            "competition": st.column_config.TextColumn("Comp."),
            "condicion": st.column_config.TextColumn("Cond."),
            # Pegado a "Resultado" (que ya empieza por el nombre del rival, tras
            # V/D) — Streamlit no permite combinar imagen+texto en una misma celda.
            "rival_logo_url": st.column_config.ImageColumn(" ", width="small"),
            "Resultado": st.column_config.TextColumn("Resultado"),
        },
    )
    selected_rows = event.selection.rows if event and event.selection else []
    selected_idx = selected_rows[0] if selected_rows else 0
    selected_game = games_df.iloc[selected_idx]

with detail_col:
    game_id = selected_game["id"]
    rival_team_id = selected_game["rival_team_id"]
    rival_name = selected_game["rival"]
    gano = selected_game["pts_favor"] > selected_game["pts_contra"]

    st.subheader(f"Baskonia {'vs' if selected_game['condicion'] == 'Local' else '@'} {selected_game['rival']}")
    score_l, sep, score_r = st.columns([2, 1, 2])
    with score_l:
        st.metric("Baskonia", int(selected_game["pts_favor"]))
    with sep:
        st.markdown(f"<div style='text-align:center;padding-top:14px'>{'🟢 Victoria' if gano else '⚪ Derrota'}</div>", unsafe_allow_html=True)
    with score_r:
        st.metric(selected_game["rival"], int(selected_game["pts_contra"]))
    st.caption(f"{selected_game['competition']} · {selected_game['game_date']} · {selected_game['condicion']}")

    # Entrada contextual al chat (ver `local/features/005-chatbot/01_design.md`
    # §9.4): se salta al asistente con la pregunta escrita y con el partido ya
    # dicho, para no tener que repetirlo en la conversación.
    ask_assistant_button(
        f"Resume el partido contra {rival_name} del {selected_game['game_date']}: "
        "qué pasó, quién destacó y en qué se decidió.",
        key=f"ask_game_{game_id}",
        context=(
            f"El usuario viene del detalle del partido {game_id} "
            f"({selected_game['game_date']}, {selected_game['competition']}, "
            f"Baskonia contra {rival_name})."
        ),
        label="Preguntar al asistente sobre este partido",
    )

    tab_resumen, tab_box, tab_tiros, tab_quintetos = st.tabs(
        ["Resumen", "Boxscore", "Tiros", "Quintetos"]
    )

    with tab_resumen:
        quarters_df = queries.game_quarter_stats(engine, game_id)
        if quarters_df.empty:
            st.info("Este partido no tiene parciales por cuarto registrados (limitación de la fuente).")
        else:
            quarters_df = quarters_df.assign(is_us=quarters_df["team_id"] == team_id)
            chart = (
                alt.Chart(quarters_df)
                .mark_bar()
                .encode(
                    x=alt.X("quarter:O", title="Cuarto"),
                    y=alt.Y("points_for:Q", title="Puntos"),
                    color=alt.Color(
                        "is_us:N",
                        scale=alt.Scale(domain=[True, False], range=[_ACCENT, _MUTED]),
                        legend=None,
                    ),
                    xOffset="team_name:N",
                    tooltip=[alt.Tooltip("team_name:N", title="Equipo"), alt.Tooltip("points_for:Q", title="Puntos")],
                )
                .properties(height=220)
            )
            st.altair_chart(chart, use_container_width=True)
            st.caption(f"🟢 Baskonia · ⚪ {selected_game['rival']}")

        adv_df = queries.game_advanced_stats(engine, game_id)
        if adv_df.empty:
            st.info("Sin estadísticas avanzadas para este partido.")
        else:
            # `ft_pct` no viene calculado de la tabla (solo `ftm`/`fta` en bruto,
            # ver `queries.game_advanced_stats`): `.where(fta > 0)` deja en NaN
            # tanto el 0 tiros libres como el `NULL` de partidos cargados antes
            # de que existiera la columna — mismo criterio que ya usa el resto
            # de métricas de esta tabla para "sin dato" (`notna()` más abajo).
            adv_df = adv_df.assign(ft_pct=(100 * adv_df["ftm"] / adv_df["fta"]).where(adv_df["fta"] > 0))
            us_row = adv_df[adv_df["team_id"] == team_id]
            them_row = adv_df[adv_df["team_id"] != team_id]
            metrics = [
                ("ortg", "ORtg"), ("efg_pct", "eFG%"), ("ts_pct", "TS%"),
                ("tov_pct", "TOV%"), ("orb_pct", "ORB%"), ("ft_pct", "FT%"),
            ]
            st.markdown("**Avanzadas** (Baskonia · rival)")
            for key, label in metrics:
                us_val = us_row[key].iloc[0] if not us_row.empty and us_row[key].notna().iloc[0] else None
                them_val = them_row[key].iloc[0] if not them_row.empty and them_row[key].notna().iloc[0] else None
                c1, c2 = st.columns([1, 3])
                c1.caption(label)
                c2.markdown(
                    f"**{us_val:.1f}**&nbsp;&nbsp;·&nbsp;&nbsp;:gray[{them_val:.1f}]"
                    if us_val is not None and them_val is not None
                    else "—"
                )

    with tab_box:
        def _render_boxscore(box_df):
            if box_df.empty:
                st.info("Sin boxscore para este equipo en este partido (hueco de datos en origen).")
                return
            st.dataframe(
                box_df,
                hide_index=True,
                use_container_width=True,
                column_order=["player_name", "minutes", "pts", "reb", "ast", "efg_pct"],
                column_config={
                    "player_name": st.column_config.TextColumn("Jugador"),
                    "minutes": st.column_config.NumberColumn("Min", format="%.1f"),
                    "pts": st.column_config.NumberColumn("Pts"),
                    "reb": st.column_config.NumberColumn("Reb"),
                    "ast": st.column_config.NumberColumn("Ast"),
                    "efg_pct": st.column_config.NumberColumn("eFG%", format="%.1f"),
                    "player_id": None,
                    "team_id": None,
                },
            )

        st.markdown("**Baskonia**")
        _render_boxscore(queries.game_boxscore(engine, game_id, team_id))
        st.divider()
        st.markdown(f"**{rival_name}**")
        _render_boxscore(queries.game_boxscore(engine, game_id, rival_team_id))

    with tab_tiros:
        shots_df = queries.game_shots(engine, game_id, team_id)
        if shots_df.empty:
            st.info("Sin tiros con coordenadas para este partido.")
        else:
            players = ["Todos"] + sorted(shots_df["player_name"].unique().tolist())
            # Clave con `game_id`: si no, al cambiar de partido en la lista el
            # selector conserva por key un jugador que puede no existir en la
            # lista de opciones del partido nuevo (Streamlit lo rechaza).
            player_choice = st.selectbox("Jugador", options=players, key=f"tiros_player_filter_{game_id}")
            filtered_df = shots_df if player_choice == "Todos" else shots_df[shots_df["player_name"] == player_choice]

            zones_df = queries.court_zones(engine)
            # Sin `use_container_width`: `shot_chart` ya fija su propio ancho (dominio
            # cuadrado) — estirarlo al contenedor aplana la cancha (ver `court.py`).
            st.altair_chart(shot_chart(filtered_df, zones_df))
            st.caption(shot_chart_caption(filtered_df))

    with tab_quintetos:
        def _render_lineups(lineups_df):
            if lineups_df.empty:
                st.info("Sin quintetos reconstruidos para este equipo en este partido.")
                return
            st.dataframe(
                lineups_df,
                hide_index=True,
                use_container_width=True,
                column_order=["jugadores", "minutes", "plus_minus"],
                column_config={
                    "jugadores": st.column_config.TextColumn("Quinteto", width="large"),
                    "minutes": st.column_config.NumberColumn("Min", format="%.1f"),
                    "plus_minus": st.column_config.NumberColumn("+/-"),
                    "lineup_id": None,
                },
            )

        st.markdown("**Baskonia**")
        _render_lineups(queries.game_lineups(engine, game_id, team_id))
        st.divider()
        st.markdown(f"**{rival_name}**")
        _render_lineups(queries.game_lineups(engine, game_id, rival_team_id))

        st.divider()
        events_df = queries.game_key_events(engine, game_id)
        if not events_df.empty:
            st.markdown("**Eventos clave**")
            for row in events_df.itertuples():
                st.caption(f"{row.quarter} · {row.game_clock} — {row.label} ({row.team_name})")
