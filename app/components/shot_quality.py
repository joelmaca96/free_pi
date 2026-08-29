"""Pintado de la calidad de tiro (xPPS): fila de métricas, ranking y referencia.

El cálculo vive en `app/analytics/shot_quality.py` y no sabe nada de
Streamlit; aquí solo se decide cómo se enseña. Están las tres pantallas que
lo usan (`partidos_anteriores`, `estado_equipo`, `proximo_rival`) para que la
frase honesta, el aviso de cobertura y el umbral de "muestra insuficiente"
sean literalmente los mismos en las tres — que es media función: una métrica
que dice una cosa en una pantalla y otra en la de al lado deja de usarse.

Se importa con el mismo doble camino que el resto de `app/` (`app.` desde los
tests, sin prefijo desde Streamlit, que corre con `app/` en el `sys.path`).
"""
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import shot_quality
    from app.components import glossary
except ImportError:  # pragma: no cover
    from analytics import shot_quality
    from components import glossary


def quality_metrics(
    summary: dict,
    *,
    reference_xpps: float = None,
    subject: str = "el ataque",
    conceded: bool = False,
) -> None:
    """Fila de tres métricas + la frase honesta debajo.

    Las dos primeras son los dos números que el %TC funde en uno: lo que
    valían los tiros que se generaron (`xPPS`, la decisión) y los puntos que
    sacaron (`PPS`, el acierto). La tercera es su diferencia, **ya
    regularizada** — y se sustituye por "muestra insuficiente" por debajo de
    `shot_quality.MIN_SHOTS` tiros en vez de enseñar un número que es ruido.

    Args:
        summary: salida de `shot_quality.summarize`.
        reference_xpps: xPPS con el que comparar la generación (la media de
            temporada del propio equipo, típicamente). `None` para no
            compararla.
        subject: sujeto de la frase ("el ataque", "la defensa", "el rival").
        conceded: `True` en la lectura defensiva (los tiros son los del
            rival). Cambia las etiquetas y los verbos: una defensa no genera
            xPPS ni saca PPS, los CONCEDE y los encaja.
    """
    if summary["shots"] == 0:
        st.caption(shot_quality.verdict(summary))
        return

    xpps_label = "xPPS concedido" if conceded else "xPPS generado"
    pps_label = "PPS encajado" if conceded else "PPS real"
    diff_label = "Acierto del rival sobre lo esperado" if conceded else "Acierto sobre lo esperado"

    # `help=` en las tres: xPPS y PPS se diferencian en una letra y significan
    # cosas opuestas (la decisión contra el acierto), así que la etiqueta sola
    # no basta. El texto sale del glosario, para que diga lo mismo aquí que en
    # la cabecera de la tabla de abajo y que en las otras dos pantallas.
    xpps_col, pps_col, diff_col = st.columns(3)
    xpps_col.metric(xpps_label, shot_quality.format_pps(summary["xpps"]), help=glossary.help_text("xpps"))
    pps_col.metric(pps_label, shot_quality.format_pps(summary["pps"]), help=glossary.help_text("pps"))
    if summary["reliable"]:
        diff_col.metric(
            diff_label, shot_quality.format_diff(summary["diff_shrunk"]), help=glossary.help_text("diff_shrunk")
        )
    else:
        diff_col.metric(diff_label, "—", help=glossary.help_text("diff_shrunk"))
        diff_col.caption(f"Muestra insuficiente ({summary['shots']} tiros).")
    st.caption(
        shot_quality.verdict(
            summary, reference_xpps=reference_xpps, subject=subject, conceded=conceded
        )
    )


