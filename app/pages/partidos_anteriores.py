"""Pantalla B — Partidos anteriores.

Selector de partidos ya disputados del Baskonia + detalle del partido
elegido (marcador, parciales, avanzadas, boxscore, tiros, quintetos,
eventos clave). Contrato de datos completo en
`local/features/001-interfaz-baskonia/01_design.md` §4.
"""
import datetime as dt

import altair as alt
import streamlit as st

from analytics import shot_quality
from assistant.llm import LLMError, build_llm_client
from components.ask_assistant import ask_assistant_button
from components.court import shot_chart, shot_chart_caption
from components.glossary import abbr, glossary_expander, help_text
from components.header import page_header
from components.rotation_chart import event_label, rotation_chart
from components.shot_quality import quality_caveat, quality_metrics
from data import queries, queries_assistant
from data.db import get_read_engine
from reports import postgame_ppt

_ACCENT = "#008300"
_MUTED = "#898781"
_PPT_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

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
        width="stretch",
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

    # Cabecera de metadata (Fase 3): árbitro(s)/asistencia/pabellón — ya
    # viaja en las respuestas que se descargan hoy en las dos fuentes (ver
    # doc/features/ingestor/02_plan_stats_completas.md §Fase 3). `None` en
    # cualquier campo = partido ingerido antes de esta fase, o la fuente no
    # lo dio para ese partido concreto (no se pinta nada en ese caso, no un
    # hueco vacío).
    meta = queries.game_metadata(engine, game_id)
    if meta:
        meta_bits = []
        if meta.get("arena"):
            meta_bits.append(f"🏟️ {meta['arena']}")
        if meta.get("attendance"):
            meta_bits.append(f"👥 {int(meta['attendance']):,} espectadores".replace(",", "."))
        if meta.get("referees"):
            meta_bits.append(f"🧑‍⚖️ {meta['referees']}")
        if meta_bits:
            st.caption(" · ".join(meta_bits))

    # Ficha arbitral (propuesta 05, `doc/features/propuestas/05_perfil_arbitral.md`
    # §6): a diferencia de "Próximo rival" (donde la terna todavía no se
    # conoce y hay que teclearla), aquí el partido YA se jugó — `referees_for_game`
    # da la terna exacta, ya canonicalizada, sin que el usuario tenga que
    # buscarla a mano.
    referees_df = queries_assistant.referees_for_game(engine, game_id)
    if not referees_df.empty:
        with st.expander(f"🧑‍⚖️ Ficha arbitral ({', '.join(referees_df['referee_name'])})"):
            for referee_name in referees_df["referee_name"]:
                st.markdown(f"**{referee_name}**")
                profile = queries_assistant.referee_profile(engine, referee_name, games_season_id)
                if profile is None:
                    st.caption(
                        f"Muestra insuficiente en la temporada (menos de "
                        f"{queries_assistant.REFEREE_MIN_GAMES_TO_SHOW} partidos con datos completos)."
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
                    st.caption(f"⚠ Solo {int(profile['gp'])} partidos esta temporada: tendencia gruesa.")

                history = queries_assistant.referee_team_history(engine, referee_name, games_season_id, team_id)
                if history is not None and history["pf_avg"] is not None:
                    baseline_bit = (
                        f" (media de temporada: {history['season_pf_avg']:.1f})"
                        if history.get("season_pf_avg") is not None else ""
                    )
                    st.caption(
                        f"Con el Baskonia esta temporada: {history['wins']}–{history['losses']}, "
                        f"{history['pf_avg']:.1f} faltas señaladas/partido{baseline_bit}."
                    )
            glossary_expander(
                ["referee_pf_residual", "referee_home_bias_fta", "referee_pace_residual"]
            )

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

    # "PPT para Paolo" (encargo directo del cuerpo técnico): una diapositiva
    # por jugador del Baskonia con lo más destacado — para bien o para mal —
    # de ESTE partido. Genera los bytes al pulsar y los deja en
    # `session_state` para que el `download_button` (que en Streamlit hace su
    # propio rerun) siga teniendo el fichero listo sin regenerarlo dos veces.
    # Clave por `game_id`: cambiar de partido no debe ofrecer para descargar
    # la PPT de otro.
    ppt_state_key = f"ppt_bytes_{game_id}"
    if st.button("Generar PPT para Paolo", key=f"ppt_btn_{game_id}", width="stretch"):
        with st.spinner("Generando la PPT con los puntos destacados de cada jugador..."):
            game_context = {
                "rival": rival_name,
                "resultado": f"Baskonia {int(selected_game['pts_favor'])}-{int(selected_game['pts_contra'])} {rival_name}",
                "competicion": selected_game["competition"],
                "fecha": selected_game["game_date"],
                "subtitle": (
                    f"{'vs' if selected_game['condicion'] == 'Local' else '@'} {rival_name} · "
                    f"{int(selected_game['pts_favor'])}–{int(selected_game['pts_contra'])} · "
                    f"{selected_game['game_date']}"
                ),
            }
            try:
                llm_client = build_llm_client()
            except LLMError:
                # Proveedor configurado pero inutilizable (SDK que falta, clave
                # rechazada): la PPT sale igual, con el fallback por reglas de
                # `postgame_ppt.select_highlights` — este botón no depende del chat.
                llm_client = None
            try:
                st.session_state[ppt_state_key] = postgame_ppt.generate_postgame_ppt(
                    engine, game_id, team_id, game_context, llm_client=llm_client
                )
            except ValueError as exc:
                st.session_state.pop(ppt_state_key, None)
                st.warning(str(exc))

    if st.session_state.get(ppt_state_key):
        st.download_button(
            "Descargar PPT",
            data=st.session_state[ppt_state_key],
            file_name=f"baskonia_vs_{rival_name}_{selected_game['game_date']}.pptx".replace(" ", "_"),
            mime=_PPT_MIME,
            key=f"ppt_dl_{game_id}",
            width="stretch",
        )

    tab_resumen, tab_box, tab_tiros, tab_quintetos, tab_rotaciones = st.tabs(
        ["Resumen", "Boxscore", "Tiros", "Quintetos", "Rotaciones"]
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
            st.altair_chart(chart, width="stretch")
            st.caption(f"🟢 Baskonia · ⚪ {selected_game['rival']}")

            # Faltas por cuarto (Fase 2), derivadas de play_events — puede
            # faltar aunque los puntos por cuarto sí estén (no todos los
            # partidos tienen play-by-play tipado todavía).
            fouls_df = quarters_df.dropna(subset=["fouls_for"])
            if not fouls_df.empty:
                fouls_chart = (
                    alt.Chart(fouls_df)
                    .mark_bar()
                    .encode(
                        x=alt.X("quarter:O", title="Cuarto"),
                        y=alt.Y("fouls_for:Q", title="Faltas"),
                        color=alt.Color(
                            "is_us:N",
                            scale=alt.Scale(domain=[True, False], range=[_ACCENT, _MUTED]),
                            legend=None,
                        ),
                        xOffset="team_name:N",
                        tooltip=[alt.Tooltip("team_name:N", title="Equipo"), alt.Tooltip("fouls_for:Q", title="Faltas")],
                    )
                    .properties(height=180)
                )
                st.caption("Faltas cometidas por cuarto")
                st.altair_chart(fouls_chart, width="stretch")

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
                # Boxscore ampliado de equipo (Fase 1): NaN = partido ingerido
                # antes de esa fase, mismo criterio de "sin dato" que arriba.
                ("stl", "Robos"), ("blk", "Tapones"), ("pf", "Faltas"), ("pir", "PIR"),
            ]
            st.markdown("**Avanzadas** (Baskonia · rival)")
            for key, label in metrics:
                us_val = us_row[key].iloc[0] if not us_row.empty and us_row[key].notna().iloc[0] else None
                them_val = them_row[key].iloc[0] if not them_row.empty and them_row[key].notna().iloc[0] else None
                c1, c2 = st.columns([1, 3])
                # `abbr` y no el `help=` de `st.caption`: ese argumento es
                # reciente y `app/requirements.txt` admite Streamlit desde la
                # 1.36. El `<abbr>` es el tooltip del navegador y funciona en
                # cualquier versión — y aquí, además, el subrayado punteado
                # avisa de que la sigla se puede consultar.
                c1.caption(abbr(key, label), unsafe_allow_html=True)
                c2.markdown(
                    f"**{us_val:.1f}**&nbsp;&nbsp;·&nbsp;&nbsp;:gray[{them_val:.1f}]"
                    if us_val is not None and them_val is not None
                    else "—"
                )
            glossary_expander([key for key, _ in metrics])

    with tab_box:
        def _render_boxscore(box_df):
            if box_df.empty:
                st.info("Sin boxscore para este equipo en este partido (hueco de datos en origen).")
                return
            st.dataframe(
                box_df,
                hide_index=True,
                width="stretch",
                column_order=[
                    "player_name", "minutes", "pts", "reb", "ast", "efg_pct",
                    "stl", "blk", "tov", "pf", "plus_minus", "pir",
                ],
                column_config={
                    # `help=` en cada columna: es el hover de la cabecera, que
                    # explica la sigla donde está y sin ocupar sitio. El texto
                    # sale del glosario (`components/glossary.py`), única
                    # definición de cada sigla en toda la interfaz.
                    "player_name": st.column_config.TextColumn("Jugador"),
                    "minutes": st.column_config.NumberColumn("Min", format="%.1f", help=help_text("minutes")),
                    "pts": st.column_config.NumberColumn("Pts", help=help_text("pts")),
                    "reb": st.column_config.NumberColumn("Reb", help=help_text("reb")),
                    "ast": st.column_config.NumberColumn("Ast", help=help_text("ast")),
                    "efg_pct": st.column_config.NumberColumn("eFG%", format="%.1f", help=help_text("efg_pct")),
                    # Boxscore ampliado (Fase 1) — columnas vacías en vez de
                    # ausentes si el partido no lo trae (NULL real en la BD).
                    "stl": st.column_config.NumberColumn("Rob", help=help_text("stl")),
                    "blk": st.column_config.NumberColumn("Tap", help=help_text("blk")),
                    "tov": st.column_config.NumberColumn("PP", help=help_text("tov")),
                    "pf": st.column_config.NumberColumn("Faltas", help=help_text("pf")),
                    "plus_minus": st.column_config.NumberColumn("+/-", help=help_text("plus_minus")),
                    "pir": st.column_config.NumberColumn("PIR", help=help_text("pir")),
                    "player_id": None,
                    "team_id": None,
                    "blk_against": None,
                    "pf_drawn": None,
                    "oreb": None,
                    "dreb": None,
                    "dunks": None,
                },
            )

        st.markdown("**Baskonia**")
        _render_boxscore(queries.game_boxscore(engine, game_id, team_id))
        st.divider()
        st.markdown(f"**{rival_name}**")
        _render_boxscore(queries.game_boxscore(engine, game_id, rival_team_id))
        glossary_expander(
            ["minutes", "pts", "reb", "ast", "efg_pct", "stl", "blk", "tov", "pf", "plus_minus", "pir"]
        )

    with tab_tiros:
        # Calidad de tiro (xPPS) — propuesta 02. Va ARRIBA del mapa a
        # propósito: es lo que separa las dos conversaciones distintas de
        # después de una derrota ("tiramos bien y no entró" contra "tiramos
        # mal"), y el mapa de puntos de debajo es el detalle de esa respuesta,
        # no al revés.
        #
        # Los dos bandos juntos, no solo el nuestro: el xPPS CONCEDIDO es la
        # métrica defensiva honesta del partido, porque no premia que el rival
        # fallara tiros abiertos — que es justo lo que hace el %TC en contra.
        game_counts = queries.game_shot_counts(engine, game_id)
        baseline = shot_quality.league_baseline(queries.league_shot_counts(engine, games_season_id))
        valued = shot_quality.with_expected(game_counts, baseline)
        if valued.empty:
            st.caption(
                "Sin tiros localizados y clasificados por zona en este partido: no hay calidad "
                "de tiro que medir."
            )
        else:
            # Referencia con la que comparar la generación de ESTE partido: la
            # media de la temporada del propio equipo (en ataque y en defensa).
            # Sin ella, un 1,04 xPPS no dice nada; con ella dice si el partido
            # se salió de lo normal, que es la pregunta real.
            season_own = shot_quality.summarize(
                shot_quality.with_expected(queries.team_shot_counts(engine, team_id, games_season_id), baseline)
            )
            season_conceded = shot_quality.summarize(
                shot_quality.with_expected(
                    queries.team_shot_counts(engine, team_id, games_season_id, conceded=True), baseline
                )
            )
            own_col, rival_col = st.columns(2)
            with own_col:
                st.markdown("**Lo que generamos**")
                quality_metrics(
                    shot_quality.summarize(valued.loc[valued["team_id"] == team_id]),
                    reference_xpps=season_own["xpps"] if season_own["shots"] else None,
                    subject="nuestro ataque",
                )
            with rival_col:
                st.markdown(f"**Lo que le concedimos a {rival_name}**")
                quality_metrics(
                    shot_quality.summarize(valued.loc[valued["team_id"] == rival_team_id]),
                    reference_xpps=season_conceded["xpps"] if season_conceded["shots"] else None,
                    subject="nuestra defensa",
                    conceded=True,
                )
            _, coverage = shot_quality.split_usable(game_counts)
            quality_caveat(coverage)
            glossary_expander(["xpps", "pps", "diff_shrunk", "shots"])
        st.divider()

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
                width="stretch",
                column_order=["jugadores", "minutes", "plus_minus"],
                column_config={
                    "jugadores": st.column_config.TextColumn("Quinteto", width="large"),
                    # Claves de quinteto y no de jugador: los mismos minutos y
                    # el mismo +/- significan otra cosa cuando el sujeto son
                    # cinco y no uno (ver `components/glossary.py`).
                    "minutes": st.column_config.NumberColumn(
                        "Min", format="%.1f", help=help_text("lineup_minutes")
                    ),
                    "plus_minus": st.column_config.NumberColumn("+/-", help=help_text("lineup_plus_minus")),
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

        # Faltas con reloj exacto (Fase 2), mismo formato que "Eventos clave"
        # de arriba — es lo que permite leer el momento de acumulación de
        # faltas ("foul trouble"), no solo el total del boxscore.
        fouls_df = queries_assistant.game_play_events(engine, game_id, event_type="foul_personal")
        if not fouls_df.empty:
            st.divider()
            st.markdown("**Faltas personales**")
            for row in fouls_df.itertuples():
                who = f" — {row.player_name}" if row.player_name else ""
                st.caption(f"{row.quarter} · {row.game_clock} — {row.team_name}{who}")

    with tab_rotaciones:
        # Rotaciones y parciales explicados
        # (`doc/features/propuestas/01_rotaciones_y_parciales.md`). La vista es
        # siempre de UN partido, así que su sitio es esta pantalla y no "Estado
        # del equipo": lo que responde es "¿dónde se fue ESTE partido, y quién
        # estaba en pista cuando se fue?".
        stints_df = queries.game_stints(engine, game_id, team_id)
        steps_df = queries.game_score_steps(engine, game_id, team_id)

        # Faltas sobre el timeline (propuesta 06, §2c): momento exacto de
        # cada falta personal, más el minuto en que cada equipo entra en
        # bonus por cuarto. Se piden una vez para el partido entero y se
        # filtran por equipo abajo — `foul_timeline`/`foul_bonus_minutes` no
        # distinguen "propio" de "rival", igual que `game_stints`.
        foul_events_df = queries_assistant.foul_timeline(engine, game_id)
        personal_fouls_df = (
            foul_events_df[foul_events_df["event_type"] == "foul_personal"]
            if not foul_events_df.empty else foul_events_df
        )
        bonus_df = queries_assistant.foul_bonus_minutes(foul_events_df)

        if stints_df.empty and steps_df.empty:
            st.info(
                "Este partido no tiene ni tramos de quinteto ni play-by-play tipado: se ingirió "
                "antes de las fases que los cargan."
            )
        else:
            if steps_df.empty:
                st.warning(
                    "Sin play-by-play tipado en este partido: se ven las rotaciones, pero no el "
                    "margen de fondo ni los parciales."
                )
            if stints_df.empty:
                st.warning(
                    "Sin tramos de quinteto en este partido: se ve el marcador, pero no quién "
                    "estaba en pista."
                )

            # Los dos umbrales van en la interfaz y no en el código: cada
            # entrenador tiene su idea de "parcial preocupante", y 8 puntos en
            # 3 minutos es el punto de partida, no la definición.
            c_window, c_swing = st.columns(2)
            window_min = c_window.slider(
                "Duración máxima del parcial (min)", 1.0, 6.0, 3.0, 0.5,
                key=f"run_window_{game_id}",
            )
            min_swing = c_swing.slider(
                "Diferencia mínima del parcial (puntos)", 4, 20, 8,
                key=f"run_swing_{game_id}",
            )
            runs_df = queries.game_runs(engine, game_id, team_id, window_min * 60.0, min_swing)

            st.altair_chart(
                rotation_chart(
                    stints_df, steps_df, runs_df, title="Baskonia",
                    fouls=personal_fouls_df[personal_fouls_df["team_id"] == team_id],
                    bonus=bonus_df[bonus_df["team_id"] == team_id],
                ),
                width="stretch",
            )
            st.caption(
                "Barras = minutos en pista · fondo = margen del marcador (🟢 a favor, 🔴 en contra) · "
                "franjas sombreadas = parciales detectados con los umbrales de arriba · marca ámbar = "
                "falta personal · raya ámbar discontinua = el equipo entra en bonus ese cuarto."
            )

            if runs_df.empty:
                st.info(
                    f"Ningún parcial de {min_swing} puntos o más en {window_min:g} minutos o menos. "
                    "Baja los umbrales para ver movimientos más pequeños."
                )
            else:
                choice = st.selectbox(
                    f"Parciales detectados ({len(runs_df)}), del mayor al menor",
                    options=runs_df["label"].tolist(),
                    key=f"run_pick_{game_id}",
                    # La propia etiqueta es una sigla más ("Q4 05:15 a Q4
                    # 03:20 · 13-3 (+10)") y no se explica sola.
                    help=(
                        "Se lee: desde qué reloj hasta qué reloj, los puntos del parcial "
                        "(a favor-en contra) y, entre paréntesis, cuánto se movió la diferencia "
                        "en el marcador. Positivo = a favor del Baskonia."
                    ),
                )
                run = runs_df[runs_df["label"] == choice].iloc[0]

                lineup_col, events_col = st.columns([1, 2], gap="medium")
                with lineup_col:
                    st.markdown("**Quinteto del parcial**")
                    lineup_df = queries.window_lineup(stints_df, run["start_seconds"], run["end_seconds"])
                    if lineup_df.empty:
                        st.caption("Sin tramos de quinteto que solapen esta ventana.")
                    else:
                        st.dataframe(
                            lineup_df,
                            hide_index=True,
                            width="stretch",
                            column_order=["player_name", "minutes", "share"],
                            column_config={
                                "player_name": st.column_config.TextColumn("Jugador"),
                                "minutes": st.column_config.NumberColumn(
                                    "Min", format="%.1f", help=help_text("run_minutes")
                                ),
                                "share": st.column_config.NumberColumn(
                                    "% del parcial", format="%.0f", help=help_text("run_share")
                                ),
                                "player_id": None,
                                "seconds": None,
                            },
                        )
                        st.caption(
                            f"Los cinco del tramo que más pesa en la ventana; los minutos son los "
                            f"suyos DENTRO del parcial ({run['duration_s'] / 60:.1f} min)."
                        )

                with events_col:
                    st.markdown("**Qué pasó en esos minutos**")
                    events_df = queries.game_window_events(
                        engine, game_id, run["start_seconds"], run["end_seconds"], team_id
                    )
                    if events_df.empty:
                        st.caption("Sin eventos tipados en esta ventana.")
                    else:
                        display_events = events_df.assign(
                            reloj=events_df["quarter"] + " " + events_df["game_clock"],
                            evento=events_df["event_type"].map(event_label),
                            quien=events_df["player_name"].fillna(""),
                            equipo=events_df["is_own"].map({True: "Baskonia", False: rival_name}),
                            marcador=(
                                events_df["score_for"].astype(str) + "-" + events_df["score_against"].astype(str)
                            ),
                        )
                        st.dataframe(
                            display_events,
                            hide_index=True,
                            width="stretch",
                            column_order=["reloj", "marcador", "equipo", "evento", "quien"],
                            column_config={
                                "reloj": st.column_config.TextColumn("Reloj"),
                                "marcador": st.column_config.TextColumn("Marcador"),
                                "equipo": st.column_config.TextColumn("Equipo"),
                                "evento": st.column_config.TextColumn("Evento"),
                                "quien": st.column_config.TextColumn("Jugador"),
                            },
                        )
                    # Limitación que el entrenador tiene que ver AQUÍ y no
                    # deducir (§5 de la propuesta): `shots` no guarda ni cuarto
                    # ni reloj, así que ningún tiro puede salir en esta lista.
                    st.caption(
                        "⚠ Los tiros no salen en esta lista: la fuente no les guarda ni cuarto ni "
                        "reloj. Los puntos se leen por el salto del marcador."
                    )

            with st.expander(f"Rotaciones de {rival_name}"):
                rival_stints = queries.game_stints(engine, game_id, rival_team_id)
                rival_steps = queries.game_score_steps(engine, game_id, rival_team_id)
                if rival_stints.empty and rival_steps.empty:
                    st.info("Sin tramos ni play-by-play del rival en este partido.")
                else:
                    st.altair_chart(
                        rotation_chart(
                            rival_stints,
                            rival_steps,
                            queries.game_runs(engine, game_id, rival_team_id, window_min * 60.0, min_swing),
                            is_own_team=False,
                            title=rival_name,
                            fouls=personal_fouls_df[personal_fouls_df["team_id"] == rival_team_id],
                            bonus=bonus_df[bonus_df["team_id"] == rival_team_id],
                        ),
                        width="stretch",
                    )
                    st.caption(
                        "El mismo gráfico visto desde el rival, para preparar sus patrones de "
                        "rotación: cuándo descansa a su base, con qué quinteto abre el último cuarto."
                    )

            glossary_expander(["margin", "run_swing", "run_minutes", "run_share"])
