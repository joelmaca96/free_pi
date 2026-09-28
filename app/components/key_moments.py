"""Pestaña "Momentos clave" de "Partidos anteriores" (propuesta 18, hoja de ruta A4).

`doc/features/propuestas/18_momentos_clave.md`. El cálculo vive en
`app/analytics/win_probability.py` y las consultas en
`app/data/queries_win_probability.py`; aquí solo se decide cómo se enseña.
Cuatro piezas, de arriba abajo, en el orden en que un entrenador las lee:

1. **La curva de probabilidad de victoria** del Baskonia, con la raya del 50%
   y los cinco momentos clave sombreados y numerados sobre el mismo eje. Se
   pinta como escalera (igual que el margen del gráfico de rotaciones): entre
   dos eventos el marcador no cambia, y la probabilidad solo se mueve con el
   reloj.
2. **Los cinco momentos que decidieron el partido**, ordenados por cuánto
   movieron la probabilidad (no por el tamaño del parcial), con el quinteto
   de cada equipo en pista.
3. **WPA por quinteto**: cuánta probabilidad ganó o perdió cada cinco
   mientras estuvo en pista, nuestro y del rival.
4. **La lista de clips** en CSV (la exportación de vídeo aparcada en el
   índice de propuestas): cuarto + reloj de inicio y fin de cada momento.
"""
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import win_probability as wp
    from app.components.glossary import glossary_expander, help_text
    from app.data import queries_win_probability
except ImportError:  # pragma: no cover
    from analytics import win_probability as wp
    from components.glossary import glossary_expander, help_text
    from data import queries_win_probability

_POS = "#008300"   # por encima del 50% (verde Baskonia, mismo que `rotation_chart`)
_NEG = "#c0392b"   # por debajo del 50%
_INK = "#3f3d38"   # la línea de probabilidad
_GRID = "#c9c7bf"  # rayas de cuarto y del 50%
_BAND = "#b5651d"  # momentos clave: ámbar, ni "bueno" ni "malo" por sí mismos


def _quarter_rules(end_minutes: float) -> pd.DataFrame:
    marks = [m for m in (10.0, 20.0, 30.0) if m < end_minutes]
    minute = 40.0
    while minute < end_minutes:
        marks.append(minute)
        minute += 5.0
    return pd.DataFrame({"minute": marks})


def wp_chart(curve: pd.DataFrame, moments: pd.DataFrame, *, team_label: str = "Baskonia") -> alt.LayerChart:
    """Curva de probabilidad de victoria (0-100%) con el 50% y los momentos clave.

    Args:
        curve: `win_probability.game_wp_curve` (desde el equipo propio).
        moments: `win_probability.key_moments` (se sombrean y numeran).
    """
    data = curve.assign(
        minute=curve["seconds"] / 60.0,
        wp_pct=100.0 * curve["wp"],
        reloj=[" ".join(wp.period_clock(s, at_end=True)) for s in curve["seconds"]],
        marcador=curve["score_for"].astype(int).astype(str) + "-" + curve["score_against"].astype(int).astype(str),
    )
    data["above"] = data["wp_pct"].clip(lower=50.0)
    data["below"] = data["wp_pct"].clip(upper=50.0)
    data["fifty"] = 50.0
    end_minutes = float(data["minute"].max()) if not data.empty else 40.0
    x_scale = alt.Scale(domain=[0.0, max(end_minutes, 40.0)], nice=False)
    y_scale = alt.Scale(domain=[0.0, 100.0])
    # Mismo título en TODAS las capas con Y: Altair une los ejes de un
    # `layer` y, con títulos distintos, los concatena ("above, below, ...").
    y_title = f"Prob. de victoria {team_label} (%)"
    x = alt.X("minute:Q", title="Minuto de partido", scale=x_scale)

    layers = []
    if moments is not None and not moments.empty:
        bands = moments.assign(
            minute_start=moments["start_seconds"] / 60.0,
            minute_end=moments["end_seconds"] / 60.0,
            rank_label=moments["rank"].astype(int).astype(str),
            wpa_pp=100.0 * moments["wpa"],
        )
        layers.append(
            alt.Chart(bands)
            .mark_rect(opacity=0.18, color=_BAND)
            .encode(
                x=alt.X("minute_start:Q", scale=x_scale, title="Minuto de partido"),
                x2="minute_end:Q",
                tooltip=[
                    alt.Tooltip("rank_label:N", title="Momento"),
                    alt.Tooltip("label:N", title="Qué pasó"),
                    alt.Tooltip("wpa_pp:Q", title="Cambio (pp)", format="+.0f"),
                ],
            )
        )
        layers.append(
            alt.Chart(bands)
            .mark_text(dy=-4, fontSize=12, fontWeight="bold", color=_BAND, baseline="bottom")
            .encode(
                x=alt.X("minute_start:Q", scale=x_scale),
                # `datum` y no un campo: así esta capa no aporta eje propio y
                # el eje Y (compartido por todas las capas) es el de la curva.
                y=alt.datum(100.0, scale=y_scale),
                text="rank_label:N",
            )
        )

    layers += [
        alt.Chart(data)
        .mark_area(interpolate="step-after", opacity=0.22, color=_POS)
        .encode(x=x, y=alt.Y("above:Q", scale=y_scale, title=y_title), y2="fifty:Q"),
        alt.Chart(data)
        .mark_area(interpolate="step-after", opacity=0.22, color=_NEG)
        .encode(x=x, y=alt.Y("below:Q", scale=y_scale, title=y_title), y2="fifty:Q"),
        alt.Chart(pd.DataFrame({"y": [50.0]}))
        .mark_rule(color=_GRID, strokeDash=[4, 3])
        .encode(y=alt.Y("y:Q", scale=y_scale, title=y_title)),
        alt.Chart(data)
        .mark_line(interpolate="step-after", color=_INK, strokeWidth=2)
        .encode(
            x=x,
            y=alt.Y("wp_pct:Q", scale=y_scale, title=y_title),
            tooltip=[
                alt.Tooltip("reloj:N", title="Reloj"),
                alt.Tooltip("marcador:N", title="Marcador"),
                alt.Tooltip("wp_pct:Q", title="Prob. victoria (%)", format=".0f"),
            ],
        ),
    ]
    rules = _quarter_rules(end_minutes)
    if not rules.empty:
        layers.append(alt.Chart(rules).mark_rule(color=_GRID, strokeWidth=1).encode(x=alt.X("minute:Q", scale=x_scale)))
    return alt.layer(*layers).properties(height=300)


