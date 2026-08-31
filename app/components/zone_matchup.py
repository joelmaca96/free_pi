"""Pintado de "dónde castigar al rival" (propuesta 08): pistas partidas y lista corta de zonas.

El cálculo vive en `app/analytics/zone_matchup.py`, que no sabe nada de
Streamlit; aquí solo se decide cómo se enseña. Reutiliza el mapa de calor
"vs. liga" que ya pinta `components/court.py::zone_heatmap` para la calidad
de tiro (propuesta 02): es la misma pregunta —"¿aquí somos buenos, o tira
bien todo el mundo?"— aplicada a un cruce de dos equipos en vez de a uno
solo, así que se pinta con el mismo componente en vez de duplicarlo.

Se importa con el mismo doble camino que el resto de `app/` (`app.` desde
los tests, sin prefijo desde Streamlit, que corre con `app/` en el
`sys.path` — ver `app/components/shot_quality.py`).
"""
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import zone_matchup
    from app.components import glossary
    from app.components.court import zone_heatmap, zone_heatmap_caption
except ImportError:  # pragma: no cover
    from analytics import zone_matchup
    from components import glossary
    from components.court import zone_heatmap, zone_heatmap_caption


def split_court_matchup(
    concede_df: pd.DataFrame,
    produce_df: pd.DataFrame,
    zones_geo: pd.DataFrame,
    concede_title: str,
    produce_title: str,
) -> None:
    """Dos pistas lado a lado, cada una "vs. liga": lo que se concede y lo que se produce.

    Un entrenador ve la coincidencia sin leer un número (§2a del documento):
    si el mismo rectángulo sale verde en las dos pistas, ahí hay un plan. Cada
    pista es el mismo `zone_heatmap(mode="vs_league")` que ya pinta la
    calidad de tiro — el color es absoluto (contra la liga), no relativo
    entre zonas del propio gráfico, que es justo lo que hace comparables las
    dos pistas entre sí.

    Args:
        concede_df / produce_df: salida de `zone_matchup.zone_diff_profile`.
        zones_geo: salida de `queries.court_zones` (geometría compartida).
    """
    col_concede, col_produce = st.columns(2)
    with col_concede:
        st.markdown(f"**{concede_title}**")
        st.altair_chart(zone_heatmap(concede_df, zones_geo, mode="vs_league"))
        st.caption(zone_heatmap_caption(concede_df, mode="vs_league"))
    with col_produce:
        st.markdown(f"**{produce_title}**")
        st.altair_chart(zone_heatmap(produce_df, zones_geo, mode="vs_league"))
        st.caption(zone_heatmap_caption(produce_df, mode="vs_league"))


def targets_table(targets: pd.DataFrame, empty_message: str) -> None:
    """La lista corta y accionable: salida de `zone_matchup.attack_targets`.

    Ordenada por puntos por partido en juego (ya viene ordenada así de
    `attack_targets`), no por diferencia de porcentaje: una zona con +8 pp
    pero poco volumen vale menos que otra con +3 pp y mucho más (§2b del
    documento). `st.dataframe` respeta el orden de filas que trae `targets`.
    """
    if targets.empty:
        st.info(empty_message)
        return
    st.dataframe(
        targets,
        hide_index=True,
        width="stretch",
        column_order=[
            "zone_label", "value_pts_per_game", "concede_diff_pp", "produce_diff_pp",
            "shots_per_game", "shot_value",
        ],
        column_config={
            "zone_label": st.column_config.TextColumn("Zona", help=glossary.help_text("zone_label")),
            "value_pts_per_game": st.column_config.NumberColumn(
                "Pts/partido en juego", format="%+.2f", help=glossary.help_text("value_pts_per_game")
            ),
            "concede_diff_pp": st.column_config.NumberColumn(
                "Concede vs. liga", format="%+.1f", help=glossary.help_text("concede_diff_pp")
            ),
            "produce_diff_pp": st.column_config.NumberColumn(
                "Produce vs. liga", format="%+.1f", help=glossary.help_text("produce_diff_pp")
            ),
            "shots_per_game": st.column_config.NumberColumn(
                "Tiros/partido", format="%.1f", help=glossary.help_text("shots_per_game")
            ),
            "shot_value": st.column_config.NumberColumn(
                "Vale", help="Puntos que vale una canasta desde esa zona: 2 o 3, derivado de la zona."
            ),
            "concede_volume": None,
            "produce_volume": None,
        },
    )


def matchup_caveat() -> None:
    """Pie de sección: muestra mínima, regularización y qué NO dice esta lista (§5 del documento)."""
    st.caption(
        f"Solo zonas con al menos {zone_matchup.MIN_SHOTS} tiros en LOS DOS lados (concede y produce); "
        "por debajo, ni aparece en la lista — con menos, el error típico del acierto es del orden de la "
        f"señal que se busca. La diferencia con la que se ordena va regularizada hacia 0 "
        f"(n/(n+{zone_matchup.SHRINK_K})), no es la diferencia bruta."
    )
    st.caption(
        "**Concedes lo que defiendes.** Poco volumen con buen porcentaje puede ser buena defensa (cede "
        "pocos tiros ahí) o mala (los pocos que cede, entran) — vigila siempre el volumen junto al "
        "acierto, nunca uno sin el otro. Sin defensor ni tipo de acción en los datos: esto dice DÓNDE "
        "atacar o cuidar, no CÓMO — la pizarra la sigue poniendo el entrenador. Un año de datos: la "
        "diferencia entre \"concede la esquina\" y \"la concedió en 20 partidos\" es real."
    )
