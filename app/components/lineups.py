"""Sección "Quintetos más utilizados", compartida por las pantallas que la enseñan.

Estaba copiada literalmente en `screens/estado_equipo.py` (sobre el Baskonia)
y en `screens/proximo_rival.py` (sobre el rival): mismo selector de
competición, misma consulta, mismas cinco columnas con el mismo
`column_config`. Y ya había empezado a divergir por el sitio por donde
siempre divergen estas copias — la del rival se había quedado sin la nota que
explica que "Min. juntos" y "+/-" suman tramos de partidos distintos, y sin
el desplegable del glosario. Con las dos aquí, la tabla dice lo mismo en las
dos pantallas por construcción y no por disciplina.

Lo único que cambia entre las dos llamadas es de quién son los quintetos
(para el mensaje de "no hay") y el `key` del selector, que tiene que ser
distinto o Streamlit funde los dos widgets en uno.

Se importa con el mismo doble camino que el resto de `app/` (`app.` desde los
tests, sin prefijo desde Streamlit, que corre con `app/` en el `sys.path`).
"""
from typing import Optional

import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.components.glossary import glossary_expander, help_text
    from app.data import queries
except ImportError:  # pragma: no cover
    from components.glossary import glossary_expander, help_text
    from data import queries

_GLOSSARY_KEYS = ["lineup_minutes", "lineup_plus_minus", "stints"]


def _competition_filter(engine, key: str) -> tuple[Optional[int], str]:
    """Selector de competición -> (`competition_id` o `None`, etiqueta elegida)."""
    competitions = queries.list_competitions(engine)
    choice = st.selectbox("Competición", options=["Todas"] + competitions["name"].tolist(), key=key)
    if choice == "Todas":
        return None, choice
    return int(competitions.loc[competitions["name"] == choice, "id"].iloc[0]), choice


def season_lineups_section(engine, team_id: str, season_id: int, *, key: str, subject: str) -> None:
    """Pinta el bloque entero: selector de competición + tabla + notas.

    Args:
        team_id: equipo del que se piden los quintetos.
        season_id: temporada de la que se piden. En "Próximo rival" NO es la
            del selector lateral sino la de scouting del rival, que puede ser
            anterior (ver `queries.team_scouting_season`) — por eso llega como
            argumento y no se lee de `st.session_state`.
        key: `key` del selectbox de competición. Distinto en cada pantalla, o
            Streamlit trata los dos selectores como el mismo widget.
        subject: de quién son los quintetos, para el mensaje de "no hay"
            ("el Baskonia", el nombre del rival...).
    """
    st.subheader("Quintetos más utilizados")
    competition_id, competition_label = _competition_filter(engine, key)
    lineups_df = queries.season_lineups(engine, team_id, season_id, competition_id)

    if lineups_df.empty:
        scope = "" if competition_id is None else f" en {competition_label}"
        st.info(f"Todavía no hay quintetos reconstruidos para {subject}{scope} en esta temporada.")
        return

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
    glossary_expander(_GLOSSARY_KEYS)