def _moments_display(moments: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "rank": moments["rank"].astype(int),
        "inicio": moments["quarter_start"] + " " + moments["clock_start"],
        "fin": moments["quarter_end"] + " " + moments["clock_end"],
        "parcial": moments["points_for"].astype(int).astype(str) + "-" + moments["points_against"].astype(int).astype(str),
        "marcador": moments["score_before"] + " a " + moments["score_after"],
        "wp_before": 100.0 * moments["wp_before"],
        "wp_after": 100.0 * moments["wp_after"],
        "wpa": 100.0 * moments["wpa"],
        "lineup_own": moments["lineup_own"].fillna("—"),
        "lineup_rival": moments["lineup_rival"].fillna("—"),
    })


def _lineups_table(df: pd.DataFrame, key: str) -> None:
    if df.empty:
        st.caption("Sin tramos de quinteto en este partido.")
        return
    shown = df.assign(wpa_pp=100.0 * df["wpa"], plus_minus=df["points_for"] - df["points_against"])
    st.dataframe(
        shown,
        hide_index=True,
        width="stretch",
        key=key,
        column_order=["lineup", "minutes", "plus_minus", "wpa_pp"],
        column_config={
            "lineup": st.column_config.TextColumn("Quinteto", width="large"),
            "minutes": st.column_config.NumberColumn("Min", format="%.1f"),
            "plus_minus": st.column_config.NumberColumn("+/-", format="%+d", help=help_text("lineup_plus_minus")),
            "wpa_pp": st.column_config.NumberColumn("WPA (pp)", format="%+.1f", help=help_text("wpa_lineup")),
        },
    )


