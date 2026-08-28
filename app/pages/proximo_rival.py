"""Pantalla — Próximo rival: todo el scouting del siguiente equipo a enfrentar.

Diseño completo en `local/features/004-proximo-rival/01_design.md`. Amplía a
informe completo lo que "Estado del equipo" resume en una tarjeta (rival,
fecha, competición, condición): balance y forma, perfil avanzado, cara a
cara, jugadores clave, quintetos, parciales por cuarto y mapa de tiros.

Funciona igual para un rival de ACB/Copa del Rey/Supercopa que de Euroliga:
ninguna consulta filtra por competición ni por fuente, todas trabajan sobre
`team_id` (ver §10 del diseño). La única asimetría real entre fuentes es el
origen de ORtg/DRtg/pace — oficial en ACB, estimación propia en Euroliga —,
etiquetada en la pestaña correspondiente del perfil avanzado, no escondida.

DOS TEMPORADAS EN JUEGO, a propósito: la del PARTIDO (la seleccionada en el
panel lateral, de donde sale `upcoming_matchups`) y la de los DATOS de
scouting (`scouting_season_id`), que puede ser anterior si el rival aún no ha
jugado nada en la seleccionada — ver `queries.team_scouting_season` para el
caso real que lo motivó. Todo lo acotado por temporada usa la segunda; el
cara a cara no usa ninguna (el historial no caduca).
"""
import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from components.ask_assistant import ask_assistant_button
from components.avatar import player_avatar_html, team_crest_html
from components.court import shot_chart, shot_chart_caption, zone_breakdown, zone_heatmap, zone_heatmap_caption
from components.header import page_header
from components.player_dialog import player_detail
from data import queries
from data.db import get_read_engine

_ACCENT = "#008300"   # verde Baskonia — puntos anotados por el rival
_MUTED = "#898781"    # ink muted — puntos encajados

engine = get_read_engine()
own_team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]
today = dt.date.today()

page_header("Próximo rival")

# --------------------------------------------------------------- el rival --
# Mismo criterio que la tarjeta de "Estado del equipo": un calendario futuro
# solo tiene sentido con la temporada más reciente seleccionada — una
# temporada ya cerrada no tiene "próximo partido".
if not st.session_state.get("is_current_season", True):
    st.info(
        "El próximo rival solo aplica a la temporada en curso. "
        "Cambia el selector de temporada del panel lateral para verlo."
    )
    st.stop()

matchup = queries.next_matchup(engine, season_id, today)
if matchup is None:
    st.info(
        "No hay ningún partido futuro cargado para esta temporada todavía. "
        "El calendario lo pueblan `ingest/acb` e `ingest/euroleague` (`run_upcoming`)."
    )
    st.stop()

rival_team_id = matchup["opponent_team_id"]
rival_name = matchup["opponent"]
condicion = "Local" if matchup["is_home"] else "Visitante"
fecha = dt.date.fromisoformat(str(matchup["match_date"])).strftime("%d %b %Y")

# ------------------------------------------------------------- cabecera --
crest_col, info_col = st.columns([1, 6])
with crest_col:
    st.markdown(team_crest_html(rival_name, matchup.get("opponent_logo_url"), size=88), unsafe_allow_html=True)
with info_col:
    st.subheader(f"Baskonia {'vs' if matchup['is_home'] else '@'} {rival_name}")
    st.caption(f"{fecha} · {matchup['competition']} · {condicion}")
    if matchup.get("key_player_note"):
        st.caption(matchup["key_player_note"])
    # Entrada contextual al chat (ver `local/features/005-chatbot/01_design.md`
    # §9.4), la misma que en "Partidos anteriores" pero mirando hacia adelante:
    # se salta al asistente con la pregunta escrita y con el rival ya dicho,
    # para no tener que repetirlo en la conversación.
    #
    # Va aquí, en la cabecera, y no más abajo: es lo único de la pantalla que
    # depende solo de que HAYA próximo partido, no de que el rival tenga datos
    # de scouting — cuando no los tiene, la página se corta con `st.stop()`
    # antes de la primera sección y el botón seguiría teniendo sentido.
    ask_assistant_button(
        f"Prepara el partido contra {rival_name} del {fecha}: "
        "cómo juegan, quién es su amenaza y qué hay que cuidar.",
        key=f"ask_next_game_{rival_team_id}",
        context=(
            f"El usuario viene de la pantalla del próximo rival: {rival_name} "
            f"({fecha}, {matchup['competition']}, Baskonia como {condicion.lower()})."
        ),
        label="Preguntar al asistente sobre este partido",
        width="content",
    )

