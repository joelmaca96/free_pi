"""Pantalla A — Estado actual del equipo.

Resumen (récord + próximo rival), plantilla con medias por jugador y carga
de minutos de los últimos partidos. Contrato de datos completo en
`local/features/001-interfaz-baskonia/01_design.md` §4.
"""
import datetime as dt

import altair as alt
import streamlit as st

from analytics import shot_quality
from analytics import signals as signals_engine
from components.ask_assistant import ask_assistant_button
from components.avatar import team_crest_html
from components.court import shot_chart, shot_chart_caption, zone_breakdown, zone_heatmap, zone_heatmap_caption
from components.glossary import glossary_expander, help_text
from components.header import page_header
from components.shot_quality import (
    league_reference_expander,
    player_quality_table,
    quality_caveat,
    quality_metrics,
)
from data import queries, queries_assistant
from data.db import get_read_engine

engine = get_read_engine()
team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]
today = dt.date.today()

page_header("Estado del equipo")

# ------------------------------------------------------- señales semanales --
# Lo primero que se ve al entrar (propuesta 10, `10_senales_semanales.md`):
# qué ha cambiado DE VERDAD en los últimos partidos, no cualquier cambio. El
# valor entero de esto está en decir POCO y acertar (§1 del documento), así
# que `select_top_signals` ya aplica las dos barreras obligatorias
# (Benjamini-Hochberg + tamaño de efecto mínimo en unidades de baloncesto) —
# aquí solo se pintan las tarjetas, la decisión de qué es una señal ya está
# tomada en `analytics/signals.py`.
st.subheader("Señales de la semana")


@st.cache_data(ttl=3600, show_spinner=False)
def _weekly_signals(_engine, team_id: str, season_id: int, last_n: int):
    """Detección + filtro, cacheados por (equipo, temporada, K) — nunca el LLM (ver más abajo).

    Solo `_engine` lleva guion bajo: es el único argumento que NO debe entrar
    en la clave de caché de Streamlit (convención de `queries.py`, motor sin
    hashear). `team_id`/`season_id` SÍ tienen que hashear — de lo contrario,
    cambiar de temporada en el selector de la barra lateral seguiría
    devolviendo el resultado cacheado de la primera temporada vista.
    """
    team_name = queries.team_name(_engine, team_id) or team_id
    player_log = queries.team_player_game_log(_engine, team_id, season_id)
    team_log = queries.team_game_advanced_log(_engine, team_id, season_id)
    zone_log = queries.team_game_zone_counts(_engine, team_id, season_id)
    pair_window = queries_assistant.pair_minutes_by_window(_engine, team_id, season_id, last_n)

    rolling = queries.rolling_load(_engine, team_id, season_id, 7)
    rolling_latest = rolling[rolling["game_date"] == rolling["game_date"].max()] if not rolling.empty else rolling

    team_signals = signals_engine.detect_team_signals(
        team_log, team_id=team_id, team_name=team_name, last_n=last_n
    ) + signals_engine.detect_team_zone_signals(zone_log, team_id=team_id, team_name=team_name, last_n=last_n)
    candidates = signals_engine.all_candidates(
        player=signals_engine.detect_player_signals(player_log, last_n=last_n),
        team=team_signals,
        rotation=signals_engine.detect_rotation_signals(pair_window, last_n=last_n),
        load=signals_engine.detect_load_signals(rolling_latest),
    )
    return signals_engine.select_top_signals(candidates, max_signals=5)


week_signals = _weekly_signals(engine, team_id, season_id, 5)

