"""Modal de detalle de un jugador, compartido entre páginas.

Extraído de `app/screens/plantilla.py` (donde nació como función local) para
poder abrirlo también desde "Próximo rival" sobre jugadores del equipo
contrario — el contenido es idéntico, lo único que cambia es de qué galería
se llega (ver `local/features/004-proximo-rival/01_design.md` §3.2).

Lee `engine`/`season_id` de donde ya los lee cualquier página
(`get_read_engine()` y `st.session_state["season_id"]`) en vez de recibirlos
como parámetros: así el componente no queda atado al módulo que lo definió y
las dos páginas lo invocan igual, `player_detail(player_id)`.

Nada aquí asume que el jugador sea del Baskonia. Las secciones que dependen
de datos que hoy solo puebla `ingest/baskonia_web` para la plantilla propia
(foto real, altura, nacionalidad, fecha de nacimiento) ya degradaban a "—" /
badge de iniciales antes de esta extracción — que es justo lo que hace falta
para un rival, cuyas filas de `players` las crea `ingest/acb`/
`ingest/euroleague` desde el boxscore y traen esos campos en `NULL` (ver
`doc/features/ingestor/01_estado.md` §2.1).
"""
import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from analytics import similarity as similarity_engine
from components import similarity as similarity_component
from components.ask_assistant import ask_assistant_button
from components.avatar import player_avatar_html
from components.court import shot_chart, shot_chart_caption, zone_breakdown, zone_heatmap, zone_heatmap_caption
from components.glossary import glossary_expander, help_text
from data import queries, queries_assistant
from data.db import get_read_engine

#: Top de la propuesta 11 dentro del modal: compacto a propósito (§2: "salen
#: los 8-10 más parecidos" es para el buscador completo de `screens/
#: similitud.py`; aquí el usuario ya está mirando a ESTE jugador y solo
#: quiere el resumen rápido).
_SIMILARITY_TOP_N_COMPACT = 5

_ACCENT = "#008300"


def _age(birth_date, today: dt.date) -> str:
    """Edad en años a partir de `birth_date`, o "—" si no hay dato.

    `today` se pasa explícitamente (en vez de llamar a `dt.date.today()`
    dentro) por el mismo motivo que lo hacen las consultas de
    `data/queries.py`: que el resultado no dependa de una llamada oculta al
    reloj.
    """
    if pd.isna(birth_date):
        return "—"
    b = birth_date if isinstance(birth_date, dt.date) else dt.date.fromisoformat(str(birth_date))
    years = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return str(years)