# De qué temporada salen los datos de scouting. NO tiene por qué ser la
# seleccionada: al arrancar una temporada, el rival del primer partido aún no
# ha jugado nada en ella y todo el scouting acotado a esa temporada sale
# vacío — el dato útil está en la anterior (ver `queries.team_scouting_season`,
# que documenta el caso real que lo motivó). Se cae a esa, avisando.
scouting = queries.team_scouting_season(engine, rival_team_id, season_id)
scouting_season_id = None if scouting is None else scouting["season_id"]

if scouting is None:
    st.warning(
        f"No hay ningún partido de {rival_name} cargado en la base de datos, ni en esta "
        "temporada ni en anteriores — no se puede construir su scouting todavía. "
        "Solo se muestra el cara a cara histórico, si lo hay."
    )
elif scouting["is_fallback"]:
    st.warning(
        f"⚠ {rival_name} todavía no ha disputado partidos en la temporada seleccionada. "
        f"**Todo el scouting de abajo es de {scouting['label']}**, su última temporada con "
        "datos")
else:
    st.caption(f"Datos de scouting de la temporada {scouting['label']}.")


def render_head_to_head() -> None:
    """Sección de enfrentamientos directos.

    En su propia función porque es la ÚNICA sección que no depende de que el
    rival tenga partidos en ninguna temporada concreta (el historial no se
    acota por temporada), así que se pinta tanto en el flujo normal como
    cuando no hay nada más que enseñar — sin duplicar el código.
    """
    st.subheader("Cara a cara vs. Baskonia")
    h2h_df = queries.head_to_head(engine, own_team_id, rival_team_id)

    if h2h_df.empty:
        st.info(f"No hay enfrentamientos previos con {rival_name} en los datos cargados.")
        return

    wins = int((h2h_df["pts_favor"] > h2h_df["pts_contra"]).sum())
    losses = len(h2h_df) - wins
    display_h2h = h2h_df.copy()
    display_h2h["resultado"] = display_h2h.apply(
        lambda r: f"{'V' if r['pts_favor'] > r['pts_contra'] else 'D'}  {r['pts_favor']}–{r['pts_contra']}",
        axis=1,
    )
    st.metric("Balance del Baskonia", f"{wins}–{losses}")
    st.dataframe(
        display_h2h,
        hide_index=True,
        width="stretch",
        column_order=["game_date", "season_label", "competition", "condicion", "resultado"],
        column_config={
            "game_date": st.column_config.TextColumn("Fecha"),
            "season_label": st.column_config.TextColumn("Temporada"),
            "competition": st.column_config.TextColumn("Comp."),
            "condicion": st.column_config.TextColumn("Cond."),
            "resultado": st.column_config.TextColumn("Resultado"),
        },
    )
    st.caption(
        "Todas las temporadas y competiciones cargadas, no solo la seleccionada — "
        "el historial de enfrentamientos no caduca con la temporada. "
        "Cond./Resultado desde la perspectiva del Baskonia."
    )


st.divider()

if scouting_season_id is None:
    render_head_to_head()
    st.stop()

# ------------------------------------------------- balance y forma reciente --
st.subheader("Balance y forma reciente")

record_df = queries.team_record(engine, rival_team_id, scouting_season_id, today)
if record_df.empty:
    st.info(f"{rival_name} no tiene partidos disputados registrados en esa temporada.")
else:
    record_cols = st.columns(len(record_df))
    for col, row in zip(record_cols, record_df.itertuples()):
        col.metric(f"Récord {row.competition}", f"{row.wins}–{row.losses}")

# Últimos partidos del RIVAL contra quien sea (no solo contra el Baskonia) —
# es la lectura de forma; el cara a cara va en su propia sección más abajo.
recent_df = queries.list_past_games(engine, rival_team_id, scouting_season_id, today, competition_id=None, limit=8)
if recent_df.empty:
    st.caption("Sin partidos recientes para mostrar.")
else:
    display_df = recent_df.copy()
    display_df["resultado"] = display_df.apply(
        lambda r: f"{'V' if r['pts_favor'] > r['pts_contra'] else 'D'}  {r['pts_favor']}–{r['pts_contra']}",
        axis=1,
    )
    st.dataframe(
        display_df,
        hide_index=True,
        width="stretch",
        column_order=["game_date", "competition", "condicion", "rival", "resultado"],
        column_config={
            "game_date": st.column_config.TextColumn("Fecha"),
            "competition": st.column_config.TextColumn("Comp."),
            "condicion": st.column_config.TextColumn("Cond."),
            "rival": st.column_config.TextColumn("Contra"),
            "resultado": st.column_config.TextColumn("Resultado"),
        },
    )
    st.caption(f"Últimos {len(display_df)} partidos de {rival_name}, todas las competiciones.")