# La redacción con LLM (§6: "reglas como suelo, LLM opcional por encima") NO
# se dispara sola en cada rerun de Streamlit — sería una llamada de pago por
# cada clic en cualquier otro control de la página. Se deja tras un
# interruptor explícito, apagado por defecto; sin tocarlo, las tarjetas se
# quedan con el titular de reglas, que ya es correcto y completo por sí solo.
if week_signals and st.toggle(
    "Redactar con IA (opcional)", value=False, key="signals_llm_polish",
    help="Pule la prosa de los titulares con el modelo configurado. Los números y la selección de "
    "señales no cambian: eso ya lo decide el código, siempre.",
):
    try:  # pragma: no cover - ver nota en tools/context.py sobre las dos formas de import
        from app.assistant.llm import build_llm_client
    except ImportError:  # pragma: no cover
        from assistant.llm import build_llm_client
    llm_client = build_llm_client()
    if llm_client is None:
        st.caption("Sin proveedor de modelo configurado: se muestran los titulares por reglas.")
    else:
        team_name_for_llm = queries.team_name(engine, team_id) or team_id
        week_signals = signals_engine.polish_headlines(llm_client, week_signals, team_name=team_name_for_llm)

if not week_signals:
    st.info(
        "Sin cambios significativos esta semana. Con cinco partidos como ventana, eso es lo esperable "
        "la mayoría de semanas — es la respuesta correcta, no un hueco de datos (§1 y §5 de la propuesta 10)."
    )
else:
    signal_cols = st.columns(len(week_signals))
    for col, signal in zip(signal_cols, week_signals):
        with col:
            with st.container(border=True, height="stretch"):
                st.markdown(f"**{signal.headline}**")
                st.caption(signal.confidence)
                if signal.extra_note:
                    st.caption(f"⚠ {signal.extra_note}")
                ask_assistant_button(
                    signal.ask_question,
                    key=f"signal_ask_{signal.family}_{signal.subject_id}_{signal.metric}",
                    label="Preguntar al asistente",
                )
    st.caption(
        "Ordenadas por relevancia práctica (efecto × peso), no por lo estadísticamente llamativo — "
        "un cambio grande en un suplente puede pesar menos que uno mediano en un titular."
    )

