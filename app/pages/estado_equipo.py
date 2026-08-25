"""Pantalla A — Estado actual del equipo.

Resumen (récord + próximo rival), plantilla con medias por jugador y carga
de minutos de los últimos partidos. Contrato de datos completo en
`local/features/001-interfaz-baskonia/01_design.md` §4.
"""
import datetime as dt

import altair as alt
import streamlit as st

from components.avatar import team_crest_html
from components.header import page_header
from data import queries
from data.db import get_read_engine

engine = get_read_engine()
team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]
today = dt.date.today()

page_header("Estado del equipo")

# ---------------------------------------------------------------- resumen --
record_df = queries.team_record(engine, team_id, season_id, today)

cols = st.columns(len(record_df) + 1 if not record_df.empty else 1)
for col, row in zip(cols, record_df.itertuples()):
    col.metric(f"Récord {row.competition}", f"{row.wins}–{row.losses}")

with cols[-1]:
    # Calendario real (ver queries.next_matchup) — solo tiene sentido mirarlo
    # con la temporada más reciente seleccionada (una pasada ya no tiene
    # "próximo" partido).
    if not st.session_state.get("is_current_season", True):
        st.metric("Próximo rival", "—")
        st.caption("No aplica a temporadas pasadas.")
    else:
        next_df = queries.next_matchup(engine, season_id, today)
        if next_df is None:
            st.metric("Próximo rival", "—")
        else:
            condicion = "Local" if next_df["is_home"] else "Visitante"
            fecha = dt.date.fromisoformat(str(next_df["match_date"])).strftime("%d %b")
            crest_col, info_col = st.columns([1, 5])
            with crest_col:
                st.markdown(
                    team_crest_html(next_df["opponent"], next_df.get("opponent_logo_url"), size=40),
                    unsafe_allow_html=True,
                )
            with info_col:
                st.metric("Próximo rival", next_df["opponent"])
            st.caption(f"{fecha} · {next_df['competition']} · {condicion}")
            if next_df.get("key_player_note"):
                st.caption(next_df["key_player_note"])

st.caption("Plantilla completa y estadísticas por jugador → pestaña **Plantilla**.")
st.caption("Scouting completo del rival (forma, avanzadas, cara a cara, tiros) → pestaña **Próximo rival**.")

st.divider()

# --------------------------------------------------------------- calendario --
# Mismo criterio que "Próximo rival" arriba: el calendario futuro solo tiene
# sentido con la temporada más reciente seleccionada.
if st.session_state.get("is_current_season", True):
    st.subheader("Calendario")
    calendar_df = queries.upcoming_matchups_list(engine, season_id, today)
    if calendar_df.empty:
        st.info("Todavía no hay calendario futuro cargado para esta temporada.")
    else:
        st.dataframe(
            calendar_df,
            hide_index=True,
            # `width="stretch"` (el viejo `use_container_width=True`) reparte el
            # espacio sobrante EN PARTES IGUALES entre todas las columnas — con
            # solo 5 columnas eso inflaba la del escudo (`width="small"` = 75px)
            # hasta ~170px de espacio vacío alrededor de un icono diminuto.
            # `"content"` ajusta la tabla a lo que ocupan sus columnas (sin pasar
            # del contenedor), así que cada columna se queda con su ancho real.
            width="content",
            height=280,
            column_order=["rival_logo_url", "rival", "match_date", "competition", "condicion"],
            column_config={
                # Escudo pegado al nombre (columnas adyacentes) — Streamlit no
                # permite combinar imagen+texto en una misma celda de `st.dataframe`.
                "rival_logo_url": st.column_config.ImageColumn(" ", width=40),
                "rival": st.column_config.TextColumn("Rival", width=180),
                "match_date": st.column_config.TextColumn("Fecha", width=100),
                "competition": st.column_config.TextColumn("Comp.", width=90),
                "condicion": st.column_config.TextColumn("Cond.", width=90),
            },
        )
        st.caption(f"{len(calendar_df)} partidos programados — Liga Endesa, Copa del Rey, Supercopa y Euroliga.")
    st.divider()

# ---------------------------------------------------------- carga de minutos --
st.subheader("Carga de minutos")
n_games = st.slider("Últimos partidos a mostrar", min_value=4, max_value=12, value=6)
minutes_df = queries.minutes_load(engine, team_id, season_id, n_games)

