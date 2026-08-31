"""Pintado de los umbrales de victoria (propuesta 09): panel de objetivos y su comprobación.

El cálculo vive en `app/analytics/win_thresholds.py` y no sabe nada de
Streamlit; aquí solo se decide cómo se enseña. Tres piezas, las tres del §2
del documento:

- `objectives_panel`: el panel de tarjetas en `proximo_rival` (§2a), con el
  ajuste al rival ya aplicado si se le pasan los promedios de concesión.
- `objectives_checklist`: la fila de cumplido/no cumplido de `partidos_
  anteriores` (§2b) — cierra el ciclo "se fija, se juega, se revisa".
- `factor_correlations_table` / `logistic_importance_expander`: la tabla de
  §1 (qué batalla pesa más) y los pesos del modelo v2, en un desplegable —
  se consultan una vez y se recuerdan, no se miran cada día (mismo criterio
  que `components/shot_quality.py::league_reference_expander`).
"""
from typing import Optional

import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import win_thresholds
    from app.components import glossary
except ImportError:  # pragma: no cover
    from analytics import win_thresholds
    from components import glossary


def _capitalized(label: str) -> str:
    """Mayúscula solo en la primera letra — `.capitalize()` pone en minúscula el RESTO
    de la cadena, y "eFG%" (o cualquier otra sigla de una etiqueta) no sobrevive a eso."""
    return f"{label[0].upper()}{label[1:]}" if label else label


def objectives_panel(
    cards: list,
    *,
    rival_avg: Optional[dict] = None,
    league_avg: Optional[dict] = None,
    rival_name: Optional[str] = None,
    competition_label: Optional[str] = None,
) -> None:
    """El panel de tarjetas de §2a: un número grande por objetivo, con su historial debajo.

    Args:
        cards: salida de `win_thresholds.league_objectives`.
        rival_avg: salida de `win_thresholds.rival_concession_averages`, para
            desplazar cada tarjeta al perfil de un rival concreto (§4, paso
            2). `None` para enseñar el umbral de liga sin ajustar (p.ej. en
            una pantalla que no tiene un rival concreto delante).
        league_avg: salida de `win_thresholds.league_concession_averages`,
            pareja obligatoria de `rival_avg` — sin ella no hay con qué
            comparar el perfil del rival.
        rival_name: nombre del rival, solo para la leyenda del ajuste.
        competition_label: si `cards` se calculó acotado a una sola
            competición (§5: "conviene poder separarlas"), su nombre — para
            que el aviso permanente diga el ámbito correcto en vez de "ACB y
            Euroliga juntas" por defecto. `None` (por defecto) cuando `cards`
            viene de las competiciones juntas.
    """
    if not cards:
        st.info(
            f"Ningún factor separa victorias de derrotas con al menos {win_thresholds.MIN_SIDE_GAMES} "
            "partidos a cada lado esta temporada — muestra insuficiente para fijar objetivos."
        )
        return

    adjusted_cards = cards
    is_adjusted = False
    if rival_avg and league_avg:
        adjusted_cards = [win_thresholds.rival_adjusted_card(card, rival_avg, league_avg) for card in cards]
        is_adjusted = any(card["is_rival_adjusted"] for card in adjusted_cards)

    columns = st.columns(len(adjusted_cards))
    for col, card in zip(columns, adjusted_cards):
        # `help=` sale del glosario: las cuatro claves de `FACTORS` coinciden
        # a propósito con las de `components/glossary.py`, así que la sigla
        # se explica con la MISMA definición que en cualquier otra pantalla.
        col.metric(
            _capitalized(card["label"]), win_thresholds.card_value_text(card), help=glossary.help_text(card["key"])
        )
        col.caption(win_thresholds.card_caption(card))

    if is_adjusted:
        n_games = rival_avg.get("n_games", 0)
        who = f" de {rival_name}" if rival_name else " del rival"
        st.caption(
            f"Objetivo ajustado al perfil{who} esta temporada ({n_games} partidos con avanzadas) — "
            "nunca leído como histórico contra este rival en concreto (§4 del documento). El % de "
            "victorias de cada tarjeta sigue siendo el de TODA la liga cumpliendo el umbral base."
        )
    objectives_caveat(competition_label)


def objectives_caveat(competition_label: Optional[str] = None) -> None:
    """El aviso permanente de §5: correlación no es causa, y eFG% se lo come todo."""
    st.caption(win_thresholds.objectives_caveat_text(competition_label))