st.divider()

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
            # `width="stretch"` reparte el
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
    st.altair_chart((heat + text).properties(height=28 * minutes_df["player_name"].nunique() + 40), width="stretch")
    st.caption("Celda en blanco = el jugador no disputó ese partido (rotación, baja o convocatoria).")

    # ------------------------------------------------ descanso entre partidos --
    # Mismo recorte de fechas que el mapa de arriba (los `n_games` partidos
    # del slider), para que las dos lecturas —cuánto se jugó y con cuánto
    # descanso se llegó— se miren sobre el mismo tramo de calendario.
    # `rest_days` cuenta CUALQUIER competición (§4 de la propuesta 04): el
    # descanso antes de un partido de Euroliga sí cuenta el de ACB anterior.
    st.markdown("**Descanso entre partidos**")
    shown_dates = set(minutes_df["game_date"])
    rest_df = queries.rest_days(engine, team_id, season_id)
    rest_df = rest_df[rest_df["game_date"].isin(shown_dates)]
    if not rest_df.empty:
        st.dataframe(
            rest_df,
            hide_index=True,
            width="content",
            column_order=["game_date", "competition", "condicion", "rival", "rest_days"],
            column_config={
                "game_date": st.column_config.TextColumn("Fecha"),
                "competition": st.column_config.TextColumn("Comp."),
                "condicion": st.column_config.TextColumn("Cond."),
                "rival": st.column_config.TextColumn("Rival"),
                "rest_days": st.column_config.NumberColumn("Descanso (días)", help=help_text("rest_days")),
            },
        )
        st.caption("Vacío en el primer partido de la base de datos: no hay uno anterior del que restar.")

    # ------------------------------------------------------- carga acumulada --
    # Ventana móvil de calendario (§2a de la propuesta 04), no de partidos:
    # cinco partidos en nueve días y cinco en tres semanas no son la misma
    # carga, y `minutes_load` de arriba (últimos N partidos) no distingue
    # entre las dos.
    st.markdown("**Carga acumulada (ventana móvil)**")
    window_days = st.radio("Ventana", options=[7, 14], horizontal=True, key="load_window_days")
    alert_threshold = st.number_input(
        f"Aviso: minutos en {window_days} días por encima de",
        min_value=0,
        value=140 if window_days == 7 else 240,
        step=10,
        key="load_alert_threshold",
    )

    rolling_df = queries.rolling_load(engine, team_id, season_id, window_days)
    # Mismos jugadores que el mapa de arriba (con minutos reales en la
    # ventana de partidos mostrada) — un fichaje sin debutar no aporta nada
    # a una lectura de carga.
    rolling_df = rolling_df[rolling_df["player_name"].isin(minutes_df["player_name"].unique())]

    if rolling_df.empty:
        st.info("Sin datos suficientes para calcular la carga acumulada.")
    else:
        latest_date = rolling_df["game_date"].max()
        latest = rolling_df[rolling_df["game_date"] == latest_date].sort_values("rolling_minutes", ascending=False)

        games_in_window = int(latest["games_in_window"].iloc[0])
        if games_in_window >= 3:
            st.warning(
                f"⚠ {games_in_window}º partido en {window_days} días a fecha de {latest_date.date()}: "
                "semana cargada, mira quién repite minutos altos en los anteriores."
            )

        bar = (
            alt.Chart(latest)
            .mark_bar()
            .encode(
                x=alt.X("rolling_minutes:Q", title=f"Minutos en {window_days} días"),
                y=alt.Y("player_name:N", title=None, sort="-x"),
                color=alt.condition(
                    alt.datum.rolling_minutes > alert_threshold, alt.value("#c0392b"), alt.value("#104281")
                ),
                tooltip=[
                    alt.Tooltip("player_name:N", title="Jugador"),
                    alt.Tooltip("rolling_minutes:Q", title="Minutos", format=".0f"),
                    alt.Tooltip("games_in_window:Q", title="Partidos en la ventana"),
                    alt.Tooltip("minutes_per_day:Q", title="Min/día", format=".1f"),
                ],
            )
            .properties(height=24 * len(latest) + 20)
        )
        st.altair_chart(bar, width="stretch")
        st.caption(
            f"Carga de cada jugador en los {window_days} días hasta {latest_date.date()}. En rojo, por "
            f"encima del aviso configurado ({alert_threshold} min)."
        )
        glossary_expander(["rest_days", "rolling_minutes", "minutes_per_day"])

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
        width="stretch",
        column_order=["jugadores", "minutes", "plus_minus", "stints"],
        column_config={
            "jugadores": st.column_config.TextColumn("Quinteto", width="large"),
            "minutes": st.column_config.NumberColumn(
                "Min. juntos", format="%.1f", help=help_text("lineup_minutes")
            ),
            "plus_minus": st.column_config.NumberColumn("+/-", help=help_text("lineup_plus_minus")),
            "stints": st.column_config.NumberColumn("Tramos", help=help_text("stints")),
        },
    )
    st.caption(
        "Min. juntos y +/- suman todos los tramos en los que ha jugado ese quinteto exacto "
        "esta temporada, aunque vengan de partidos distintos. Tramos = en cuántos tramos "
        "reconstruidos ha aparecido (sustituciones incluidas)."
    )
    glossary_expander(["lineup_minutes", "lineup_plus_minus", "stints"])

st.divider()

# ------------------------------------------------------ gestión de faltas --
# Propuesta 06 (`doc/features/propuestas/06_gestion_de_faltas.md`), parte
# (a): perfil de faltas de la plantilla. Después de "Quintetos" y antes de
# "Mapa de tiros" — es lectura de rotación, como lo de arriba, no de tiro.
st.subheader("Gestión de faltas")

