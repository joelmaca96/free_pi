"""Pintado de similitud de jugadores (propuesta 11): controles, resultados y barras de percentil.

El cálculo vive en `app/analytics/similarity.py`, que no sabe nada de
Streamlit; aquí solo se decide cómo se enseña. Dos consumidores comparten
estas piezas (§6 del documento):

- `components/player_dialog.py`: un bloque compacto "se parece a", sin
  controles, con los valores por defecto de la propuesta (estilo, 500
  minutos, top 5) — es donde el usuario ya está mirando a un jugador.
- `screens/similitud.py`: el buscador completo para fichajes, con filtros,
  pesos ajustables y las barras de percentil comparadas.

**Barras de percentil superpuestas, no radar** (§2 ofrece las dos opciones):
Altair no tiene un mark polar nativo y todo el resto de la interfaz ya es
Altair — un radar exigiría una librería nueva o SVG a mano solo para esta
pantalla. Las barras dicen exactamente lo mismo (dónde se parecen y dónde
no) y se leen mejor con 15 dimensiones que un radar, que se satura pasadas
8-10 puntas.
"""
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import similarity as similarity_engine
    from app.components import glossary
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import similarity as similarity_engine
    from components import glossary
    from data import queries, queries_assistant

_ACCENT = "#008300"
_ACCENT_MUTED = "#a8c9a8"

#: Igual TTL que `data/queries.py` (§6 del documento: "cacheado con
#: @st.cache_data — se recalcula entero en menos de un segundo para 280
#: jugadores"). Vive aquí y no en `analytics/similarity.py` porque ese módulo
#: es lógica pura sin Streamlit (se prueba sin base de datos, ver su
#: docstring) — el cacheo es responsabilidad de quien SÍ sabe de Streamlit.
_VECTORS_TTL = 3600


@st.cache_data(ttl=_VECTORS_TTL, show_spinner=False)
def league_vectors(_engine, season_id: int) -> pd.DataFrame:
    """El vector de perfil de TODOS los jugadores de la liga, listo para `most_similar`.

    Junta las tres consultas de origen (`queries.league_player_index`,
    `queries_assistant.league_player_percentiles`,
    `queries.league_player_zone_volume`, cada una ya cacheada por su cuenta)
    y `similarity_engine.build_player_vectors` — cachear el resultado evita
    rehacer los `merge`/`groupby` en cada rerun de Streamlit (uno por
    interacción con cualquier control de la pantalla).
    """
    index_df = queries.league_player_index(_engine, season_id)
    percentiles_df = queries_assistant.league_player_percentiles(_engine, season_id)
    zone_volume_df = queries.league_player_zone_volume(_engine, season_id)
    return similarity_engine.build_player_vectors(index_df, percentiles_df, zone_volume_df)

#: Etiqueta de cada método (§4: "las dos preguntas reales"), en el orden en
#: que se enseñan en el `st.radio` de `method_control`.
METHOD_LABELS = {"cosine": "Estilo (forma del perfil)", "euclidean": "Nivel (incluye la magnitud)"}


def method_control(key: str, *, default: str = "cosine") -> str:
    """El interruptor "parecido en estilo" / "parecido en nivel" de §4."""
    options = list(METHOD_LABELS)
    choice = st.radio(
        "¿Parecido en qué?",
        options=options,
        index=options.index(default),
        format_func=lambda m: METHOD_LABELS[m],
        key=key,
        horizontal=True,
        help=(
            "Estilo compara la FORMA del perfil sin pesar el nivel absoluto (coseno). Nivel también pesa "
            "cuánto de bueno es cada uno en cada cosa (euclídea) — son dos preguntas distintas y dan "
            "listas distintas."
        ),
    )
    return choice


def weight_controls(key_prefix: str) -> dict:
    """Sliders por grupo de peso (§4: "pesos por dimensión, ajustables"), en un desplegable.

    Por grupo (`similarity_engine.WEIGHT_GROUPS`) y no por las 15 dimensiones
    sueltas: ocho controles caben en un panel sin abrumar, quince no.
    """
    weights = {}
    with st.expander("Ajustar qué pesa más (opcional)"):
        st.caption(
            "1,0 es el peso normal para todos. Súbelo para que ese bloque pese más al buscar parecidos, "
            "bájalo a 0 para ignorarlo del todo — por ejemplo, buscar un sustituto para un tirador sin que "
            "el rebote pese lo mismo."
        )
        cols = st.columns(2)
        for i, (group_key, label) in enumerate(similarity_engine.WEIGHT_GROUPS.items()):
            with cols[i % 2]:
                weights[group_key] = st.slider(label, 0.0, 3.0, 1.0, 0.25, key=f"{key_prefix}_weight_{group_key}")
    return weights


@st.cache_data(ttl=_VECTORS_TTL, show_spinner=False)
def competition_labels(_engine) -> dict:
    """`{competition_id: nombre}`, para mostrar la competición de cada resultado."""
    competitions = queries.list_competitions(_engine)
    return dict(zip(competitions["id"], competitions["name"])) if not competitions.empty else {}


