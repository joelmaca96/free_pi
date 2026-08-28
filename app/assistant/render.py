"""Artefactos: de un `tool_result` al componente que ya usa el resto de la interfaz.

**El modelo no dibuja** (§9.3). Marca qué resultado de herramienta quiere
enseñar (el `artifact` que devuelve la propia herramienta) y esta capa lo
pinta con el componente registrado para ese tipo. El modelo nunca emite HTML,
ni Vega, ni markdown de tabla: elimina de raíz una clase entera de fallos de
formato y reutiliza tal cual `components/court.py::shot_chart` y los
`column_config` ya afinados en las otras cuatro pantallas.

Si un artefacto llega con una forma que esta capa no reconoce, se pinta como
tabla en vez de no pintarse: enseñar el dato en crudo es peor que enseñarlo
bien, pero mucho mejor que perderlo.
"""
from typing import Any, Dict, List, Optional

import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en tools/context.py
    from app.components.court import shot_chart, shot_chart_caption
    from app.data import queries
except ImportError:  # pragma: no cover
    from components.court import shot_chart, shot_chart_caption
    from data import queries

_ACCENT = "#008300"  # verde Baskonia, el mismo acento del resto de la interfaz
_MUTED = "#898781"


def _frame(payload: Any) -> Optional[pd.DataFrame]:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return None


def _render_table(df: pd.DataFrame) -> None:
    st.dataframe(df, hide_index=True, width="content")


def _render_shot_chart(df: pd.DataFrame, engine) -> None:
    """Mapa de tiros con el mismo componente (y el mismo pie) que las otras pantallas."""
    required = {"pos_x", "pos_y", "made"}
    if not required <= set(df.columns):
        _render_table(df)
        return
    zones = queries.court_zones(engine)
    # Sin `use_container_width`: `shot_chart` fija 360x360 a propósito porque
    # el dominio es cuadrado, y estirarlo aplana la cancha (ver su docstring).
    st.altair_chart(shot_chart(df, zones))
    st.caption(shot_chart_caption(df))


def _render_bar(df: pd.DataFrame) -> None:
    """Barra para los dos cortes que producen artefactos de este tipo: zonas y cuartos."""
    if {"zone_label", "volume"} <= set(df.columns):
        value = "share" if "share" in df.columns else "volume"
        title = "% de intentos" if value == "share" else "intentos"
        chart = (
            alt.Chart(df)
            .mark_bar(color=_ACCENT)
            .encode(
                x=alt.X(f"{value}:Q", title=title),
                y=alt.Y("zone_label:N", sort="-x", title=None),
                tooltip=list(df.columns),
            )
            .properties(height=28 * len(df) + 40)
        )
        st.altair_chart(chart, width="stretch")
        return

    if {"quarter", "avg_points_for", "avg_points_against"} <= set(df.columns):
        long = df.melt(
            id_vars="quarter",
            value_vars=["avg_points_for", "avg_points_against"],
            var_name="serie",
            value_name="puntos",
        ).replace({"avg_points_for": "A favor", "avg_points_against": "En contra"})
        chart = (
            alt.Chart(long)
            .mark_bar()
            .encode(
                x=alt.X("quarter:O", title="Cuarto"),
                y=alt.Y("puntos:Q", title="Puntos por partido"),
                xOffset="serie:N",
                color=alt.Color(
                    "serie:N",
                    title=None,
                    scale=alt.Scale(domain=["A favor", "En contra"], range=[_ACCENT, _MUTED]),
                ),
                tooltip=["quarter", "serie", "puntos"],
            )
            .properties(height=260)
        )
        st.altair_chart(chart, width="stretch")
        return

    _render_table(df)


def render_artifact(artifact: Dict[str, Any], engine) -> None:
    """Pinta un artefacto debajo del texto de la respuesta."""
    payload = artifact.get("payload")
    df = _frame(payload)
    if df is None or df.empty:
        return

    title = artifact.get("title")
    if title:
        st.caption(f"**{title}**")

    kind = artifact.get("type")
    if kind == "shot_chart":
        _render_shot_chart(df, engine)
    elif kind == "bar":
        _render_bar(df)
    else:
        _render_table(df)


def render_artifacts(artifacts: List[Dict[str, Any]], engine) -> None:
    """Pinta todos los artefactos de un turno, en el orden en que se pidieron.

    Se descartan los repetidos: dos herramientas pueden devolver el mismo
    corte (un mapa de tiros pedido dos veces con distinto pretexto), y pintar
    dos gráficos idénticos seguidos parece un error de la aplicación.
    """
    seen = set()
    for artifact in artifacts:
        key = (artifact.get("type"), artifact.get("title"), len(artifact.get("payload") or []))
        if key in seen:
            continue
        seen.add(key)
        render_artifact(artifact, engine)


def provenance_line(invocations) -> str:
    """Pie de procedencia de una respuesta: de dónde sale cada cifra (§1).

    Sin esto no es una herramienta de scouting, es un generador de frases. Se
    construye desde `meta` de cada herramienta llamada, no desde el texto del
    modelo — que es justo la parte de la que no hay que fiarse.
    """
    parts = []
    for invocation in invocations:
        if invocation.error:
            continue
        meta = (invocation.result or {}).get("meta") or {}
        piece = meta.get("source", "")
        if meta.get("scope"):
            piece = f"{piece} · {meta['scope']}"
        if piece and piece not in parts:
            parts.append(piece)
    return "Fuente: " + " | ".join(parts) if parts else ""