c_n1, c_min1, c_n2, c_min2 = st.columns(4)
early_n1 = c_n1.number_input("Faltas (regla 1)", min_value=1, max_value=5, value=2, key="foul_early_n1")
early_min1 = c_min1.number_input("Antes del minuto", min_value=1, max_value=40, value=10, key="foul_early_min1")
early_n2 = c_n2.number_input("Faltas (regla 2)", min_value=1, max_value=5, value=3, key="foul_early_n2")
early_min2 = c_min2.number_input("Antes del minuto", min_value=1, max_value=40, value=20, key="foul_early_min2")
st.caption(
    "\"Carga temprana\" = cumplir cualquiera de las dos reglas (§4 de la propuesta 06). Los valores "
    "de fábrica (2 antes del 10, 3 antes del 20) son un punto de partida, no una definición fija."
)

foul_df = queries_assistant.foul_profile(
    engine, team_id, season_id, int(early_n1), float(early_min1), int(early_n2), float(early_min2)
)

if foul_df.empty:
    st.info(
        "Sin boxscore ampliado o sin play-by-play tipado para el Baskonia en esta temporada: no hay "
        "faltas que perfilar todavía."
    )
else:
    baseline_margin = foul_df["team_margin_per_min_season"].dropna()
    if not baseline_margin.empty:
        st.metric(
            "Margen habitual del equipo",
            f"{baseline_margin.iloc[0]:+.2f} pts/min",
            help=help_text("team_margin_per_min_season"),
        )

    display_foul = foul_df.sort_values(["early_trouble_games", "pf_per40"], ascending=False)
    st.dataframe(
        display_foul,
        hide_index=True,
        width="stretch",
        column_order=[
            "player_name", "gp", "pf_per40", "min_2nd_foul_avg", "min_3rd_foul_avg",
            "early_trouble_games", "minutes_lost_avg", "bench_gap_avg_min", "bench_margin_per_min",
        ],
        column_config={
            "player_name": st.column_config.TextColumn("Jugador"),
            "gp": st.column_config.NumberColumn("PJ", help=help_text("gp")),
            "pf_per40": st.column_config.NumberColumn("Faltas/40", format="%.1f", help=help_text("pf_per40")),
            "min_2nd_foul_avg": st.column_config.NumberColumn(
                "Min. 2.ª falta", format="%.1f", help=help_text("min_2nd_foul_avg")
            ),
            "min_3rd_foul_avg": st.column_config.NumberColumn(
                "Min. 3.ª falta", format="%.1f", help=help_text("min_3rd_foul_avg")
            ),
            "early_trouble_games": st.column_config.NumberColumn(
                "Cargas tempranas", help=help_text("early_trouble_games")
            ),
            "minutes_lost_avg": st.column_config.NumberColumn(
                "Min. perdidos (aprox.)", format="%.1f", help=help_text("minutes_lost_avg")
            ),
            "bench_gap_avg_min": st.column_config.NumberColumn(
                "Hueco real (min)", format="%.1f", help=help_text("bench_gap_avg_min")
            ),
            "bench_margin_per_min": st.column_config.NumberColumn(
                "Margen en el hueco", format="%+.2f", help=help_text("bench_margin_per_min")
            ),
        },
    )
    st.caption(
        "Ordenado por cargas tempranas y, a igualdad, por faltas/40. Min./Hueco/Margen en blanco = ese "
        "jugador no tuvo ningún partido con carga temprana con los umbrales de arriba."
    )
    st.warning(
        "⚠ El margen durante el hueco es DESCRIPTIVO, no una recomendación: el rival, el momento y el "
        "marcador de esos minutos concretos no son comparables sin más con un minuto cualquiera de la "
        "temporada. No leer como \"sentarlo cuesta X puntos\"."
    )

    with st.expander("Reparto de faltas por cuarto"):
        st.dataframe(
            foul_df.sort_values("pf_q1_share", ascending=False),
            hide_index=True,
            width="stretch",
            column_order=["player_name", "pf_q1_share", "pf_q2_share", "pf_q3_share", "pf_q4_share", "pf_ot_share"],
            column_config={
                "player_name": st.column_config.TextColumn("Jugador"),
                "pf_q1_share": st.column_config.NumberColumn("Q1 %", format="%.0f", help=help_text("pf_quarter_share")),
                "pf_q2_share": st.column_config.NumberColumn("Q2 %", format="%.0f", help=help_text("pf_quarter_share")),
                "pf_q3_share": st.column_config.NumberColumn("Q3 %", format="%.0f", help=help_text("pf_quarter_share")),
                "pf_q4_share": st.column_config.NumberColumn("Q4 %", format="%.0f", help=help_text("pf_quarter_share")),
                "pf_ot_share": st.column_config.NumberColumn("OT %", format="%.0f", help=help_text("pf_quarter_share")),
            },
        )
        st.caption("Suman 100% por fila. Ordenado por quién más acumula en el primer cuarto.")

    st.caption(
        "No se distingue el tipo de falta (en tiro, antideportiva, técnica): la fuente no lo guarda con "
        "fiabilidad, así que todas cuentan igual (§5 de la propuesta 06)."
    )
    glossary_expander([
        "gp", "pf_per40", "min_2nd_foul_avg", "min_3rd_foul_avg", "early_trouble_games",
        "minutes_lost_avg", "bench_gap_avg_min", "bench_margin_per_min", "team_margin_per_min_season",
        "pf_quarter_share",
    ])