st.divider()

# ------------------------------------------------------------ perfil avanzado --
st.subheader("Perfil avanzado")
profile_df = queries.team_advanced_profile(engine, rival_team_id, scouting_season_id)

if profile_df.empty:
    st.info(
        f"Sin estadísticas avanzadas registradas para {rival_name} en esa temporada "
        "(ningún partido suyo tiene todavía `game_advanced_stats`)."
    )
else:
    tabs = st.tabs(profile_df["competition"].tolist())
    for tab, row in zip(tabs, profile_df.itertuples()):
        with tab:
            cols = st.columns(8)
            cols[0].metric("PJ", int(row.gp))
            cols[1].metric("Ritmo", f"{row.pace:.1f}" if pd.notna(row.pace) else "—")
            cols[2].metric("ORtg", f"{row.ortg:.1f}" if pd.notna(row.ortg) else "—")
            cols[3].metric("DRtg", f"{row.drtg:.1f}" if pd.notna(row.drtg) else "—")
            cols[4].metric("Net", f"{row.net_rating:+.1f}" if pd.notna(row.net_rating) else "—")
            cols[5].metric("eFG%", f"{row.efg_pct:.1f}" if pd.notna(row.efg_pct) else "—")
            cols[6].metric("TS%", f"{row.ts_pct:.1f}" if pd.notna(row.ts_pct) else "—")
            # `ft_pct` puede ser NaN con `gp` > 0 (partidos sin `ftm`/`fta`
            # cargados, ver el docstring de `team_advanced_profile`) — se
            # distingue de "0%", no se disimula el hueco de cobertura.
            cols[7].metric("FT%", f"{row.ft_pct:.1f}" if pd.notna(row.ft_pct) else "—")

            # Fase 0 (quick win, 2026-08-27): tasas de equipo ya cargadas que
            # ninguna query seleccionaba — % de canastas asistidas, presión
            # defensiva (robos/tapones por posesión rival), ratio AST/TOV.
            if pd.notna(getattr(row, "ast_pct", None)) or pd.notna(getattr(row, "stl_pct", None)):
                rate_cols = st.columns(5)
                rate_cols[0].metric("% Asistidas", f"{row.ast_pct:.1f}" if pd.notna(row.ast_pct) else "—")
                rate_cols[1].metric("% Robo", f"{row.stl_pct:.1f}" if pd.notna(row.stl_pct) else "—")
                rate_cols[2].metric("% Tapón", f"{row.blk_pct:.1f}" if pd.notna(row.blk_pct) else "—")
                rate_cols[3].metric("Tasa TL", f"{row.ft_rate:.1f}" if pd.notna(row.ft_rate) else "—")
                rate_cols[4].metric("AST/TOV", f"{row.ast_to_ratio:.2f}" if pd.notna(row.ast_to_ratio) else "—")

            # Boxscore ampliado de equipo (Fase 1): propio y CONCEDIDO.
            if pd.notna(getattr(row, "stl_avg", None)):
                box_cols = st.columns(4)
                box_cols[0].metric(
                    "Robos (propio·rival)",
                    f"{row.stl_avg:.1f} · {row.opp_stl_avg:.1f}" if pd.notna(row.opp_stl_avg) else f"{row.stl_avg:.1f}",
                )
                box_cols[1].metric(
                    "Tapones (propio·rival)",
                    f"{row.blk_avg:.1f} · {row.opp_blk_avg:.1f}" if pd.notna(row.opp_blk_avg) else f"{row.blk_avg:.1f}",
                )
                box_cols[2].metric(
                    "Pérdidas (propio·rival)",
                    f"{row.tov_avg:.1f} · {row.opp_tov_avg:.1f}" if pd.notna(row.opp_tov_avg) else f"{row.tov_avg:.1f}",
                )
                box_cols[3].metric(
                    "Faltas (propio·rival)",
                    f"{row.pf_avg:.1f} · {row.opp_pf_avg:.1f}" if pd.notna(row.opp_pf_avg) else f"{row.pf_avg:.1f}",
                )
            # La procedencia de ORtg/DRtg/ritmo NO es la misma en las dos fuentes y
            # eso cambia cómo hay que leer la cifra — se dice, no se disimula (ver
            # doc/features/ingestor/01_estado.md §2.2/§2.3).
            if row.competition == "Euroliga":
                st.caption(
                    "ORtg/DRtg/ritmo de Euroliga son **estimación propia** (fórmula Dean Oliver): "
                    "esa API no publica un endpoint de avanzadas oficial. No son directamente "
                    "comparables con los de ACB, que sí son el dato oficial de acb.com."
                )
            elif row.competition == "Combinado":
                st.caption(
                    "Media de todas las competiciones. Si el rival juega ACB y Euroliga, "
                    "mezcla dato oficial (ACB) y estimado (Euroliga) — mira cada pestaña por separado."
                )
            else:
                st.caption("ORtg/DRtg/ritmo oficiales de acb.com.")

