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

from analytics import shot_quality, win_thresholds, zone_matchup
from assistant.capabilities import probe
from assistant.llm import LLMError, build_llm_client
from components.ask_assistant import ask_assistant_button
from components.avatar import player_avatar_html, team_crest_html
from components.court import (
    SHOT_CONTEXT_CAPTION,
    shot_chart,
    shot_chart_caption,
    shot_context_filter,
    zone_breakdown,
    zone_heatmap,
    zone_heatmap_caption,
)
from components.glossary import glossary_expander, help_text
from components.header import page_header
from components.lineups import season_lineups_section
from components.prediction import prediction_section
from components.player_dialog import player_detail
from components.rotation_pattern import rotation_pattern_section
from components.rotation_plan import rotation_plan_section
from components.shot_quality import (
    league_reference_expander,
    player_quality_table,
    quality_caveat,
    quality_metrics,
)
from components.win_thresholds import factor_correlations_table, logistic_importance_expander, objectives_panel
from components.zone_matchup import matchup_caveat, split_court_matchup, targets_table
from data import queries, queries_assistant
from data.db import get_read_engine
from reports import scouting_ppt

_ACCENT = "#008300"   # verde Baskonia — puntos anotados por el rival
_MUTED = "#898781"    # ink muted — puntos encajados
_PPT_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

engine = get_read_engine()
own_team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]
today = dt.date.today()

page_header("Próximo rival")

# --------------------------------------------------------------- el rival --
# El próximo partido es el siguiente del calendario, sin acotar por la
# temporada del selector (ver `queries.next_matchup`): esta pantalla se
# quedaba bloqueada precisamente en la temporada con datos en cuanto la
# ingesta cargaba el calendario de la siguiente. La temporada del selector
# sigue mandando en los DATOS de scouting, vía `team_scouting_season` (más
# abajo).
matchup = queries.next_matchup(engine, today)
if matchup is None:
    st.info(
        "No hay ningún partido futuro cargado en el calendario todavía. "
        "Lo pueblan `ingest/acb` e `ingest/euroleague` (`run_upcoming`)."
    )
    st.stop()

rival_team_id = matchup["opponent_team_id"]
rival_name = matchup["opponent"]
condicion = "Local" if matchup["is_home"] else "Visitante"
match_date = dt.date.fromisoformat(str(matchup["match_date"]))
fecha = match_date.strftime("%d %b %Y")

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

# ------------------------------------------------------- dossier de scouting --
# Propuesta 03 (`doc/features/propuestas/03_dossier_scouting_rival.md`): un
# botón que convierte todo el scouting de esta pantalla en un `.pptx`
# proyectable para la reunión del día antes — a la reunión no va la
# aplicación. Mismo patrón que "PPT para Paolo"
# (`app/screens/partidos_anteriores.py`): bytes en `session_state` +
# `download_button`, porque este último provoca *rerun* y el fichero no
# puede construirse en el mismo paso en que se descarga. Clave por rival Y
# temporada de scouting: cambiar de rival (o que la página caiga a otra
# temporada por fallback) no debe ofrecer para descargar el dossier de otro.
dossier_state_key = f"dossier_bytes_{rival_team_id}_{scouting_season_id}"
if st.button("📊 Generar dossier de scouting", key=f"dossier_btn_{rival_team_id}", width="stretch"):
    with st.spinner(f"Generando el dossier de {rival_name}..."):
        try:
            llm_client = build_llm_client()
        except LLMError:
            # Proveedor configurado pero inutilizable (SDK que falta, clave
            # rechazada): el dossier sale igual, con el fallback por reglas de
            # `scouting_ppt` — este botón no depende del chat.
            llm_client = None
        st.session_state[dossier_state_key] = scouting_ppt.generate_scouting_ppt(
            engine,
            rival_team_id=rival_team_id,
            rival_name=rival_name,
            is_home=matchup["is_home"],
            competition=matchup["competition"],
            fecha=fecha,
            own_team_id=own_team_id,
            scouting_season_id=scouting_season_id,
            scouting_season_label=scouting["label"],
            is_fallback_season=scouting["is_fallback"],
            today=today,
            llm_client=llm_client,
            match_date=match_date,
        )

if st.session_state.get(dossier_state_key):
    st.download_button(
        "Descargar dossier",
        data=st.session_state[dossier_state_key],
        file_name=f"scouting_{rival_name}_{fecha}.pptx".replace(" ", "_"),
        mime=_PPT_MIME,
        key=f"dossier_dl_{rival_team_id}",
        width="stretch",
    )

st.divider()

# ------------------------------------------------------------- predicción --
# Propuesta 16 (`doc/features/propuestas/16_prediccion_del_partido.md`):
# margen esperado, probabilidad y qué la mueve, calculado al vuelo (la app no
# escribe `upcoming_matchups.predicted_net_rating`).
prediction_section(
    engine, own_team_id=own_team_id, rival_team_id=rival_team_id, rival_name=rival_name,
    season_id=scouting_season_id, match_date=match_date, is_home=bool(matchup["is_home"]),
    competition=matchup["competition"],
)
st.divider()

