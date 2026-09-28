"""Pintado del patrón de rotación de un equipo (propuesta 13).

El cálculo vive en `app/analytics/rotation_patterns.py`; aquí se decide cómo
se enseña: primero las frases (lo que se lleva a la reunión), después el mapa
de minutos (de dónde salen) y al final el detalle.

Se importa con el mismo doble camino que el resto de `app/`.
"""
import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import rotation_patterns as rp
    from app.components.glossary import glossary_expander
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import rotation_patterns as rp
    from components.glossary import glossary_expander
    from data import queries, queries_assistant

_POS = "#008300"
_NEG = "#c0392b"
_WINDOWS = {"Toda la temporada": None, "Últimos 10 partidos": 10, "Últimos 5 partidos": 5}
#: Jugadores en el mapa de minutos: los de más minutos por partido. Más allá
#: son minutos de la basura y alargan el gráfico sin decir nada.
_HEATMAP_PLAYERS = 12


def rotation_pattern_section(engine, team_id: str, season_id: int, team_name: str, *, key: str) -> None:
    """Sección entera: selector de ventana, resumen, mapa de minutos, titulares/cerradores y tramos."""
    st.subheader("Patrón de rotación")
    window_label = st.radio(
        "Partidos", options=list(_WINDOWS), horizontal=True, key=f"{key}_window", label_visibility="collapsed"
    )
    rows = queries.team_stint_rows(engine, team_id, season_id, last_n_games=_WINDOWS[window_label])
    if rows.empty:
        st.info(f"No hay tramos de quinteto de {team_name} en esta temporada.")
        return

    n_games = rows["game_id"].nunique()
    shares = rp.minute_shares(rows)
    rotation = rp.player_rotation_table(shares)
    starters = rp.starting_lineups(rows)
    closers, close_games = rp.closing_players(rows)
    blocks = rp.block_performance(rows)
    on_off = queries_assistant.player_on_off(engine, team_id, season_id)

    lines = rp.rotation_insights(
        team_name, rotation, starters, closers, close_games, blocks, on_off, n_games=n_games
    )
    if lines:
        st.markdown("\n".join(f"- {line}" for line in lines))

    # ------------------------------------------------------- mapa de minutos --
    top_ids = rotation.head(_HEATMAP_PLAYERS)["player_id"].tolist()
    order = rotation.head(_HEATMAP_PLAYERS)["player_name"].tolist()
    heat_df = shares[shares["player_id"].isin(top_ids)]
    heat = (
        alt.Chart(heat_df)
        .mark_rect()
        .encode(
            x=alt.X("minute:O", title="Minuto de partido", axis=alt.Axis(values=[1, 5, 10, 15, 20, 25, 30, 35, 40], labelAngle=0)),
            y=alt.Y("player_name:N", sort=order, title=None, axis=alt.Axis(labelLimit=220, labelOverlap=False)),
            color=alt.Color(
                "share:Q",
                scale=alt.Scale(domain=[0, 1], range=["#f2f1ec", _POS]),
                legend=alt.Legend(title="% en pista", format="%"),
            ),
            tooltip=[
                alt.Tooltip("player_name:N", title="Jugador"),
                alt.Tooltip("minute:O", title="Minuto"),
                alt.Tooltip("share:Q", title="En pista", format=".0%"),
            ],
        )
    )
    st.altair_chart(heat.properties(height=alt.Step(22)), width="stretch")
    st.caption(
        f"{n_games} partidos con tramos. Cada celda: en qué proporción de esos partidos estaba el jugador en "
        "pista en ese minuto. Las franjas claras dentro de una fila oscura son sus descansos habituales."
    )

    # ------------------------------------------------- titulares y cerradores --
    col_start, col_close = st.columns(2)
    with col_start:
        st.markdown("**Quintetos iniciales**")
        if starters.empty:
            st.caption("Sin datos del quinteto de salida.")
        else:
            table = starters.head(4).assign(quinteto=starters.head(4)["players"].map(" · ".join))
            st.dataframe(
                table,
                hide_index=True,
                width="stretch",
                column_order=["quinteto", "games", "share"],
                column_config={
                    "quinteto": st.column_config.TextColumn("Quinteto", width="large"),
                    "games": st.column_config.NumberColumn("Partidos"),
                    "share": st.column_config.ProgressColumn("% partidos", format="percent", min_value=0, max_value=1),
                },
            )
    with col_close:
        st.markdown(f"**Quién cierra los partidos apretados** ({close_games})")
        if closers.empty:
            st.caption(
                f"Ningún partido llegó al minuto {rp.CLOSING_FROM_MINUTE} con ±{rp.CLOSE_MARGIN} o menos."
            )
        else:
            st.dataframe(
                closers.head(8),
                hide_index=True,
                width="stretch",
                column_order=["player_name", "share"],
                column_config={
                    "player_name": st.column_config.TextColumn("Jugador"),
                    "share": st.column_config.ProgressColumn(
                        "% de los minutos finales", format="percent", min_value=0, max_value=1
                    ),
                },
            )

    # -------------------------------------------------------- tramos del partido --
    st.markdown("**Diferencia por tramo del partido**")
    valid = blocks.dropna(subset=["per_40"])
    if not valid.empty:
        cap = float(valid["per_40"].abs().max() or 1.0)
        bars = (
            alt.Chart(valid)
            .mark_bar()
            .encode(
                x=alt.X("block:N", sort=valid["block"].tolist(), title="Minutos", axis=alt.Axis(labelAngle=0)),
                y=alt.Y("per_40:Q", title="+/- por 40", scale=alt.Scale(domain=[-cap, cap])),
                color=alt.condition(alt.datum.per_40 >= 0, alt.value(_POS), alt.value(_NEG)),
                tooltip=[
                    alt.Tooltip("block:N", title="Minutos"),
                    alt.Tooltip("per_40:Q", title="+/- por 40", format="+.1f"),
                    alt.Tooltip("plus_minus:Q", title="+/- total", format="+.0f"),
                    alt.Tooltip("minutes:Q", title="Minutos de muestra", format=".0f"),
                ],
            )
            .properties(height=220)
        )
        st.altair_chart(bars, width="stretch")
    st.caption(
        "Dónde gana y dónde pierde el partido, por reloj. Un valle en los minutos en que sienta a sus "
        "titulares es la ventana para apretar. Los puntos de cada tramo de quinteto se reparten por tiempo "
        "entre los bloques que toca (no hay canasta a canasta en `lineup_stints`)."
    )
    glossary_expander(["minute_share", "rest_window", "block_per_40", "on_per_40", "off_per_40"])