def player_quality_table(ranking: pd.DataFrame) -> None:
    """Ranking de plantilla por calidad de tiro: quién ELIGE bien y quién ACIERTA.

    Son dos habilidades distintas y hoy se mezclan en una sola columna de %
    (§2.2 del documento): un jugador puede generar tiros excelentes y estar
    en una mala racha, y otro sostener un %TC alto a base de acertar tiros
    que no valen nada. Por eso van en dos columnas y no en una.

    `diff_shrunk` sale en blanco —no a cero— para quien no llega a
    `MIN_SHOTS`: es la única forma honesta de decir "todavía no se sabe".

    Args:
        ranking: salida de `shot_quality.summarize_by(..., ["player_id"],
            extra=["player_name"])`.
    """
    if ranking.empty:
        st.info("Sin tiros localizados y clasificados por zona para esta plantilla.")
        return

    table = ranking.copy()
    table.loc[~table["reliable"], "diff_shrunk"] = float("nan")
    table = table.sort_values("xpps", ascending=False)
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_order=["player_name", "shots", "xpps", "pps", "diff_shrunk", "fg_pct"],
        column_config={
            "player_name": st.column_config.TextColumn("Jugador"),
            "shots": st.column_config.NumberColumn("Tiros", help=glossary.help_text("shots")),
            "xpps": st.column_config.NumberColumn(
                "xPPS", format="%.2f", help="Lo que valen sus tiros para la liga. Mide la ELECCIÓN."
            ),
            "pps": st.column_config.NumberColumn("PPS", format="%.2f", help="Puntos reales por tiro."),
            "diff_shrunk": st.column_config.NumberColumn(
                "PPS − xPPS",
                format="%.2f",
                help=(
                    "Acierto por encima de lo que valían sus tiros, ya regularizado hacia 0 "
                    f"(n/(n+{shot_quality.SHRINK_K})). En blanco por debajo de "
                    f"{shot_quality.MIN_SHOTS} tiros: muestra insuficiente."
                ),
            ),
            "fg_pct": st.column_config.NumberColumn(
                "% acierto", format="%.1f", help=glossary.help_text("fg_pct")
            ),
            "player_id": None,
            "made": None,
            "diff": None,
            "reliable": None,
        },
    )
    st.caption(
        "Ordenado por xPPS (quién elige mejor tiro); pulsa en la cabecera de **PPS − xPPS** para "
        "ver el otro ranking, el de quién acierta por encima de lo que valen sus tiros. Son dos "
        "cosas distintas y hoy se mezclan en la columna de %."
    )


def league_reference_expander(baseline: pd.DataFrame, *, season_label: str = "") -> None:
    """La tabla de referencia de la liga, plegada: qué vale un tiro desde cada zona.

    Es, por sí sola, un argumento de vestuario —un triple de esquina vale
    prácticamente lo mismo que un tiro de pintura y un 50% más que una media
    distancia—, y viene de los datos de ESTA liga y ESTA temporada, no de un
    artículo de la NBA. Va plegada porque se consulta una vez y se recuerda,
    no se mira cada día.
    """
    table = shot_quality.league_zone_table(baseline)
    if table.empty:
        return
    scope = f" · {season_label}" if season_label else ""
    with st.expander(f"Qué vale un tiro en esta liga, por zona{scope}"):
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                "zone_label": st.column_config.TextColumn("Zona", help=glossary.help_text("zone_label")),
                "shot_value": st.column_config.NumberColumn(
                    "Vale", help="Puntos que vale una canasta desde esa zona: 2 o 3."
                ),
                "league_shots": st.column_config.NumberColumn(
                    "Tiros de la liga", help="Tiros intentados desde esa zona por TODOS los equipos."
                ),
                "league_fg_pct": st.column_config.NumberColumn(
                    "FG% liga", format="%.1f", help="Acierto medio de la liga desde esa zona."
                ),
                "league_pps": st.column_config.NumberColumn(
                    "PPS liga", format="%.2f",
                    help="Puntos por tiro que saca la liga desde esa zona. Es la referencia del xPPS.",
                ),
            },
        )
        st.caption(
            "Todos los equipos de todas las competiciones de la temporada. La referencia con la "
            "que se calcula el xPPS se separa además por competición (ACB y Euroliga no tienen el "
            "mismo nivel de tiro); una celda con menos de "
            f"{shot_quality.MIN_BASELINE_SHOTS} tiros —Copa del Rey y Supercopa casi enteras— usa "
            "esta referencia agrupada en vez de inventarse una propia. El valor del tiro (2 o 3) "
            "no está guardado en la base de datos: se deriva de la zona."
        )


def quality_caveat(coverage: dict) -> None:
    """Pie de sección: qué tiros se han quedado fuera y qué NO mide esta métrica."""
    st.caption(shot_quality.coverage_caption(coverage))
    st.caption(shot_quality.QUALITY_CAVEAT)