@st.dialog("​", width="large")  # título real dentro (foto+nombre grandes), ver diseño §3
def player_detail(player_id: str) -> None:
    """Modal con toda la estadística disponible de `player_id`.

    Secciones, en orden: bio, medias (combinada + por competición), récords
    de temporada, tendencia de puntos, boxscore partido a partido y mapa de
    tiros de la temporada. Cada una degrada con un aviso si no hay dato — un
    jugador sin partidos registrados (fichaje reciente, o un rival del que
    solo se conoce la ficha) sale con bio y nada más, no roto.
    """
    engine = get_read_engine()
    season_id = st.session_state["season_id"]
    today = dt.date.today()

    bio = queries.player_bio(engine, player_id)
    if bio is None:
        st.error("No se encuentra este jugador.")
        return

    # ---------------------------------------------------------------- cabecera --
    head_photo, head_info = st.columns([1, 4])
    with head_photo:
        st.markdown(
            player_avatar_html(bio["name"], bio["photo_url"], local_path=bio["photo_local_path"], size=160),
            unsafe_allow_html=True,
        )
    with head_info:
        st.markdown(f"### #{bio['number']} · {bio['name']}")
        position = bio["position"] if pd.notna(bio["position"]) else "—"
        nationality = bio["nationality"] if pd.notna(bio["nationality"]) else "—"
        st.caption(position)
        # Peso junto a altura desde que `ingest/euroleague/roster.py` las
        # puebla: son el par que se lee junto al preparar un emparejamiento
        # ("204 cm y 83 kg" no es el mismo cuatro que "204 cm y 108 kg"), y
        # enseñar una sin la otra deja la lectura a medias. "—" en las dos
        # para quien no juega Euroliga: esa API es la única fuente que las
        # publica, y decirlo con un hueco es más honesto que esconder la
        # sección entera.
        # Nacionalidad como campo propio y no colgada del pie de la posición
        # ("Escolta · Eslovenia"): ahí se leía como una coletilla del puesto y
        # pasaba desapercibida, que es justo lo contrario de lo que se busca
        # al preparar un rival. Va la última de las cuatro porque es la única
        # que no es un número.
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Edad", _age(bio["birth_date"], today))
        c2.metric("Altura", f"{int(bio['height_cm'])} cm" if pd.notna(bio["height_cm"]) else "—")
        c3.metric("Peso", f"{int(bio['weight_kg'])} kg" if pd.notna(bio["weight_kg"]) else "—")
        # La nacionalidad NO va en `st.metric` como las tres de al lado: el
        # valor de una métrica es una sola línea con elipsis, y "República
        # Dominicana" se quedaba en "República D…" en la cuarta parte del
        # ancho del modal. Caption + encabezado ocupa el mismo hueco, se lee
        # igual y parte en dos líneas cuando hace falta.
        c4.caption("Nacionalidad")
        c4.markdown(f"### {nationality}")
        # Entrada contextual al chat (ver `local/features/005-chatbot/
        # 01_design.md` §9.4). Cierra el modal por el camino: `st.switch_page`
        # cambia de pantalla, y un modal abierto sobre otra página no tendría
        # sentido.
        ask_assistant_button(
            f"¿Qué tal está jugando {bio['name']} esta temporada?",
            key=f"ask_player_{player_id}",
            context=f"El usuario viene de la ficha del jugador {player_id} ({bio['name']}).",
            label="Preguntar al asistente",
            width="content",
        )

    st.divider()

    # Temporada de la que sacar medias/boxscore/tiros: la seleccionada, o la
    # última con partidos de este jugador si la seleccionada aún no tiene
    # ninguno (fichaje reciente, o inicio de temporada antes de su debut) —
    # mismo caso real que resuelve `queries.team_scouting_season` para el
    # scouting de rival en "Próximo rival", aquí a nivel de jugador.
    scouting = queries.player_scouting_season(engine, player_id, season_id)
    stats_season_id = scouting["season_id"] if scouting else season_id

    # ---------------------------------------------------------------- medias --
    st.markdown("**Medias**")
    if scouting is None:
        st.info("Sin partidos registrados todavía, ni en esta temporada ni en anteriores.")
        return
    if scouting["is_fallback"]:
        st.info(
            f"⚠ {bio['name']} no tiene partidos registrados en la temporada seleccionada. "
            f"**Se muestran los de {scouting['label']}**, su última temporada con datos."
        )
    averages_df = queries.player_averages_all(engine, player_id, stats_season_id)
    if averages_df.empty:
        st.info("Sin partidos registrados todavía para esta temporada.")
    else:
        tabs = st.tabs(averages_df["competition"].tolist())
        for tab, row in zip(tabs, averages_df.itertuples()):
            with tab:
                # Todas las medias con `help=`: es la ficha que se abre desde
                # la galería de plantilla, o sea la primera pantalla donde un
                # ojeador nuevo se encuentra eFG% sin nadie al lado que se lo
                # explique. El texto sale del glosario (`components/glossary.py`).
                cols = st.columns(7)
                cols[0].metric("PJ", int(row.gp), help=help_text("gp"))
                cols[1].metric(
                    "Min", f"{row.min_avg:.1f}" if pd.notna(row.min_avg) else "—", help=help_text("minutes")
                )
                cols[2].metric("Pts", f"{row.pts_avg:.1f}" if pd.notna(row.pts_avg) else "—", help=help_text("pts"))
                cols[3].metric("Reb", f"{row.reb_avg:.1f}" if pd.notna(row.reb_avg) else "—", help=help_text("reb"))
                cols[4].metric("Ast", f"{row.ast_avg:.1f}" if pd.notna(row.ast_avg) else "—", help=help_text("ast"))
                cols[5].metric(
                    "eFG%", f"{row.efg_pct:.1f}" if pd.notna(row.efg_pct) else "—", help=help_text("efg_pct")
                )
                # `ft_pct` puede ser NaN con `gp` > 0 (partidos sin `ftm`/`fta`
                # cargados, ver el docstring de `queries.player_averages_all`).
                cols[6].metric(
                    "FT%", f"{row.ft_pct:.1f}" if pd.notna(row.ft_pct) else "—", help=help_text("ft_pct")
                )

                # Boxscore ampliado (Fase 1): mismo criterio "NaN = sin dato,
                # no cero" que arriba — `gp_box_extras` viaja en `row` pero no
                # se pinta, es solo la señal de cobertura parcial.
                if pd.notna(getattr(row, "stl_avg", None)):
                    extra_cols = st.columns(8)
                    extra_cols[0].metric("Rob", f"{row.stl_avg:.1f}", help=help_text("stl"))
                    extra_cols[1].metric(
                        "Tap", f"{row.blk_avg:.1f}" if pd.notna(row.blk_avg) else "—", help=help_text("blk")
                    )
                    extra_cols[2].metric(
                        "PP", f"{row.tov_avg:.1f}" if pd.notna(row.tov_avg) else "—", help=help_text("tov")
                    )
                    extra_cols[3].metric(
                        "Reb.Of", f"{row.oreb_avg:.1f}" if pd.notna(row.oreb_avg) else "—", help=help_text("oreb")
                    )
                    extra_cols[4].metric(
                        "Reb.Def", f"{row.dreb_avg:.1f}" if pd.notna(row.dreb_avg) else "—", help=help_text("dreb")
                    )
                    extra_cols[5].metric(
                        "Faltas", f"{row.pf_avg:.1f}" if pd.notna(row.pf_avg) else "—", help=help_text("pf")
                    )
                    extra_cols[6].metric(
                        "+/-",
                        f"{row.plus_minus_avg:+.1f}" if pd.notna(row.plus_minus_avg) else "—",
                        help=help_text("plus_minus"),
                    )
                    extra_cols[7].metric(
                        "PIR", f"{row.pir_avg:.1f}" if pd.notna(row.pir_avg) else "—", help=help_text("pir")
                    )

                glossary_expander([
                    "gp", "minutes", "pts", "reb", "ast", "efg_pct", "ft_pct",
                    "stl", "blk", "tov", "oreb", "dreb", "pf", "plus_minus", "pir",
                ])

    # ---------------------------------------------------------- partido a partido --
    log_df = queries.player_game_log(engine, player_id, stats_season_id)

    if log_df.empty:
        st.caption("Sin estadísticas registradas todavía — probablemente una alta reciente.")
        return

    st.divider()

    # -------------------------------------------------------- récords de temporada --
    st.markdown("**Récords de temporada**")
    r1, r2, r3 = st.columns(3)
    for col, stat, label in ((r1, "pts", "Máx. puntos"), (r2, "reb", "Máx. rebotes"), (r3, "ast", "Máx. asistencias")):
        best = log_df.loc[log_df[stat].idxmax()]
        col.metric(label, int(best[stat]))
        col.caption(f"{best['game_date']} · vs {best['rival']}")

    # Fase 1: robos/tapones — solo si el partido a partido trae boxscore
    # ampliado (columna presente Y con algún dato real, no solo NULL).
    if "stl" in log_df.columns and log_df["stl"].notna().any():
        r4, r5 = st.columns(2)
        for col, stat, label in ((r4, "stl", "Máx. robos"), (r5, "blk", "Máx. tapones")):
            if log_df[stat].notna().any():
                best = log_df.loc[log_df[stat].idxmax()]
                col.metric(label, int(best[stat]))
                col.caption(f"{best['game_date']} · vs {best['rival']}")

    st.divider()

    # -------------------------------------------------------------------- tendencia --
    st.markdown("**Tendencia de puntos**")
    trend = (
        alt.Chart(log_df)
        .mark_line(point=True, color=_ACCENT)
        .encode(
            x=alt.X("game_date:O", title=None),
            y=alt.Y("pts:Q", title="Puntos"),
            tooltip=[
                alt.Tooltip("game_date:O", title="Fecha"),
                alt.Tooltip("rival:N", title="Rival"),
                alt.Tooltip("pts:Q", title="Puntos"),
            ],
        )
        .properties(height=200)
    )
    st.altair_chart(trend, width="stretch")

    st.divider()

    # ----------------------------------------------------------- boxscore completo --
    st.markdown("**Partido a partido**")
    st.dataframe(
        log_df,
        hide_index=True,
        width="stretch",
        height=280,
        column_order=["rival_logo_url", "rival", "game_date", "competition", "condicion", "minutes", "pts", "reb", "ast", "efg_pct"],
        column_config={
            # Escudo pegado al nombre (columnas adyacentes) — Streamlit no permite
            # combinar imagen+texto en una misma celda de `st.dataframe`. `rival_logo_url`
            # queda en blanco (no roto) para rivales sin escudo cargado todavía.
            "rival_logo_url": st.column_config.ImageColumn(" ", width=40),
            "rival": st.column_config.TextColumn("Rival"),
            "game_date": st.column_config.TextColumn("Fecha"),
            "competition": st.column_config.TextColumn("Comp."),
            "condicion": st.column_config.TextColumn("Cond."),
            "minutes": st.column_config.NumberColumn("Min", format="%.1f"),
            "pts": st.column_config.NumberColumn("Pts"),
            "reb": st.column_config.NumberColumn("Reb"),
            "ast": st.column_config.NumberColumn("Ast"),
            "efg_pct": st.column_config.NumberColumn("eFG%", format="%.1f"),
        },
    )

    # ------------------------------------------------- avanzadas oficiales (Fase 4) --
    # ACB-only (Euroliga no tiene avanzadas oficiales por partido) y acotado
    # por coste a Baskonia + próximo rival (ver `ingest/acb/pipeline.py::
    # _advanced_stats_scope`) — puede no haber nada que mostrar, y está bien.
    advanced_df = queries_assistant.player_advanced_profile(engine, player_id, stats_season_id)
    if not advanced_df.empty:
        st.divider()
        st.markdown("**Avanzadas oficiales (ACB) — victorias vs. derrotas**")
        wins = advanced_df[advanced_df["win"] == 1]
        losses = advanced_df[advanced_df["win"] == 0]
        a1, a2, a3 = st.columns(3)
        a1.metric("Partidos con dato", len(advanced_df))
        a2.metric(
            "TS% en victorias",
            f"{wins['ts_pct'].mean():.1f}" if not wins.empty and wins["ts_pct"].notna().any() else "—",
        )
        a3.metric(
            "TS% en derrotas",
            f"{losses['ts_pct'].mean():.1f}" if not losses.empty and losses["ts_pct"].notna().any() else "—",
        )
        st.caption(
            "PIR/TS%/ritmo oficiales de acb.com, solo disponibles para el Baskonia y el próximo rival "
            "(coste de red). 'win' se deduce del equipo actual del jugador."
        )

    # -------------------------------------------------------------------- faltas --
    # Propuesta 06 (`doc/features/propuestas/06_gestion_de_faltas.md`), parte
    # (a) a nivel de UN jugador — el perfil de la plantilla entera, con los
    # controles de "carga temprana", vive en "Estado del equipo"; aquí solo
    # su fila, con los umbrales de fábrica (2 antes del 10, 3 antes del 20).
    foul_profile_df = queries_assistant.foul_profile(engine, bio["team_id"], stats_season_id)
    foul_row_df = foul_profile_df[foul_profile_df["player_id"] == player_id]
    if not foul_row_df.empty:
        foul_row = foul_row_df.iloc[0]
        st.divider()
        st.markdown("**Faltas**")
        f1, f2, f3, f4 = st.columns(4)
        f1.metric(
            "Faltas/40",
            f"{foul_row['pf_per40']:.1f}" if pd.notna(foul_row["pf_per40"]) else "—",
            help=help_text("pf_per40"),
        )
        f2.metric(
            "Min. 2.ª falta",
            f"{foul_row['min_2nd_foul_avg']:.1f}" if pd.notna(foul_row["min_2nd_foul_avg"]) else "—",
            help=help_text("min_2nd_foul_avg"),
        )
        f3.metric(
            "Min. 3.ª falta",
            f"{foul_row['min_3rd_foul_avg']:.1f}" if pd.notna(foul_row["min_3rd_foul_avg"]) else "—",
            help=help_text("min_3rd_foul_avg"),
        )
        f4.metric("Cargas tempranas", int(foul_row["early_trouble_games"]), help=help_text("early_trouble_games"))

        if foul_row["early_trouble_games"] > 0:
            g1, g2 = st.columns(2)
            g1.metric(
                "Min. perdidos (aprox.)",
                f"{foul_row['minutes_lost_avg']:+.1f}" if pd.notna(foul_row["minutes_lost_avg"]) else "—",
                help=help_text("minutes_lost_avg"),
            )
            g2.metric(
                "Hueco real en pista",
                f"{foul_row['bench_gap_avg_min']:.1f} min" if pd.notna(foul_row["bench_gap_avg_min"]) else "—",
                help=help_text("bench_gap_avg_min"),
            )
            if pd.notna(foul_row["bench_margin_per_min"]) and pd.notna(foul_row["team_margin_per_min_season"]):
                st.caption(
                    f"⚠ Margen del equipo durante esos huecos: {foul_row['bench_margin_per_min']:+.2f} pts/min "
                    f"(habitual: {foul_row['team_margin_per_min_season']:+.2f} pts/min). Descriptivo, no "
                    "causal — no leer como \"sentarlo cuesta X puntos\" (§5 de la propuesta 06)."
                )
        glossary_expander([
            "pf_per40", "min_2nd_foul_avg", "min_3rd_foul_avg", "early_trouble_games",
            "minutes_lost_avg", "bench_gap_avg_min", "bench_margin_per_min",
        ])

    # -------------------------------------------------------------- mapa de tiros --
    shots_df = queries.player_shots_season(engine, player_id, stats_season_id)
    if shots_df.empty:
        st.caption("Sin tiros con coordenadas registrados esta temporada.")
    else:
        st.divider()
        st.markdown("**Mapa de tiros de la temporada**")
        zones_df = queries.court_zones(engine)
        # A diferencia del equipo (`team_zone_profile`, agregado en origen sin
        # desglose por jugador), aquí sí hay una tabla equivalente por jugador
        # — `player_zone_profile` la calcula de `shots.zone_id` porque no
        # existe un agregado oficial por jugador que reutilizar (ver su
        # docstring).
        zone_df = queries.player_zone_profile(engine, player_id, stats_season_id)

        # Uno al lado del otro, no apilados: son dos lecturas del mismo mapa
        # (nube de tiros vs. acierto por zona) y se comparan mejor en
        # paralelo. Cada gráfico conserva su ancho fijo (dominio cuadrado,
        # sin `use_container_width`/`width="stretch"` — ver `court.py`) y ya
        # trae de serie el icono de pantalla completa de Streamlit al pasar
        # el ratón por encima, para verlo grande sin perder el layout de dos
        # columnas.
        col_shots, col_zones = st.columns(2)
        with col_shots:
            st.altair_chart(shot_chart(shots_df, zones_df))
            st.caption(shot_chart_caption(shots_df))
        with col_zones:
            st.markdown("**Acierto por zona**")
            st.altair_chart(zone_heatmap(zone_df, zones_df))
            st.caption(zone_heatmap_caption(zone_df))
        zone_breakdown(zone_df, len(shots_df), scope="player")

    # ---------------------------------------------------- similitud (propuesta 11) --
    # Bloque compacto: valores por defecto (estilo, top 5), sin controles —
    # el buscador completo con filtros y pesos ajustables vive en
    # `screens/similitud.py` (§6 del documento).
    st.divider()
    st.markdown("**Se parece a...**")
    vectors = similarity_component.league_vectors(engine, stats_season_id)
    if vectors.empty or player_id not in set(vectors["player_id"]):
        st.caption(
            "Sin perfil de percentiles suficiente esta temporada (hace falta al menos 5 partidos en una "
            "competición) para calcular similitud."
        )
    else:
        target_row = vectors.loc[vectors["player_id"] == player_id].iloc[0]
        results = similarity_engine.most_similar(vectors, player_id, method="cosine", top_n=_SIMILARITY_TOP_N_COMPACT)
        labels = similarity_component.competition_labels(engine)
        rows = similarity_component.build_result_rows(target_row, results, labels)
        similarity_component.results_list(rows)
        similarity_component.similarity_caveat()
        if st.button("Abrir buscador completo (filtros y pesos)", key=f"similarity_open_finder_{player_id}"):
            st.session_state["similarity_preselect_player_id"] = player_id
            st.switch_page("screens/similitud.py")