def build_result_rows(
    target_row: pd.Series, results: pd.DataFrame, labels: dict, *, group_weights: Optional[dict] = None
) -> list:
    """`most_similar(...)` -> lista de dicts listos para `results_list`/`result_row`.

    Junta el score que ya trae `results` con la explicación de
    `similarity_engine.explain_similarity` (§4: "sale gratis del propio
    cálculo") — es la MISMA transformación que hace la herramienta del
    asistente (`assistant/tools/similarity.py::_result_row`), duplicada a
    propósito y no importada desde ahí: ese módulo se prueba sin Streamlit
    y este necesita `st.cache_data` (ver el docstring de `league_vectors`).
    """
    rows = []
    for _, row in results.iterrows():
        explanation = similarity_engine.explain_similarity(target_row, row, group_weights=group_weights)
        rows.append({
            "player_id": row["player_id"],
            "name": row["name"],
            "team_id": row["team_id"],
            "team_name": row["team_name"],
            "competition": labels.get(row["competition_id"], row["competition_id"]),
            "gp": int(row["gp_total"]) if pd.notna(row["gp_total"]) else None,
            "minutes_total": float(row["minutes_total"]) if pd.notna(row["minutes_total"]) else None,
            "similarity_score": float(row["similarity_score"]),
            "closest": explanation["closest"],
            "farthest": explanation["farthest"],
        })
    return rows


def _score_badge(score: float) -> str:
    if score >= 90:
        return "🟢"
    if score >= 75:
        return "🟡"
    return "⚪"


def result_row(row: dict, *, on_open=None) -> None:
    """Una fila de resultado: nombre/equipo, la explicación en dos frases y el score.

    Args:
        row: uno de los dicts de `similar` en el resultado de la herramienta
            `similar_players` (o el equivalente construido a mano por la
            página/el diálogo — misma forma: `name, team_name, competition,
            gp, minutes_total, similarity_score, closest, farthest`).
        on_open: si se pasa, se pinta un botón "Ver ficha" que la llama con
            `row['player_id']` (para abrir el modal de jugador desde el
            buscador de la página completa).
    """
    info_col, score_col = st.columns([4, 1])
    with info_col:
        st.markdown(f"**{row['name']}** · {row['team_name']} · {row['competition']}")
        if row["closest"]:
            closest_labels = ", ".join(d["label"].lower() for d in row["closest"])
            st.caption(f"Se parecen en: {closest_labels}.")
        if row["farthest"]:
            farthest = row["farthest"][0]
            st.caption(f"Se alejan sobre todo en: {farthest['label'].lower()} ({farthest['diff_pp']:.0f} pp de percentil).")
        if on_open is not None and st.button("Ver ficha", key=f"similarity_open_{row['player_id']}"):
            on_open(row["player_id"])
    with score_col:
        st.metric(
            "Parecido", f"{_score_badge(row['similarity_score'])} {row['similarity_score']:.0f}",
            help=glossary.help_text("similarity_score"),
        )
        if row.get("gp") is not None:
            st.caption(f"{row['gp']} PJ · {row['minutes_total']:.0f} min" if row.get("minutes_total") is not None else f"{row['gp']} PJ")


def results_list(rows: list, *, on_open=None) -> None:
    """La lista entera de resultados (§2: "salen los 8-10 más parecidos"), o el aviso de que no hay ninguno."""
    if not rows:
        st.info(
            "Nadie cumple los filtros actuales con perfil suficientemente completo para comparar — prueba "
            "bajando el mínimo de minutos o quitando algún filtro."
        )
        return
    for row in rows:
        result_row(row, on_open=on_open)
        st.divider()


def percentile_bars(
    target_row: pd.Series, candidate_row: pd.Series, target_label: str, candidate_label: str
) -> Optional[alt.Chart]:
    """Barras de percentil superpuestas de las 15 dimensiones del perfil (§2, alternativa al radar).

    Args:
        target_row / candidate_row: filas de `similarity_engine.build_player_vectors`
            (necesitan las columnas `<dim_key>_pct`).

    Returns:
        El gráfico, o `None` si ninguna de las 15 dimensiones tiene dato en
        NINGUNO de los dos jugadores (no debería pasar con un `min_minutes`
        razonable, pero se cubre igual).
    """
    records = []
    for dim in similarity_engine.DIMENSIONS:
        col = f"{dim['key']}_pct"
        for label, row in ((target_label, target_row), (candidate_label, candidate_row)):
            value = row.get(col)
            if value is None or pd.isna(value):
                continue
            records.append({"dimension": dim["label"], "jugador": label, "percentil": float(value) * 100.0})
    if not records:
        return None
    df = pd.DataFrame(records)
    order = [dim["label"] for dim in similarity_engine.DIMENSIONS]
    return (
        alt.Chart(df)
        .mark_bar(opacity=0.85)
        .encode(
            x=alt.X("percentil:Q", title="Percentil dentro de su competición", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("dimension:N", title=None, sort=order),
            color=alt.Color(
                "jugador:N", scale=alt.Scale(range=[_ACCENT, _ACCENT_MUTED]), legend=alt.Legend(title=None, orient="top")
            ),
            yOffset="jugador:N",
            tooltip=["dimension", "jugador", alt.Tooltip("percentil:Q", title="Percentil", format=".0f")],
        )
        .properties(height=26 * len(order))
    )


def similarity_caveat() -> None:
    """El aviso permanente de §5: el cálculo ignora el físico y la edad, una temporada, sin ajuste de contexto."""
    st.caption(similarity_engine.SIMILARITY_CAVEAT)