st.divider()

# ---------------------------------------------------------------- cara a cara --
render_head_to_head()

st.divider()

# ------------------------------------------------------------- jugadores clave --
st.subheader("Jugadores clave")
roster_df = queries.roster_cards(engine, rival_team_id, scouting_season_id)

if roster_df.empty:
    st.info(f"Sin jugadores registrados para {rival_name}.")
else:
    # Por producción, no por dorsal (a diferencia de "Plantilla"): preparando un
    # partido lo primero es quién anota. Los que aún no tienen media (`NaN`) van al
    # final en vez de desaparecer — pueden ser altas recientes, no ausencias de dato.
    roster_df = roster_df.sort_values("pts_avg", ascending=False, na_position="last")

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
                    pir_bit = f" · PIR {player.pir_avg:.1f}" if pd.notna(getattr(player, "pir_avg", None)) else ""
                    st.caption(
                        f"{player.pts_avg:.1f} pts/partido{pir_bit}" if pd.notna(player.pts_avg) else "Sin partidos todavía"
                    )
                    if st.button("Ver estadísticas", key=f"rival_detail_{player.id}", width="stretch"):
                        player_detail(player.id)

    st.caption(
        "Ordenados por puntos por partido. Las fotos reales solo existen para la plantilla "
        "propia (`ingest/baskonia_web`); para un rival se muestra el badge con iniciales."
    )

st.divider()

# ------------------------------------------------------------------ quintetos --
st.subheader("Quintetos más utilizados")
competitions = queries.list_competitions(engine)
comp_choice = st.selectbox(
    "Competición", options=["Todas"] + competitions["name"].tolist(), key="rival_lineups_competition"
)
lineups_competition_id = None
if comp_choice != "Todas":
    lineups_competition_id = int(competitions.loc[competitions["name"] == comp_choice, "id"].iloc[0])

lineups_df = queries.season_lineups(engine, rival_team_id, scouting_season_id, lineups_competition_id)

if lineups_df.empty:
    st.info(f"Sin quintetos reconstruidos para {rival_name} con este filtro.")
else:
    total_combos = int(lineups_df["total_combos"].iloc[0])
    st.caption(f"{len(lineups_df)} quintetos más usados de {total_combos} combinaciones")
    st.dataframe(
        lineups_df,
        hide_index=True,
        width="stretch",
        column_order=["jugadores", "minutes", "plus_minus", "stints"],
        column_config={
            "jugadores": st.column_config.TextColumn("Quinteto", width="large"),
            "minutes": st.column_config.NumberColumn("Min. juntos", format="%.1f"),
            "plus_minus": st.column_config.NumberColumn("+/-"),
            "stints": st.column_config.NumberColumn("Tramos"),
        },
    )

st.divider()

# ------------------------------------------------------------ perfil por cuartos --
st.subheader("Rendimiento por cuarto")
quarters_df = queries.team_quarter_profile(engine, rival_team_id, scouting_season_id)

if quarters_df.empty:
    st.info(f"Sin parciales por cuarto registrados para {rival_name} en esa temporada.")