# --------------------------------------------------------- objetivos del partido --
# Propuesta 09 (`doc/features/propuestas/09_umbrales_de_victoria.md`): tres o
# cuatro números, no veinte estadísticas — los umbrales que mejor separaron
# victoria de derrota ESTA temporada, ajustados al perfil de ESTE rival. Va
# antes que cualquier otra sección a propósito (§1 y §2a del documento): es
# el mensaje de vestuario que ordena el resto, y si algo de abajo no mueve
# estos números no merece pantalla.
st.subheader("Objetivos del partido")
league_factor_rows = queries.game_factor_rows(engine, scouting_season_id)
if league_factor_rows.empty:
    st.info(f"Sin estadísticas avanzadas suficientes en la temporada de {rival_name} para calcular umbrales.")
else:
    # Por defecto, ACB y Euroliga juntas (más muestra). §5 del documento avisa
    # de que el ritmo y el arbitraje difieren entre las dos
    # (`05_perfil_arbitral.md`), así que se puede acotar a una sola cuando hay
    # partidos de sobra para sostener el barrido (§4: `MIN_SIDE_GAMES` a cada
    # lado) — con menos, un "umbral de competición" sería una anécdota.
    comp_counts = league_factor_rows["competition_id"].value_counts()
    comp_names = queries.list_competitions(engine).set_index("id")["name"]
    comp_options = {"ACB y Euroliga juntas": None}
    for comp_id, count in comp_counts.items():
        if count >= 2 * win_thresholds.MIN_SIDE_GAMES:
            comp_options[str(comp_names.get(comp_id, f"Competición {comp_id}"))] = comp_id

    selected_competition_id = None
    competition_label = None
    if len(comp_options) > 1:
        selected_label = st.radio(
            "Umbrales calculados sobre", list(comp_options.keys()),
            horizontal=True, key="win_thresholds_competition",
        )
        selected_competition_id = comp_options[selected_label]
        competition_label = None if selected_competition_id is None else selected_label

    factor_rows = (
        league_factor_rows if selected_competition_id is None
        else league_factor_rows[league_factor_rows["competition_id"] == selected_competition_id]
    )
    objective_cards = win_thresholds.league_objectives(factor_rows)
    rival_concession = win_thresholds.rival_concession_averages(factor_rows, rival_team_id)
    league_concession = win_thresholds.league_concession_averages(factor_rows)
    objectives_panel(
        objective_cards, rival_avg=rival_concession, league_avg=league_concession,
        rival_name=rival_name, competition_label=competition_label,
    )
    factor_correlations_table(factor_rows)
    # v2 del documento (§4): regresión logística sobre las cuatro batallas,
    # solo para el "¿cuál pesa más de verdad?" del desplegable — el panel de
    # arriba sigue siendo v1 (barrido de umbrales), que es la versión
    # explicable que se enseña sin pedir permiso.
    logistic_importance_expander(win_thresholds.fit_logistic_model(factor_rows))

st.divider()

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

# ------------------------------------------------------------------ fatiga --
# Ficha de fatiga del rival (§2b de la propuesta 04,
# `doc/features/propuestas/04_fatiga_y_calendario.md`): un bloque corto para
# "¿cómo llega?" sin tener que cruzar el calendario a mano. Depende de
# `recent_df` (arriba): sin un último partido jugado no hay descanso que
# calcular. Si la temporada de scouting es un FALLBACK (el rival aún no ha
# jugado en la seleccionada, ver el aviso de arriba), su "último partido" es
# de otra temporada y el descanso hasta ESTE partido no significaría nada
# real — se dice en vez de enseñar un número engañoso.
st.subheader("Fatiga y descanso")
if recent_df.empty:
    st.info(f"Sin partidos recientes de {rival_name}: no se puede calcular su descanso ni su carga.")
elif scouting["is_fallback"]:
    st.info(
        f"{rival_name} no ha jugado todavía en la temporada seleccionada: su descanso y su carga de "
        f"los últimos días no se pueden calcular sobre partidos de {scouting['label']}."
    )