def key_moments_tab(
    engine,
    game_id: str,
    team_id: str,
    rival_name: str,
    *,
    team_label: str = "Baskonia",
    file_stem: Optional[str] = None,
) -> Optional[dict]:
    """Pinta la pestaña entera para un partido. Devuelve el `dict` de
    `queries_win_probability.game_key_moments` (o `None` si no hay nada que pintar)."""
    window_min = st.slider(
        "Duración máxima de un momento (min)", 0.5, 4.0, wp.KEY_MOMENT_WINDOW_S / 60.0, 0.5,
        key=f"km_window_{game_id}",
        help="Un momento clave es una ventana de como mucho esta duración en la que cambia el marcador.",
    )
    with st.spinner("Calculando la probabilidad de victoria (el modelo se ajusta con toda la liga)..."):
        bundle = queries_win_probability.game_key_moments(
            engine, game_id, team_id, window_s=window_min * 60.0, team_label=team_label
        )
    if bundle is None:
        st.info("El partido no está en la base de datos o el equipo propio no lo juega.")
        return None
    curve, moments = bundle["curve"], bundle["moments"]
    if curve.empty:
        st.info(
            "Este partido no tiene play-by-play tipado: sin marcador con reloj no hay curva de "
            "probabilidad ni momentos clave."
        )
        return None

    st.altair_chart(wp_chart(curve, moments, team_label=team_label), width="stretch")
    st.caption(
        f"Probabilidad de que gane el {team_label} en cada momento, según margen, tiempo restante y "
        f"campo · franjas ámbar numeradas = los momentos clave de la tabla · modelo: "
        f"{bundle['model'].description}."
    )

    st.markdown("**Los momentos que decidieron el partido**")
    if moments.empty:
        st.info("Ninguna ventana movió la probabilidad de victoria de forma apreciable.")
    else:
        st.dataframe(
            _moments_display(moments),
            hide_index=True,
            width="stretch",
            key=f"km_table_{game_id}",
            # El cambio de probabilidad —lo que ordena la tabla— antes que el
            # resto: con los quintetos, la tabla no cabe entera sin desplazarse.
            column_order=[
                "rank", "inicio", "fin", "parcial", "wpa", "wp_before", "wp_after", "marcador",
                "lineup_own", "lineup_rival",
            ],
            column_config={
                "rank": st.column_config.NumberColumn("#", format="%d"),
                "inicio": st.column_config.TextColumn("Inicio"),
                "fin": st.column_config.TextColumn("Fin"),
                "parcial": st.column_config.TextColumn("Parcial", help="Puntos del Baskonia-puntos del rival dentro de la ventana."),
                "marcador": st.column_config.TextColumn("Marcador"),
                "wp_before": st.column_config.NumberColumn("Prob. antes (%)", format="%.0f", help=help_text("live_win_probability")),
                "wp_after": st.column_config.NumberColumn("Prob. después (%)", format="%.0f", help=help_text("live_win_probability")),
                "wpa": st.column_config.NumberColumn("Cambio (pp)", format="%+.0f", help=help_text("wpa")),
                "lineup_own": st.column_config.TextColumn(f"Quinteto {team_label}", width="large"),
                "lineup_rival": st.column_config.TextColumn(f"Quinteto {rival_name}", width="large"),
            },
        )
        st.caption(
            "Ordenados por cuánto movieron la probabilidad de ganar, no por el tamaño del parcial: "
            "un 8-0 en el minuto 3 pesa mucho menos que un 5-0 en el 38. ⚠ Los tiros no tienen reloj "
            "en la fuente: el salto del marcador se ve en el siguiente evento tipado, así que la "
            "canasta real cae un poco antes del inicio que marca la tabla."
        )

    st.markdown(f"**WPA por quinteto de {team_label}** — probabilidad de victoria ganada o perdida con cada cinco en pista")
    # Una debajo de otra y no en dos columnas: cinco nombres por fila no caben
    # en media pantalla. El rival, plegado (mismo criterio que sus rotaciones).
    _lineups_table(bundle["lineups_own"], key=f"km_own_{game_id}")
    with st.expander(f"WPA por quinteto de {rival_name}"):
        _lineups_table(bundle["lineups_rival"], key=f"km_rival_{game_id}")
    st.caption(
        "La suma de todos los quintetos de un equipo es exactamente su probabilidad final (0% o 100%) "
        "menos la de salida: el reparto dice QUIÉN estaba en pista mientras el partido se ganaba o se "
        "perdía, no quién jugó mejor."
    )

    clips = bundle["clips"]
    if not clips.empty:
        st.download_button(
            "Descargar lista de clips (CSV)",
            # BOM UTF-8: Excel abre bien los acentos de las descripciones.
            data=clips.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"clips_{file_stem or game_id}.csv".replace(" ", "_"),
            mime="text/csv",
            key=f"km_clips_{game_id}",
            help=(
                "Cuarto y reloj de inicio y fin de cada momento clave (con unos segundos de margen "
                "antes y después), en el formato del acta, para buscarlos en el vídeo."
            ),
        )
    glossary_expander(["live_win_probability", "wpa", "wpa_lineup", "lineup_plus_minus"])
    return bundle