# Un jugador sin un solo minuto en toda la ventana (fichaje muy reciente,
# lesión de larga duración) no aporta nada a una lectura de carga/rotación
# — se descarta de la tabla en vez de dejar una fila vacía de un extremo a
# otro (a diferencia de la plantilla, donde SÍ se muestran adrede).
minutes_df = minutes_df.groupby("player_name").filter(lambda g: g["minutes"].notna().any())

if minutes_df.empty:
    st.info("Todavía no hay minutos registrados para calcular la carga reciente.")
else:
    # Orden explícito por carga total (suma de minutos en la ventana), de
    # más a menos, para que "quién se usa más" se lea de un vistazo de
    # arriba abajo. Se calcula en pandas y se pasa como lista al `sort` de
    # Altair en vez de `EncodingSortField(op="sum")`: con dos capas (rect +
    # text) sobre datos filtrados de forma distinta, el sort agregado no se
    # aplicaba de forma fiable — una lista explícita no depende de eso.
    order = (
        minutes_df.groupby("player_name")["minutes"].sum().sort_values(ascending=False).index.tolist()
    )

    heat = (
        alt.Chart(minutes_df)
        .mark_rect()
        .encode(
            x=alt.X("game_date:O", title=None),
            y=alt.Y("player_name:N", title=None, sort=order),
            color=alt.Color(
                "minutes:Q",
                title="Minutos",
                scale=alt.Scale(range=["#cde2fb", "#104281"]),
            ),
            tooltip=[
                alt.Tooltip("player_name:N", title="Jugador"),
                alt.Tooltip("game_date:O", title="Fecha"),
                alt.Tooltip("minutes:Q", title="Minutos", format=".1f"),
            ],
        )
    )
    text = (
        alt.Chart(minutes_df.dropna(subset=["minutes"]))
        .mark_text(fontSize=10, fontWeight="bold")
        .encode(
            x=alt.X("game_date:O"),
            y=alt.Y("player_name:N", sort=order),
            text=alt.Text("minutes:Q", format=".0f"),
            color=alt.condition(alt.datum.minutes > 20, alt.value("white"), alt.value("#0b0b0b")),
        )
    )
    st.altair_chart((heat + text).properties(height=28 * minutes_df["player_name"].nunique() + 40), use_container_width=True)
    st.caption("Celda en blanco = el jugador no disputó ese partido (rotación, baja o convocatoria).")

st.divider()

# ----------------------------------------------------------------- quintetos --
st.subheader("Quintetos más utilizados")
competitions = queries.list_competitions(engine)
comp_choice = st.selectbox("Competición", options=["Todas"] + competitions["name"].tolist(), key="lineups_competition")
lineups_competition_id = None
if comp_choice != "Todas":
    lineups_competition_id = int(competitions.loc[competitions["name"] == comp_choice, "id"].iloc[0])

lineups_df = queries.season_lineups(engine, team_id, season_id, lineups_competition_id)

if lineups_df.empty:
    if comp_choice == "Todas":
        st.info("Todavía no hay quintetos reconstruidos para esta temporada.")
    else:
        st.info(f"Todavía no hay quintetos reconstruidos en {comp_choice} para esta temporada.")
else:
    total_combos = int(lineups_df["total_combos"].iloc[0])
    st.caption(f"{len(lineups_df)} quintetos más usados de {total_combos} combinaciones")
    st.dataframe(
        lineups_df,
        hide_index=True,
        use_container_width=True,
        column_order=["jugadores", "minutes", "plus_minus", "stints"],
        column_config={
            "jugadores": st.column_config.TextColumn("Quinteto", width="large"),
            "minutes": st.column_config.NumberColumn("Min. juntos", format="%.1f"),
            "plus_minus": st.column_config.NumberColumn("+/-"),
            "stints": st.column_config.NumberColumn("Tramos"),
        },
    )
    st.caption(
        "Min. juntos y +/- suman todos los tramos en los que ha jugado ese quinteto exacto "
        "esta temporada, aunque vengan de partidos distintos. Tramos = en cuántos tramos "
        "reconstruidos ha aparecido (sustituciones incluidas)."
    )