st.divider()

# --------------------------------------------------------- tiros y zonas --
# Al final de la página a propósito — es la sección más pesada de renderizar
# (mapa + tabla de zonas) y la que menos hace falta mirar primero al entrar
# a "Estado del equipo": récord/calendario/carga/quintetos son el vistazo
# rápido, esto es para quien quiere profundizar.
#
# Mismo patrón que el mapa de tiros de "Próximo rival" (`queries.
# team_shots_season`/`team_zone_profile` + `components/court.py`), aquí sobre
# el propio Baskonia en vez del rival — ver `zone_breakdown` para por qué el
# aviso de cobertura y la advertencia de geometría de zonas viven en un solo
# sitio compartido entre las dos pantallas.
#
# Con fallback a la última temporada con tiros registrados si la
# seleccionada todavía no tiene ninguno — mismo caso real que resuelve
# `queries.team_scouting_season` para el resto del scouting del rival
# (inicio de temporada, calendario cargado pero aún sin partidos jugados):
# antes esta sección miraba `season_id` a pelo y se quedaba en blanco justo
# en ese momento, en vez de caer a la temporada anterior avisando.
st.subheader("Mapa de tiros de la temporada")
shots_scouting = queries.team_scouting_season(engine, team_id, season_id)
shots_season_id = shots_scouting["season_id"] if shots_scouting else season_id
shots_df = queries.team_shots_season(engine, team_id, shots_season_id)

if shots_df.empty:
    st.info("Sin tiros con coordenadas registrados para el Baskonia, ni en esta temporada ni en anteriores.")
else:
    if shots_scouting and shots_scouting["is_fallback"]:
        st.info(
            f"⚠ El Baskonia todavía no tiene partidos en la temporada seleccionada. "
            f"**Se muestran los tiros de {shots_scouting['label']}**, su última temporada con datos."
        )
    players = ["Todos"] + sorted(shots_df["player_name"].unique().tolist())
    player_choice = st.selectbox("Jugador", options=players, key="own_shots_player_filter")
    filtered_df = shots_df if player_choice == "Todos" else shots_df[shots_df["player_name"] == player_choice]

    zones_df = queries.court_zones(engine)
    # La tabla/mapa de zonas son del EQUIPO completo, no del filtro de
    # jugador de arriba (`game_zone_stats` es un agregado por equipo, sin
    # desglose por jugador — ver `queries.team_zone_profile`); el propio
    # aviso de `zone_breakdown` lo aclara.
    zone_df = queries.team_zone_profile(engine, team_id, shots_season_id)

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

