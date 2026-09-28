"""Pintado de la predicción del partido (propuesta 16) en "Próximo rival".

El cálculo vive en `app/analytics/prediction.py` y lo sirve (cacheado)
`data/queries_prediction.matchup_prediction`; aquí solo se decide cómo se
enseña: dos números grandes (margen y probabilidad), la descomposición en
puntos —que es lo que se discute en la reunión— y, siempre a la vista, cuánto
acierta el modelo cuando se le prueba hacia atrás. Es un modelo, y la sección
lo dice sin letra pequeña.
"""
import datetime as dt
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import prediction
    from app.components.glossary import glossary_expander, help_text
    from app.data import queries_prediction
except ImportError:  # pragma: no cover
    from analytics import prediction
    from components.glossary import glossary_expander, help_text
    from data import queries_prediction

_ACCENT = "#008300"  # verde Baskonia — a favor
_AGAINST = "#B3261E"  # en contra
#: Por debajo de estos partidos en el ajuste, el rating de un equipo es casi
#: todo "equipo medio" (lo ha encogido el ridge) y se avisa.
_FEW_GAMES = 8


def _comma(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def prediction_section(
    engine,
    *,
    own_team_id: str,
    rival_team_id: str,
    rival_name: str,
    season_id: int,
    match_date: dt.date,
    is_home: bool,
    competition: Optional[str] = None,
) -> None:
    """Sección "Predicción del partido": margen, probabilidad, qué la mueve y cuánto acierta."""
    st.subheader("Predicción del partido")
    result = queries_prediction.matchup_prediction(
        engine, own_team_id, rival_team_id, season_id, match_date, bool(is_home), competition
    )
    if result is None:
        st.info("Sin partidos jugados en la temporada de scouting para ajustar el modelo de predicción.")
        return
    pred, fit, bt = result["prediction"], result["fit"], result["backtest"]

    margin_col, prob_col, rating_col = st.columns(3)
    margin_col.metric(
        "Margen esperado (Baskonia)", prediction.fmt_points(pred.expected_margin), help=help_text("expected_margin")
    )
    prob_col.metric("Probabilidad de victoria", f"{pred.win_probability * 100:.0f}%", help=help_text("win_probability"))
    rating_col.metric(
        "Nivel ajustado: nosotros / ellos",
        f"{prediction.fmt_points(pred.own_rating)} / {prediction.fmt_points(pred.rival_rating)}",
        help=help_text("adjusted_rating"),
    )

    # Qué la mueve: las tres piezas en puntos, que suman el margen.
    st.markdown("**Qué la mueve**\n" + "\n".join(f"- {line}" for line in prediction.explain_components(pred, rival_name)))
    parts = pd.DataFrame(pred.components)
    parts["signo"] = parts["points"].map(lambda v: "A favor" if v >= 0 else "En contra")
    chart = (
        alt.Chart(parts)
        .mark_bar()
        .encode(
            y=alt.Y("label:N", title=None, sort=parts["label"].tolist()),
            x=alt.X("points:Q", title="Puntos de margen"),
            color=alt.Color(
                "signo:N", title=None, scale=alt.Scale(domain=["A favor", "En contra"], range=[_ACCENT, _AGAINST])
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Pieza"),
                alt.Tooltip("points:Q", title="Puntos", format="+.1f"),
                alt.Tooltip("detail:N", title="Detalle"),
            ],
        )
        .properties(height=130)
    )
    st.altair_chart(chart, width="stretch")

    notes = []
    if min(pred.own_games, pred.rival_games) < _FEW_GAMES:
        notes.append(
            f"⚠ Poca muestra: Baskonia {pred.own_games} y {rival_name} {pred.rival_games} partidos en el ajuste — "
            "con tan pocos, el modelo los acerca a un equipo medio."
        )
    if not fit.rest_estimated:
        notes.append("Sin partidos suficientes con descanso distinto para estimar su efecto: el descanso cuenta 0.")
    for note in notes:
        st.caption(note)

    backtest_text = (
        f"Probado hacia atrás en {bt['n']} partidos de la temporada (ajustando cada fecha solo con lo "
        f"anterior): error medio de {_comma(bt['mae'])} puntos y acierta el ganador en el "
        f"{bt['hit_rate'] * 100:.0f}% (decir siempre \"gana el local\" acertaría el {bt['home_hit_rate'] * 100:.0f}%)."
        if bt else "Aún no hay partidos suficientes en la temporada para probar el modelo hacia atrás."
    )
    st.caption(
        f"Es un modelo, no un pronóstico: margen final de {fit.n_games} partidos de la temporada (todas las "
        f"competiciones), descontando la fuerza de los rivales, más la ventaja de campo "
        f"({prediction.fmt_points(fit.home_court)}) y {prediction.fmt_points(fit.rest_per_day)} por día de descanso "
        "de diferencia, ambas estimadas de la liga. No sabe de lesiones, bajas ni rotaciones. "
        + backtest_text
    )

    with st.expander("Nivel ajustado de todos los equipos"):
        st.dataframe(
            result["ratings"],
            hide_index=True,
            width="stretch",
            column_order=["team", "rating", "games"],
            column_config={
                "team": st.column_config.TextColumn("Equipo"),
                "rating": st.column_config.NumberColumn("Nivel ajustado", format="%+.1f", help=help_text("adjusted_rating")),
                "games": st.column_config.NumberColumn("Partidos"),
            },
        )
    glossary_expander(["expected_margin", "win_probability", "adjusted_rating", "prediction_backtest"])