else:
    last_game = recent_df.iloc[0]
    last_game_date = dt.date.fromisoformat(str(last_game["game_date"]))
    days_rest = (match_date - last_game_date).days

    rest_col, w7_col, w14_col = st.columns(3)
    rest_col.metric(f"Descanso hasta el {fecha}", f"{days_rest} días", help=help_text("rest_days"))
    rest_col.caption(
        f"Último partido: {last_game['competition']} en {last_game['condicion'].lower()} "
        f"el {last_game_date.strftime('%d %b')}."
    )
    # Partidos en los últimos 7/14 días ANTES de hoy — lo que ya se sabe con
    # certeza; el propio partido que se está preparando no cuenta como uno más.
    for col, window in ((w7_col, 7), (w14_col, 14)):
        cutoff = today - dt.timedelta(days=window)
        in_window = recent_df[
            recent_df["game_date"].apply(lambda d: dt.date.fromisoformat(str(d))) >= cutoff
        ]
        col.metric(f"Partidos en {window} días", len(in_window))
        if not in_window.empty:
            col.caption(" · ".join(f"{r['competition']} {r['game_date']}" for _, r in in_window.iloc[::-1].iterrows()))

    # Minutos de sus jugadores principales en la ventana de 7 días, y quién
    # está jugando por encima de su media de la temporada (§2b del documento).
    rolling_df = queries.rolling_load(engine, rival_team_id, scouting_season_id, 7)
    if rolling_df.empty:
        st.caption("Sin minutos registrados en esa ventana para desglosar por jugador.")
    else:
        latest_rolling_date = rolling_df["game_date"].max()
        latest = rolling_df[rolling_df["game_date"] == latest_rolling_date].copy()
        averages = queries_assistant.team_roster_production(engine, rival_team_id, scouting_season_id)
        latest = latest.merge(averages[["id", "min_avg"]], left_on="player_id", right_on="id", how="left")
        latest["minutos_recientes_partido"] = latest["rolling_minutes"] / latest["games_in_window"].replace(0, pd.NA)
        latest["por_encima_de_su_media"] = latest["minutos_recientes_partido"] > latest["min_avg"]
        top_load = latest.sort_values("rolling_minutes", ascending=False).head(6)
        st.dataframe(
            top_load,
            hide_index=True,
            width="stretch",
            column_order=[
                "player_name", "rolling_minutes", "games_in_window", "minutos_recientes_partido",
                "min_avg", "por_encima_de_su_media",
            ],
            column_config={
                "player_name": st.column_config.TextColumn("Jugador"),
                "rolling_minutes": st.column_config.NumberColumn(
                    "Min. en 7 días", format="%.0f", help=help_text("rolling_minutes")
                ),
                "games_in_window": st.column_config.NumberColumn("Partidos"),
                "minutos_recientes_partido": st.column_config.NumberColumn("Min/partido reciente", format="%.1f"),
                "min_avg": st.column_config.NumberColumn("Media de temporada", format="%.1f", help=help_text("minutes")),
                "por_encima_de_su_media": st.column_config.CheckboxColumn("¿Por encima de su media?"),
            },
        )
        st.caption(f"Los {min(6, len(latest))} jugadores con más minutos en los últimos 7 días, a fecha de {latest_rolling_date.date()}.")

    # Rendimiento con y sin descanso: el dato que convierte la ficha en
    # decisión ("con dos días de descanso concede X puntos más por 100
    # posesiones", §2b/§4 del documento).
    perf_df = queries.performance_by_rest(engine, rival_team_id, scouting_season_id)
    if perf_df.empty:
        st.caption("Sin estadísticas avanzadas suficientes para cruzar rendimiento con descanso.")
    else:
        short = perf_df[perf_df["rest_bucket"].isin(["≤1", "2"])]
        long = perf_df[perf_df["rest_bucket"].isin(["3-4", "≥5"])]
        short_gp, long_gp = int(short["gp"].sum()), int(long["gp"].sum())
        short_net = (short["net_rating"] * short["gp"]).sum() / short_gp if short_gp else None
        long_net = (long["net_rating"] * long["gp"]).sum() / long_gp if long_gp else None

        net_col_short, net_col_long = st.columns(2)
        net_col_short.metric(
            "Net rating con ≤2 días de descanso",
            f"{short_net:+.1f}" if short_net is not None else "—",
        )
        net_col_short.caption(f"{short_gp} partido(s) con estadísticas avanzadas.")
        net_col_long.metric(
            "Net rating con ≥3 días de descanso",
            f"{long_net:+.1f}" if long_net is not None else "—",
        )
        net_col_long.caption(f"{long_gp} partido(s) con estadísticas avanzadas.")

        chart = (
            alt.Chart(perf_df)
            .mark_bar(color=_ACCENT)
            .encode(
                x=alt.X("rest_bucket:N", title="Días de descanso", sort=perf_df["rest_bucket"].tolist()),
                y=alt.Y("net_rating:Q", title="Net rating"),
                tooltip=[
                    alt.Tooltip("rest_bucket:N", title="Descanso"),
                    alt.Tooltip("net_rating:Q", title="Net rating", format="+.1f"),
                    alt.Tooltip("gp:Q", title="Partidos"),
                ],
            )
            .properties(height=180)
        )
        st.altair_chart(chart, width="stretch")
        min_bucket_gp = int(perf_df["gp"].min())
        st.caption(
            f"Desglose por tramo de descanso ({int(perf_df['gp'].sum())} partidos con estadísticas avanzadas "
            f"en total). {'⚠ Algún tramo tiene solo ' + str(min_bucket_gp) + ' partido(s): léelo con cautela.' if min_bucket_gp < 4 else ''}"
        )
        glossary_expander(["rest_bucket", "net_rating", "rolling_minutes"])

    st.caption(
        "Aproximación desde calendario y minutos de partido, no datos médicos ni de entrenamiento — "
        "no lo trates como predicción de lesión."
    )

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
            # `help=` en todas: esta fila es la más densa en siglas de la app
            # (ocho seguidas, cuatro de ellas avanzadas) y es donde antes había
            # que salir a buscar qué significaba cada una. El texto sale del
            # glosario (`components/glossary.py`).
            cols = st.columns(8)
            cols[0].metric("PJ", int(row.gp), help=help_text("gp"))
            cols[1].metric("Ritmo", f"{row.pace:.1f}" if pd.notna(row.pace) else "—", help=help_text("pace"))
            cols[2].metric("ORtg", f"{row.ortg:.1f}" if pd.notna(row.ortg) else "—", help=help_text("ortg"))
            cols[3].metric("DRtg", f"{row.drtg:.1f}" if pd.notna(row.drtg) else "—", help=help_text("drtg"))
            cols[4].metric(
                "Net", f"{row.net_rating:+.1f}" if pd.notna(row.net_rating) else "—", help=help_text("net_rating")
            )
            cols[5].metric("eFG%", f"{row.efg_pct:.1f}" if pd.notna(row.efg_pct) else "—", help=help_text("efg_pct"))
            cols[6].metric("TS%", f"{row.ts_pct:.1f}" if pd.notna(row.ts_pct) else "—", help=help_text("ts_pct"))
            # `ft_pct` puede ser NaN con `gp` > 0 (partidos sin `ftm`/`fta`
            # cargados, ver el docstring de `team_advanced_profile`) — se
            # distingue de "0%", no se disimula el hueco de cobertura.
            cols[7].metric("FT%", f"{row.ft_pct:.1f}" if pd.notna(row.ft_pct) else "—", help=help_text("ft_pct"))

            # Fase 0 (quick win, 2026-08-27): tasas de equipo ya cargadas que
            # ninguna query seleccionaba — % de canastas asistidas, presión
            # defensiva (robos/tapones por posesión rival), ratio AST/TOV.
            if pd.notna(getattr(row, "ast_pct", None)) or pd.notna(getattr(row, "stl_pct", None)):
                rate_cols = st.columns(5)
                rate_cols[0].metric(
                    "% Asistidas", f"{row.ast_pct:.1f}" if pd.notna(row.ast_pct) else "—", help=help_text("ast_pct")
                )
                rate_cols[1].metric(
                    "% Robo", f"{row.stl_pct:.1f}" if pd.notna(row.stl_pct) else "—", help=help_text("stl_pct")
                )
                rate_cols[2].metric(
                    "% Tapón", f"{row.blk_pct:.1f}" if pd.notna(row.blk_pct) else "—", help=help_text("blk_pct")
                )
                rate_cols[3].metric(
                    "Tasa TL", f"{row.ft_rate:.1f}" if pd.notna(row.ft_rate) else "—", help=help_text("ft_rate")
                )
                rate_cols[4].metric(
                    "AST/TOV",
                    f"{row.ast_to_ratio:.2f}" if pd.notna(row.ast_to_ratio) else "—",
                    help=help_text("ast_to_ratio"),
                )

            # Boxscore ampliado de equipo (Fase 1): propio y CONCEDIDO.
            if pd.notna(getattr(row, "stl_avg", None)):
                # El "(propio·rival)" de la etiqueta dice CÓMO se lee la cifra
                # pero no qué es; el `help` añade lo segundo, con el mismo
                # texto que la columna equivalente del boxscore.
                def _pair_help(key: str) -> str:
                    return (
                        f"{help_text(key)} Por partido: primero la media del rival, después la que "
                        "le hacen a él sus contrarios."
                    )

                box_cols = st.columns(4)
                box_cols[0].metric(
                    "Robos (propio·rival)",
                    f"{row.stl_avg:.1f} · {row.opp_stl_avg:.1f}" if pd.notna(row.opp_stl_avg) else f"{row.stl_avg:.1f}",
                    help=_pair_help("stl"),
                )
                box_cols[1].metric(
                    "Tapones (propio·rival)",
                    f"{row.blk_avg:.1f} · {row.opp_blk_avg:.1f}" if pd.notna(row.opp_blk_avg) else f"{row.blk_avg:.1f}",
                    help=_pair_help("blk"),
                )
                box_cols[2].metric(
                    "Pérdidas (propio·rival)",
                    f"{row.tov_avg:.1f} · {row.opp_tov_avg:.1f}" if pd.notna(row.opp_tov_avg) else f"{row.tov_avg:.1f}",
                    help=_pair_help("tov"),
                )
                box_cols[3].metric(
                    "Faltas (propio·rival)",
                    f"{row.pf_avg:.1f} · {row.opp_pf_avg:.1f}" if pd.notna(row.opp_pf_avg) else f"{row.pf_avg:.1f}",
                    help=_pair_help("pf"),
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

            glossary_expander([
                "gp", "pace", "ortg", "drtg", "net_rating", "efg_pct", "ts_pct", "ft_pct",
                "ast_pct", "stl_pct", "blk_pct", "ft_rate", "ast_to_ratio",
                "stl", "blk", "tov", "pf",
            ])

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
        "Ordenados por puntos por partido. Las fotos salen de baskonia.com para la plantilla "
        "propia y de la API de Euroliga para el resto (`ingest/euroleague/roster.py`): un rival "
        "que solo juega ACB, o un fichaje cuya foto la fuente aún no ha publicado, se queda con "
        "el badge de iniciales."
    )