def objectives_checklist(game_row: pd.Series, cards: list, *, gano: Optional[bool] = None) -> None:
    """§2b: de los objetivos de liga, cuáles sostuvo el equipo EN ESTE partido.

    Convierte el panel en un ciclo cerrado (se fija, se juega, se revisa) en
    vez de un adorno de prepartido — mismo argumento que el resto de la
    propuesta.

    Args:
        game_row: la fila de `queries.game_factor_rows` del equipo propio en
            el partido que se está revisando.
        cards: `win_thresholds.league_objectives` de la temporada de ese
            partido (sin ajustar a rival: el objetivo que se revisa es el
            que se fijó, el de liga).
        gano: si se sabe, si el equipo ganó ese partido — para la frase final
            ("perdiendo tres de cuatro" pesa más si además se perdió).
    """
    if not cards:
        return
    results = win_thresholds.game_card_results(game_row, cards)
    columns = st.columns(len(results))
    met_count = sum(1 for r in results if r["met"])
    known_count = sum(1 for r in results if r["met"] is not None)
    for col, result in zip(columns, results):
        if result["met"] is None:
            icon, detail = "—", "sin dato"
        else:
            icon = "✅" if result["met"] else "❌"
            detail = f"{win_thresholds.format_pct(result['actual'])} (umbral: {win_thresholds.format_pct(result.get('adjusted_threshold', result.get('display_threshold')))})"
        col.markdown(f"**{icon} {_capitalized(result['label'])}**")
        col.caption(detail)
    if not known_count:
        return
    summary = f"{met_count} de {known_count} objetivos cumplidos esta noche."
    if gano is not None:
        # La lectura que de verdad importa: cumplir los objetivos y perder (o
        # incumplirlos y ganar) es la señal de que algo más decidió el
        # partido — el panel no lo esconde detrás de un recuento neutro.
        if gano and met_count < known_count:
            summary += " Se ganó sin cumplirlos todos: alguna otra cosa decidió el partido."
        elif not gano and met_count == known_count:
            summary += " Se perdió cumpliéndolos todos: alguna otra cosa decidió el partido."
    st.caption(summary)


def factor_correlations_table(df: pd.DataFrame) -> None:
    """La tabla de §1: qué batalla pesa más, con los datos cargados hoy."""
    table = win_thresholds.factor_correlations(df)
    if table.empty:
        return
    with st.expander("Qué batalla pesa más (correlación con ganar)"):
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_order=["label", "correlation", "win_pct_when_favorable", "n_favorable", "n_total"],
            column_config={
                "label": st.column_config.TextColumn("Batalla"),
                "correlation": st.column_config.NumberColumn(
                    "Correlación con ganar", format="%.2f",
                    help="De -1 a 1. Cuanto más cerca de 1, más se gana el partido cuando se gana esta batalla.",
                ),
                "win_pct_when_favorable": st.column_config.NumberColumn(
                    "% victorias si se gana", format="%.1f"
                ),
                "n_favorable": st.column_config.NumberColumn("Partidos ganando esa batalla"),
                "n_total": st.column_config.NumberColumn("Partidos con dato"),
            },
        )
        st.caption(
            "Cada fila es la propia cifra menos la del rival en el mismo partido (la 'batalla'), no el "
            "porcentaje propio a secas — comparado sin más, el acierto propio engaña bastante más que "
            "la diferencia (§1 del documento)."
        )


def logistic_importance_expander(model: Optional[dict]) -> None:
    """v2 de §4: pesos relativos del modelo logístico, en un desplegable."""
    if not model:
        return
    importance = win_thresholds.factor_importance(model)
    with st.expander("Modelo v2: pesos relativos de cada batalla"):
        st.dataframe(
            importance,
            hide_index=True,
            width="stretch",
            column_order=["label", "share_pct", "weight"],
            column_config={
                "label": st.column_config.TextColumn("Batalla"),
                "share_pct": st.column_config.NumberColumn(
                    "% del peso total", format="%.0f",
                    help="Reparte 100% entre las cuatro batallas según el peso absoluto del modelo.",
                ),
                "weight": st.column_config.NumberColumn(
                    "Peso (estandarizado)", format="%.2f",
                    help="Coeficiente de la regresión logística sobre la batalla estandarizada.",
                ),
            },
        )
        st.caption(
            f"Regresión logística sobre {model['n']} partidos-equipo, descenso de gradiente escrito a "
            "mano (sin scikit-learn/statsmodels, ver el docstring del módulo). Da un orden de "
            "importancia, no una receta: sigue siendo correlación, no causa."
        )