else:
    long_df = quarters_df.melt(
        id_vars=["quarter"],
        value_vars=["avg_points_for", "avg_points_against"],
        var_name="tipo",
        value_name="puntos",
    ).replace({"avg_points_for": "Anotados", "avg_points_against": "Encajados"})

    chart = (
        alt.Chart(long_df)
        .mark_bar()
        .encode(
            x=alt.X("quarter:O", title="Cuarto"),
            y=alt.Y("puntos:Q", title="Puntos por partido"),
            color=alt.Color(
                "tipo:N",
                title=None,
                scale=alt.Scale(domain=["Anotados", "Encajados"], range=[_ACCENT, _MUTED]),
            ),
            xOffset="tipo:N",
            tooltip=[
                alt.Tooltip("quarter:O", title="Cuarto"),
                alt.Tooltip("tipo:N", title="Tipo"),
                alt.Tooltip("puntos:Q", title="Puntos", format=".1f"),
            ],
        )
        .properties(height=240)
    )
    st.altair_chart(chart, width="stretch")

    min_gp = int(quarters_df["gp"].min())
    st.caption(
        f"Medias sobre {min_gp} partidos con parcial registrado. "
        "Un cuarto con diferencia positiva sostenida es donde el rival hace daño."
        if min_gp >= 4
        else f"⚠ Muestra pequeña: solo {min_gp} partido(s) con parcial registrado — léelo con cautela."
    )

    # Faltas medias por cuarto (Fase 2) — el momento de acumulación de faltas:
    # preparación de rotaciones con margen de faltas contra este rival.
    fouls_df = queries.team_foul_quarter_profile(engine, rival_team_id, scouting_season_id)
    if not fouls_df.empty:
        fouls_chart = (
            alt.Chart(fouls_df)
            .mark_bar(color=_MUTED)
            .encode(
                x=alt.X("quarter:O", title="Cuarto"),
                y=alt.Y("avg_fouls_for:Q", title="Faltas por partido"),
                tooltip=[
                    alt.Tooltip("quarter:O", title="Cuarto"),
                    alt.Tooltip("avg_fouls_for:Q", title="Faltas cometidas", format=".1f"),
                    alt.Tooltip("gp:Q", title="Partidos con dato"),
                ],
            )
            .properties(height=160)
        )
        st.caption(f"Faltas cometidas por {rival_name}, por cuarto")
        st.altair_chart(fouls_chart, width="stretch")

st.divider()

# --------------------------------------------------------- tiros y zonas --
st.subheader("Mapa de tiros de la temporada")
shots_df = queries.team_shots_season(engine, rival_team_id, scouting_season_id)

if shots_df.empty:
    st.info(f"Sin tiros con coordenadas registrados para {rival_name} en esa temporada.")
else:
    players = ["Todos"] + sorted(shots_df["player_name"].unique().tolist())
    player_choice = st.selectbox("Jugador", options=players, key="rival_shots_player_filter")
    filtered_df = shots_df if player_choice == "Todos" else shots_df[shots_df["player_name"] == player_choice]

    zones_df = queries.court_zones(engine)
    # El % exacto por zona no se estima a ojo de la nube de puntos — tabla aparte.
    # No se filtra por jugador: `game_zone_stats` está agregado por equipo en
    # origen, no guarda quién tiró.
    #
    # COBERTURA, verificado en vivo (2026-08-24, antes del reteselado): las 6
    # zonas originales dejaban entre el 52% (Euroliga) y el 79% (Supercopa) de
    # los tiros de la BD sin zona (`zone_id = NULL`, fuera de `game_zone_stats`).
    # El reteselado de 2026-08-27 (ver el comentario sobre `court_zones` en
    # `schema.sql`) cubre casi toda la cancha ofensiva real, pero SIGUE siendo
    # una aproximación de rectángulos, no la línea de triple exacta: "Ala
    # izq./der." mezclan tiros de 2 largos y triples de ala en la misma zona
    # (la línea real es un arco). Así que estos porcentajes no son
    # necesariamente "el acierto del equipo desde el triple / la pintura" en
    # zonas de ala — sí lo son en Pintura, Triple esquina, Triple exterior y
    # Mate, que no mezclan nada. Se declara la cobertura siempre, y el aviso se
    # refuerza solo si de verdad queda un hueco apreciable. El mapa de arriba
    # es fiable siempre: usa las coordenadas, no las zonas (y desde esta
    # revisión, su fondo también parte "Ala izq./der." por la línea real de
    # triple — ver `court.py::_wing_split_layers` — aunque esta tabla, atada
    # a `game_zone_stats`, siga sin poder desglosarlas).
    zone_df = queries.team_zone_profile(engine, rival_team_id, scouting_season_id)

    # Uno al lado del otro, no apilados: son dos lecturas del mismo mapa
    # (nube de tiros vs. acierto por zona) y se comparan mejor en paralelo.
    # Cada gráfico conserva su ancho fijo (dominio cuadrado, sin
    # `use_container_width`/`width="stretch"` — ver `court.py`) y ya trae de
    # serie el icono de pantalla completa de Streamlit al pasar el ratón por
    # encima, para verlo grande sin perder el layout de dos columnas.
    col_shots, col_zones = st.columns(2)
    with col_shots:
        st.altair_chart(shot_chart(filtered_df, zones_df))
        st.caption(shot_chart_caption(filtered_df))
    with col_zones:
        st.markdown("**Acierto por zona**")
        st.altair_chart(zone_heatmap(zone_df, zones_df))
        st.caption(zone_heatmap_caption(zone_df))
    zone_breakdown(zone_df, len(shots_df), scope="team")