st.divider()

# ------------------------------------------------------------------ quintetos --
season_lineups_section(
    engine, rival_team_id, scouting_season_id, key="rival_lineups_competition", subject=rival_name
)

st.divider()

# --------------------------------------------------------- patrón de rotación --
# Propuesta 13: de "qué quintetos usa" a "cuándo". Necesita tramos con reloj
# (`lineup_stints`); sin ellos la sección no se pinta.
if probe(engine).lineup_stints:
    rotation_pattern_section(engine, rival_team_id, scouting_season_id, rival_name, key="rival_rotation")
    st.divider()
    # Propuesta 15: sus ventanas débiles × nuestros mejores quintetos. RAPM de
    # la temporada de SCOUTING: los dos equipos tienen que estar en el mismo ajuste.
    rotation_plan_section(
        engine, rival_team_id, own_team_id, scouting_season_id, rival_name,
        is_home=matchup["is_home"], key="rival_rotation_plan",
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

# ---------------------------------------------------- a quién no ponerle la mano --
# Propuesta 06 (`doc/features/propuestas/06_gestion_de_faltas.md`), parte
# (b): ranking del rival por faltas provocadas — entra sola en el dossier de
# prepartido (§7 de la propuesta), por eso vive aquí y no en "Estado del
# equipo" (que es la (a), sobre la plantilla propia).
st.subheader("Faltas: a quién no ponerle la mano")
min_minutes_fouls = st.slider(
    "Mínimo de minutos jugados en la temporada", 0, 1500, 500, 50, key="rival_fouls_min_minutes"
)
leaders_df = queries_assistant.foul_drawing_leaders(engine, rival_team_id, scouting_season_id, float(min_minutes_fouls))

if leaders_df.empty:
    st.info(
        f"Sin boxscore ampliado, sin play-by-play tipado, o nadie de {rival_name} llega a "
        f"{min_minutes_fouls} minutos jugados en esa temporada: baja el mínimo o prueba otra temporada."
    )
else:
    # `early_trouble_rate` llega como fracción (0-1) de la consulta — a 0-100
    # aquí, mismo criterio que cualquier otro porcentaje de la interfaz
    # (`ft_pct`, `efg_pct`...), que se calculan ya en esa escala.
    display_leaders = leaders_df.assign(early_trouble_rate=leaders_df["early_trouble_rate"] * 100.0)
    st.dataframe(
        display_leaders,
        hide_index=True,
        width="stretch",
        column_order=[
            "player_name", "gp", "pf_drawn_per40", "fta_per40", "ft_pct",
            "early_trouble_games", "early_trouble_rate",
        ],
        column_config={
            "player_name": st.column_config.TextColumn("Jugador"),
            "gp": st.column_config.NumberColumn("PJ", help=help_text("gp")),
            "pf_drawn_per40": st.column_config.NumberColumn(
                "Provocadas/40", format="%.1f", help=help_text("pf_drawn_per40")
            ),
            "fta_per40": st.column_config.NumberColumn("TL/40", format="%.1f", help=help_text("fta_per40")),
            "ft_pct": st.column_config.NumberColumn("FT%", format="%.1f", help=help_text("ft_pct")),
            "early_trouble_games": st.column_config.NumberColumn(
                "Cargas tempranas", help=help_text("early_trouble_games")
            ),
            "early_trouble_rate": st.column_config.NumberColumn(
                "% cargas tempranas", format="%.0f", help=help_text("early_trouble_rate")
            ),
        },
    )
    st.caption(
        f"De más a menos faltas provocadas por 40 minutos, entre los jugadores de {rival_name} con al "
        f"menos {min_minutes_fouls} minutos jugados. Provocadas/40 y TL/40 dicen a quién no ponerle la "
        "mano; cargas tempranas dice quién de ellos está a un aviso de sentarse."
    )
    glossary_expander(["pf_drawn_per40", "fta_per40", "ft_pct", "early_trouble_games", "early_trouble_rate"])

st.divider()

# ------------------------------------------------- dónde castigar al rival --
# Propuesta 08 (`doc/features/propuestas/08_donde_castigar_al_rival.md`): no
# "¿desde dónde tira el rival?" (eso lo enseña el mapa de tiros de abajo),
# sino la cruzada "¿desde dónde nos van a dejar tirar, y coincide con lo que
# nosotros metemos?". Va ENCIMA del mapa de tiros: es la lectura accionable
# que ese mapa solo respalda con detalle.
#
# Fuente `game_zone_stats` (vía `queries.team_zone_profile_by_competition`),
# no `shots` vía `players.team_id` — ver el docstring de
# `analytics/zone_matchup.py` (§3 del documento) sobre por qué esa distinción
# importa aquí y no solo en teoría.
st.subheader("Dónde castigar al rival")

# El Baskonia tiene su propio fallback de temporada de scouting, INDEPENDIENTE
# del `scouting_season_id` del rival (mismo criterio que la sección de
# "Calidad de tiro" de `estado_equipo.py`): si el rival ya jugó esta temporada
# pero el Baskonia todavía no (o al revés, arrancando de temporada), cada lado
# se compara contra la referencia de liga de SU PROPIA temporada, que sigue
# siendo una comparación válida aunque las dos temporadas no coincidan.
own_zone_scouting = queries.team_scouting_season(engine, own_team_id, season_id)
own_zone_season_id = own_zone_scouting["season_id"] if own_zone_scouting else None

if scouting_season_id is None or own_zone_season_id is None:
    missing = []
    if scouting_season_id is None:
        missing.append(rival_name)
    if own_zone_season_id is None:
        missing.append("el Baskonia")
    st.info(
        f"Hace falta scouting de zona de los dos equipos para cruzar los dos perfiles, y falta el de "
        f"{' y '.join(missing)}."
    )
else:
    zm_zones_geo = queries.court_zones(engine)

    rival_league_baseline = zone_matchup.league_baseline(
        queries.league_zone_baseline_counts(engine, scouting_season_id)
    )
    # Misma temporada en los dos lados (caso normal, mitad de temporada en
    # curso): reutiliza la línea base ya calculada en vez de pedirla dos veces.
    own_league_baseline = (
        rival_league_baseline
        if own_zone_season_id == scouting_season_id
        else zone_matchup.league_baseline(queries.league_zone_baseline_counts(engine, own_zone_season_id))
    )

    rival_defense_diff = zone_matchup.zone_diff_profile(
        zone_matchup.team_profile(
            queries.team_zone_profile_by_competition(engine, rival_team_id, scouting_season_id, side="defensive")
        ),
        rival_league_baseline,
    )
    rival_offense_diff = zone_matchup.zone_diff_profile(
        zone_matchup.team_profile(
            queries.team_zone_profile_by_competition(engine, rival_team_id, scouting_season_id, side="offensive")
        ),
        rival_league_baseline,
    )
    own_offense_diff = zone_matchup.zone_diff_profile(
        zone_matchup.team_profile(
            queries.team_zone_profile_by_competition(engine, own_team_id, own_zone_season_id, side="offensive")
        ),
        own_league_baseline,
    )
    own_defense_diff = zone_matchup.zone_diff_profile(
        zone_matchup.team_profile(
            queries.team_zone_profile_by_competition(engine, own_team_id, own_zone_season_id, side="defensive")
        ),
        own_league_baseline,
    )

    if rival_defense_diff.empty and own_offense_diff.empty:
        st.info(f"Sin tiros con zona registrados de {rival_name} o del Baskonia en esa temporada.")
    else:
        st.markdown(f"**Nuestro ataque contra la defensa de {rival_name}**")
        split_court_matchup(
            rival_defense_diff, own_offense_diff, zm_zones_geo,
            f"Lo que concede {rival_name}", "Lo que producimos nosotros",
        )
        rival_games_played = queries.team_games_played(engine, rival_team_id, scouting_season_id)
        targets_table(
            zone_matchup.attack_targets(rival_defense_diff, own_offense_diff, rival_games_played),
            f"Ninguna zona con muestra suficiente donde {rival_name} conceda de más Y nosotros "
            "produzcamos de más a la vez — no hay un plan de ataque claro por zona esta temporada.",
        )

        st.markdown(f"**Y al revés: dónde nos va a castigar {rival_name}**")
        split_court_matchup(
            own_defense_diff, rival_offense_diff, zm_zones_geo,
            "Lo que concedemos nosotros", f"Lo que produce {rival_name}",
        )
        own_games_played = queries.team_games_played(engine, own_team_id, own_zone_season_id)
        targets_table(
            zone_matchup.attack_targets(own_defense_diff, rival_offense_diff, own_games_played),
            "Ninguna zona con muestra suficiente donde nosotros concedamos de más Y "
            f"{rival_name} produzca de más a la vez.",
        )

        matchup_caveat()
        glossary_expander(
            ["zone_label", "concede_diff_pp", "produce_diff_pp", "shots_per_game", "value_pts_per_game"]
        )

st.divider()

# --------------------------------------------------------- tiros y zonas --
st.subheader("Mapa de tiros de la temporada")
shots_df = queries.team_shots_season(engine, rival_team_id, scouting_season_id)

if shots_df.empty:
    st.info(f"Sin tiros con coordenadas registrados para {rival_name} en esa temporada.")
else:
    players = ["Todos"] + sorted(shots_df["player_name"].unique().tolist())
    player_choice = st.selectbox("Jugador", options=players, key="rival_shots_player_filter")
    # Contexto del tiro (2026-09-28): solo si esta BD tiene tiros con reloj.
    shot_context = shot_context_filter("rival_shots_context") if probe(engine).shot_clock else None
    context_df = (
        shots_df if shot_context is None
        else queries.team_shots_season(engine, rival_team_id, scouting_season_id, shot_context)
    )
    filtered_df = context_df if player_choice == "Todos" else context_df[context_df["player_name"] == player_choice]

    zones_df = queries.court_zones(engine)
    # Con "Todos", el agregado oficial por equipo (`game_zone_stats`, vía
    # `team_zone_profile`) — el % exacto por zona no se estima a ojo de la
    # nube de puntos. Con un jugador concreto, `player_zone_profile` (sobre
    # `shots.zone_id`, sin agregado oficial por jugador que reutilizar): antes
    # este mapa se quedaba siempre en el global del equipo aunque el selector
    # de arriba filtrase por jugador, contradiciendo el mapa de tiros de al
    # lado, que sí se filtraba.
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
    # triple — ver `court.py::_wing_split_layers` — aunque la tabla de abajo,
    # atada a `game_zone_stats` con "Todos", siga sin poder desglosarlas).
    #
    # Con un contexto elegido, el acierto por zona sale de esos mismos tiros
    # (ver el mismo bloque en `estado_equipo.py`).
    player_id = None
    if player_choice != "Todos":
        player_id = shots_df.loc[shots_df["player_name"] == player_choice, "player_id"].iloc[0]
    if shot_context is not None:
        zone_df = queries.shot_zone_profile_in_context(
            engine, rival_team_id, scouting_season_id, shot_context, player_id
        )
        zone_scope = "team" if player_id is None else "player"
    elif player_id is None:
        zone_df = queries.team_zone_profile(engine, rival_team_id, scouting_season_id)
        zone_scope = "team"
    else:
        zone_df = queries.player_zone_profile(engine, player_id, scouting_season_id)
        zone_scope = "player"

    # Uno al lado del otro, no apilados: son dos lecturas del mismo mapa
    # (nube de tiros vs. acierto por zona) y se comparan mejor en paralelo.
    # Cada gráfico conserva su ancho fijo (dominio cuadrado, sin
    # `use_container_width`/`width="stretch"` — ver `court.py`) y ya trae de
    # serie el icono de pantalla completa de Streamlit al pasar el ratón por
    # encima, para verlo grande sin perder el layout de dos columnas.
    if filtered_df.empty:
        st.info("Sin tiros en ese contexto para esta selección.")
    else:
        col_shots, col_zones = st.columns(2)
        with col_shots:
            st.altair_chart(shot_chart(filtered_df, zones_df))
            st.caption(shot_chart_caption(filtered_df))
        with col_zones:
            st.markdown("**Acierto por zona**")
            st.altair_chart(zone_heatmap(zone_df, zones_df))
            st.caption(zone_heatmap_caption(zone_df))
        zone_breakdown(zone_df, len(filtered_df), scope=zone_scope)
    if shot_context is not None:
        st.caption(SHOT_CONTEXT_CAPTION)

st.divider()

# ------------------------------------------------------- calidad de tiro --
# xPPS del rival, en sus dos lados — propuesta 02
# (`doc/features/propuestas/02_calidad_de_tiro.md`).
#
# Preparando un partido, las dos preguntas son distintas y las dos importan:
#   - Lo que GENERA: si su %TC alto viene de generar buenos tiros o de estar
#     acertando por encima de lo que valen (lo segundo se corrige solo, y
#     defender esperando que siga entrando todo es prepararse mal).
#   - Lo que CONCEDE: el xPPS que le sacan los demás, por zona. Es la lectura
#     que dice dónde se le puede castigar sin que la respuesta dependa de si
#     sus rivales estaban acertados esa noche.
st.subheader("Calidad de tiro (xPPS)")

if scouting_season_id is None:
    st.info(f"Sin partidos de {rival_name} cargados: no hay calidad de tiro que medir.")
else:
    quality_zones_df = queries.court_zones(engine)
    rival_counts = queries.team_shot_counts(engine, rival_team_id, scouting_season_id)
    rival_conceded_counts = queries.team_shot_counts(
        engine, rival_team_id, scouting_season_id, conceded=True
    )
    baseline = shot_quality.league_baseline(queries.league_shot_counts(engine, scouting_season_id))
    rival_valued = shot_quality.with_expected(rival_counts, baseline)
    rival_conceded_valued = shot_quality.with_expected(rival_conceded_counts, baseline)

    if rival_valued.empty and rival_conceded_valued.empty:
        st.info(
            f"Sin tiros de {rival_name} localizados y clasificados por zona en esa temporada: "
            "no hay calidad de tiro que medir."
        )
    else:
        attack_col, defense_col = st.columns(2)
        with attack_col:
            st.markdown(f"**Ataque de {rival_name}**")
            quality_metrics(shot_quality.summarize(rival_valued), subject="su ataque")
        with defense_col:
            st.markdown(f"**Lo que concede {rival_name}**")
            quality_metrics(
                shot_quality.summarize(rival_conceded_valued),
                subject=f"la defensa de {rival_name}",
                conceded=True,
            )

        map_col, table_col = st.columns([1, 1])
        with map_col:
            side = st.radio(
                "Lado",
                options=["Ataque", "Defensa"],
                index=1,  # "Defensa" por defecto: es la mitad que dice dónde castigarle
                horizontal=True,
                key="rival_quality_side",
                label_visibility="collapsed",
            )
            side_valued = rival_valued if side == "Ataque" else rival_conceded_valued
            side_zones = shot_quality.zone_profile(side_valued)
            st.altair_chart(zone_heatmap(side_zones, quality_zones_df, mode="vs_league"))
            st.caption(zone_heatmap_caption(side_zones, mode="vs_league"))
        with table_col:
            st.markdown("**Quién elige bien y quién acierta**")
            # Desde `player_shot_counts`, no desde `rival_valued`: el corte
            # por equipo no trae `player_id` (agrega antes de llegar aquí).
            rival_player_valued = shot_quality.with_expected(
                queries.player_shot_counts(engine, rival_team_id, scouting_season_id), baseline
            )
            player_quality_table(
                shot_quality.summarize_by(rival_player_valued, ["player_id"], extra=["player_name"])
            )

        _, coverage = shot_quality.split_usable(rival_counts)
        quality_caveat(coverage)
        league_reference_expander(baseline)

st.divider()

# ------------------------------------------------------- perfil arbitral --
# Propuesta 05 (`doc/features/propuestas/05_perfil_arbitral.md`): la terna de
# un partido FUTURO no vive en `games` (esa tabla solo guarda partidos ya
# disputados, con marcador) — se suele anunciar 48h antes, fuera de lo que
# este pipeline descarga. Por eso el bloque no la busca solo: el cuerpo
# técnico teclea los nombres en cuanto se conocen y aquí sale la ficha de
# cada uno, con el historial con el Baskonia y con este rival concreto (§2a
# del documento). `capabilities.game_metadata` es el interruptor que ya
# existe para "¿esta base de datos tiene árbitros/asistencia/pabellón?" (§6
# del documento) — se reutiliza para el bloque entero en vez de asumir.
if probe(engine).game_metadata:
    st.subheader("Perfil arbitral")

    referee_names = queries_assistant.all_referee_names(engine)
    if not referee_names:
        st.info("Sin árbitros cargados todavía en esta base de datos.")
    else:
        st.caption(
            "La terna de este partido no se anuncia con antelación suficiente para tenerla cargada de "
            "antemano — en cuanto se conozca, búscala aquí para ver su ficha."
        )
        selected_referees = st.multiselect(
            "Árbitros de la terna",
            options=referee_names,
            max_selections=3,
            key=f"rival_referee_picker_{rival_team_id}",
        )
        own_roster = queries_assistant.team_roster_production(engine, own_team_id, season_id)

        for referee_name in selected_referees:
            st.markdown(f"**{referee_name}**")
            profile = queries_assistant.referee_profile(engine, referee_name, season_id)
            if profile is None:
                st.warning(
                    f"Muestra insuficiente en la temporada seleccionada (menos de "
                    f"{queries_assistant.REFEREE_MIN_GAMES_TO_SHOW} partidos con datos completos): "
                    "sin ficha fiable todavía."
                )
                continue

            metric_cols = st.columns(4)
            metric_cols[0].metric("Partidos", int(profile["gp"]), help=help_text("gp"))
            metric_cols[1].metric(
                "Faltas/partido", f"{profile['pf_residual']:+.1f}", help=help_text("referee_pf_residual")
            )
            metric_cols[2].metric(
                "Sesgo local (TL)", f"{profile['home_bias_fta']:+.1f}", help=help_text("referee_home_bias_fta")
            )
            metric_cols[3].metric(
                "Ritmo", f"{profile['pace_residual']:+.1f}", help=help_text("referee_pace_residual")
            )
            if profile["sample_size"] == "caution":
                st.caption(
                    f"⚠ Solo {int(profile['gp'])} partidos esta temporada: tendencia gruesa, no una "
                    "afirmación fina."
                )
            elif pd.notna(profile.get("pf_residual_pct")):
                st.caption(
                    f"Percentil {profile['pf_residual_pct'] * 100:.0f} en faltas señaladas, entre los "
                    "árbitros con muestra plena de la liga (más alto = pita más de lo esperado)."
                )

            hist_own_col, hist_rival_col = st.columns(2)
            for col, hist_team_id, hist_team_name in (
                (hist_own_col, own_team_id, "el Baskonia"),
                (hist_rival_col, rival_team_id, rival_name),
            ):
                with col:
                    st.markdown(f"Historial con {hist_team_name}")
                    history = queries_assistant.referee_team_history(engine, referee_name, season_id, hist_team_id)
                    if history is None:
                        st.caption(f"Sin partidos de {hist_team_name} con este árbitro esta temporada.")
                    else:
                        st.write(f"Balance: {history['wins']}–{history['losses']}")
                        if history["pf_avg"] is not None:
                            baseline_bit = (
                                f" (media de temporada: {history['season_pf_avg']:.1f})"
                                if history.get("season_pf_avg") is not None else ""
                            )
                            st.caption(f"Faltas señaladas: {history['pf_avg']:.1f}/partido{baseline_bit}")

            if not own_roster.empty:
                own_roster_names = own_roster.set_index("id")["name"].to_dict()
                with st.expander(f"Efecto sobre un jugador del Baskonia con {referee_name}"):
                    picked_player = st.selectbox(
                        "Jugador",
                        options=own_roster["id"].tolist(),
                        format_func=lambda pid: own_roster_names.get(pid, pid),
                        key=f"referee_player_effect_{rival_team_id}_{referee_name}",
                    )
                    effect = queries_assistant.player_referee_effect(engine, picked_player, referee_name, season_id)
                    if effect is None:
                        st.caption("Sin partidos de este jugador con este árbitro en la temporada.")
                    else:
                        st.write(
                            f"{effect['pf_per40']:.1f} faltas/40 con este árbitro frente a "
                            f"{effect['season_pf_per40']:.1f}/40 de su media de temporada "
                            f"({effect['diff']:+.1f})."
                        )
                        st.caption(
                            f"Muestra de {effect['gp']} partido(s): se enseña como indicio, no como hecho "
                            "(§2b del documento)."
                        )

            st.divider()

        glossary_expander(
            ["referee_pf_avg", "referee_pf_residual", "referee_fta_avg", "referee_home_bias_fta", "referee_pace_residual"]
        )