st.divider()

# ------------------------------------------------------- calidad de tiro --
# xPPS: separar la DECISIÓN (qué tiro se genera) del ACIERTO (si entra), en
# ataque y en defensa — propuesta 02
# (`doc/features/propuestas/02_calidad_de_tiro.md`). El cálculo entero está en
# `app/analytics/shot_quality.py`; aquí solo se pide y se pinta.
#
# Sección aparte de "Mapa de tiros de la temporada" y no un modo suyo, aunque
# las dos pinten la misma cancha: los porcentajes de arriba salen del agregado
# oficial `game_zone_stats` y estos de `shots` tiro a tiro contra la línea base
# de la liga. Son dos fuentes con dos coberturas distintas, y mezclarlas en un
# mismo bloque con un interruptor invitaría a comparar dos números que no son
# comparables.
st.subheader("Calidad de tiro (xPPS)")

# `court_zones` se vuelve a pedir aquí (consulta cacheada, coste cero) en vez
# de reutilizar el `zones_df` de la sección de arriba: aquel se define dentro
# del `else` de "¿hay tiros?", así que un equipo sin tiros con coordenadas
# dejaría esta sección sin geometría.
quality_zones_df = queries.court_zones(engine)
quality_counts = queries.team_shot_counts(engine, team_id, shots_season_id)
conceded_counts = queries.team_shot_counts(engine, team_id, shots_season_id, conceded=True)
baseline = shot_quality.league_baseline(queries.league_shot_counts(engine, shots_season_id))
own_valued = shot_quality.with_expected(quality_counts, baseline)
conceded_valued = shot_quality.with_expected(conceded_counts, baseline)

if own_valued.empty and conceded_valued.empty:
    st.info("Sin tiros localizados y clasificados por zona en esa temporada: no hay calidad de tiro que medir.")
else:
    attack_col, defense_col = st.columns(2)
    with attack_col:
        st.markdown("**Ataque · lo que generamos**")
        quality_metrics(shot_quality.summarize(own_valued), subject="el ataque")
    with defense_col:
        st.markdown("**Defensa · lo que concedemos**")
        # Sin `reference_xpps`: la referencia natural de la defensa es la
        # media de la liga, y eso ya está dentro del propio xPPS concedido.
        quality_metrics(shot_quality.summarize(conceded_valued), subject="la defensa", conceded=True)

    # El mapa "vs. liga": no dónde acertamos más, sino dónde acertamos más que
    # los demás. Es la lectura que distingue una zona en la que somos buenos de
    # una en la que tira bien todo el mundo.
    map_col, table_col = st.columns([1, 1])
    with map_col:
        side = st.radio(
            "Lado",
            options=["Ataque", "Defensa"],
            horizontal=True,
            key="own_quality_side",
            label_visibility="collapsed",
        )
        side_valued = own_valued if side == "Ataque" else conceded_valued
        side_zones = shot_quality.zone_profile(side_valued)
        st.altair_chart(zone_heatmap(side_zones, quality_zones_df, mode="vs_league"))
        st.caption(zone_heatmap_caption(side_zones, mode="vs_league"))
    with table_col:
        st.markdown("**Quién elige bien y quién acierta**")
        # Desde `player_shot_counts`, no desde `own_valued`: el corte por
        # equipo no trae `player_id` (agrega antes de llegar aquí).
        player_valued = shot_quality.with_expected(
            queries.player_shot_counts(engine, team_id, shots_season_id), baseline
        )
        player_quality_table(
            shot_quality.summarize_by(player_valued, ["player_id"], extra=["player_name"])
        )

    _, coverage = shot_quality.split_usable(quality_counts)
    quality_caveat(coverage)
    glossary_expander(["xpps", "pps", "diff_shrunk", "shots", "fg_pct", "zone_label"])
    league_reference_expander(baseline)
