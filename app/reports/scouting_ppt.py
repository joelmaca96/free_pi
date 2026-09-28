"""Dossier de scouting del rival, en un `.pptx` — propuesta
`doc/features/propuestas/03_dossier_scouting_rival.md`.

La pantalla "Próximo rival" (`app/screens/proximo_rival.py`) ya calcula todo
esto para pintarlo en la app; este módulo es puro ensamblado y maquetación
sobre las mismas consultas, para que el cuerpo técnico pueda proyectarlo,
anotarlo y mandarlo por WhatsApp — la reunión del día antes no lleva la
aplicación, lleva un `.pptx` (§1 de la propuesta).

**Alcance v1** (§7 de la propuesta, acotado a propósito para no perder el
esfuerzo en maquetación infinita): seis diapositivas —
portada, identidad con percentiles de liga, calidad de tiro (xPPS) en
texto, jugadores clave, quintetos y rendimiento por cuarto, y claves del
partido. **Sin mapa de tiros** (§4: rasterizar Altair pide una dependencia
nueva o dibujar la pista a mano con formas nativas; se deja para una
iteración siguiente) — la calidad de tiro sí entra, pero como cifras
(`analytics/shot_quality.py::verdict`), no como el mapa de la pantalla.

Mismo patrón de DOS CAPAS que `postgame_ppt.py` (léase su docstring de
módulo para la justificación completa) en los dos sitios donde hay texto que
redactar en vez de solo tabular — jugadores clave y claves del partido:
reglas fijas como suelo, LLM opcional por encima, con una sola llamada para
todos los jugadores/claves de golpe. El botón funciona sin LLM configurado.

Limitaciones conocidas (heredadas de las consultas, no de este módulo — ver
§5 de la propuesta): sin percentil de equipo para pérdidas/rebote ofensivo
(`team_style_percentiles` no las tiene, a diferencia de las de jugador); sin
detalle por cuarto a nivel de JUGADOR para un rival de Euroliga
(`player_game_quarter_stats` es ACB-only); sin foto real para ningún
jugador rival (`ingest/baskonia_web` solo descarga las del Baskonia) — cae
al badge de iniciales, igual que el resto de la interfaz.
"""
import datetime as dt
import io
import json
import logging
import re
from typing import Dict, List, Optional

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

# Mismo patrón de import doble que `postgame_ppt.py` (pytest vs. Streamlit,
# ver `assistant/tools/context.py`).
try:  # pragma: no cover - depende de cómo se arranque el proceso, no de la lógica
    from app.analytics import rotation_patterns, shot_quality, win_thresholds
    from app.assistant.llm import LLMClient, LLMError
    from app.components.branding import CREST_PATH
    from app.data import queries, queries_assistant
    from app.analytics.prediction import summary_sentence as prediction_summary
    from app.data import queries_prediction
    from app.reports import _deck
except ImportError:  # pragma: no cover
    from analytics import rotation_patterns, shot_quality, win_thresholds
    from assistant.llm import LLMClient, LLMError
    from components.branding import CREST_PATH
    from data import queries, queries_assistant
    from analytics.prediction import summary_sentence as prediction_summary
    from data import queries_prediction
    from reports import _deck

logger = logging.getLogger(__name__)

_MAX_HIGHLIGHTS = 4
_MAX_KEYS = 5
_MAX_PLAYERS = 8

# Umbrales de etiquetado de percentil, idénticos a `assistant/tools/team.py::
# _label` (0-1, ver el mismo comentario allí sobre por qué son fijos y no
# criterio del modelo): un percentil por debajo de 0.30 o por encima de 0.70
# es lo bastante extremo para mencionarse en un dossier que se lee en cinco
# minutos antes del partido; entre medias es "normal", no aporta.
_HIGH_PERCENTILE = 0.70
_LOW_PERCENTILE = 0.30

_FALLBACK_KEYS_TEXT = "Sin percentiles ni cifras suficientemente extremas para destacar claves automáticas."


# ================================================================ portada ==


def _build_cover_slide(prs: Presentation, ctx: dict) -> None:
    condicion = "Local" if ctx["is_home"] else "Visitante"
    subtitle = f"{'vs' if ctx['is_home'] else '@'} {ctx['rival_name']} · {ctx['competition']} · {ctx['fecha']}"

    footer_bits = [f"Baskonia como {condicion.lower()}."]
    h2h = ctx["h2h_summary"]
    if h2h is not None:
        footer_bits.append(
            f"Cara a cara histórico: Baskonia {h2h['wins']}–{h2h['losses']} en {h2h['total']} enfrentamientos."
        )
    else:
        footer_bits.append("Sin enfrentamientos previos registrados.")
    if ctx["rival_record"]:
        footer_bits.append(
            "Balance de " + ctx["rival_name"] + " esta temporada: "
            + ", ".join(f"{c} {w}–{l}" for c, w, l in ctx["rival_record"]) + "."
        )
    # Propuesta 16: la predicción del partido en una frase, si se pudo calcular
    # (`ctx.get`: un ctx de test o de una versión anterior no la trae).
    if ctx.get("prediction_sentence"):
        footer_bits.append(ctx["prediction_sentence"])
    if ctx["is_fallback_season"]:
        footer_bits.append(
            f"⚠ {ctx['rival_name']} no ha jugado aún en la temporada actual: todo el scouting "
            f"de este dossier es de {ctx['scouting_season_label']}, su última temporada con datos."
        )

    _deck.add_band_title_slide(
        prs,
        title=f"Dossier de scouting — {ctx['rival_name']}",
        subtitle=subtitle,
        footer=" ".join(footer_bits),
        crest_path=CREST_PATH,
    )


# ============================================================== identidad ==


def _build_identity_slide(prs: Presentation, style_df: pd.DataFrame, rival_name: str) -> None:
    if style_df.empty:
        _deck.add_bullets_slide(
            prs,
            title=f"Identidad de {rival_name}",
            bullets=[],
            fallback_text=(
                f"{rival_name} no llega a los 5 partidos en esta temporada con estadísticas avanzadas: "
                "sin muestra suficiente para calcular percentiles de liga todavía."
            ),
        )
        return

    row = style_df.iloc[0]  # más partidos jugados primero (`team_style_row` ya lo ordena así)
    league_n = row.get("league_teams")
    metrics = [
        ("Ritmo", row["pace"], row.get("pace_pct"), "posesiones/partido"),
        ("ORtg", row["ortg"], row.get("ortg_pct"), "puntos por 100 posesiones anotados"),
        ("DRtg", row["drtg"], row.get("drtg_pct"), "puntos por 100 posesiones concedidos (alto = defensa floja)"),
        ("Net rating", row["net_rating"], row.get("net_rating_pct"), ""),
        ("eFG%", row["efg_pct"], row.get("efg_pct_pct"), "acierto de tiro de campo ajustado por triples"),
        ("TS%", row["ts_pct"], row.get("ts_pct_pct"), "acierto de tiro real, incluye libres"),
    ]
    columns = ["Métrica", "Valor", "Percentil de liga", "Lectura"]
    rows = []
    for label, value, pct, note in metrics:
        if pd.isna(value):
            rows.append([label, "—", "—", note])
            continue
        if pct is None or pd.isna(pct):
            pct_cell = "sin muestra"
        else:
            pct_cell = f"{int(round(pct * 100))} de {int(league_n)}" if league_n else f"{int(round(pct * 100))}"
        rows.append([label, f"{value:.1f}", pct_cell, note])

    subtitle = f"{row['competition']} · {int(row['gp'])} partidos jugados"
    if len(style_df) > 1:
        subtitle += f" (hay también datos de {', '.join(style_df['competition'].iloc[1:])})"
    footer = (
        "Percentil sobre PERCENT_RANK sin ponderar por calendario. DRtg se calcula ya invertido "
        "(percentil alto = mejor defensa)."
    )
    if row["competition"] == "Euroliga":
        footer = "ORtg/DRtg/ritmo de Euroliga son estimación propia (Dean Oliver), no dato oficial. " + footer

    _deck.add_table_slide(
        prs,
        title=f"Identidad de {rival_name}",
        subtitle=subtitle,
        columns=columns,
        rows=rows,
        footer=footer,
        col_widths=[2.2, 1.6, 2.6, 5.7],
    )


# ============================================================ calidad tiro ==


def _shot_quality_bullets(
    attack_valued: pd.DataFrame, defense_valued: pd.DataFrame, coverage: dict, rival_name: str
) -> List[str]:
    """Verdictos de ataque/defensa (`shot_quality.verdict`) + la zona más castigable, en texto.

    Reutiliza `analytics/shot_quality.py` tal cual lo usa
    `app/screens/proximo_rival.py` — la única diferencia es que aquí el
    resultado es una lista de frases para una diapositiva, no un `st.metric`.
    Recibe los tiros ya valorados (`shot_quality.with_expected`) en vez de
    volver a consultarlos: `generate_scouting_ppt` los necesita también para
    "claves del partido", así que se calculan una sola vez.
    """
    if attack_valued.empty and defense_valued.empty:
        return [f"Sin tiros de {rival_name} localizados y clasificados por zona en esta temporada."]

    bullets = [
        shot_quality.verdict(shot_quality.summarize(attack_valued), subject=f"El ataque de {rival_name}"),
        shot_quality.verdict(
            shot_quality.summarize(defense_valued), subject=f"La defensa de {rival_name}", conceded=True
        ),
    ]

    # La zona donde MÁS se sale de lo esperado en cada lado — es la lectura
    # accionable ("aquí aciertan más de lo que deberían", "aquí conceden
    # tiros mejores de lo que deberían"), no solo el volumen. En los dos
    # casos interesa el volumen más ALTO de `diff_pps`: en ataque es su
    # amenaza real, en lo que concede es la zona donde su defensa flaquea
    # (ver `doc/features/propuestas/08_donde_castigar_al_rival.md`).
    for valued, label in ((attack_valued, "ataque"), (defense_valued, "que concede")):
        zones = shot_quality.zone_profile(valued)
        zones = zones.loc[zones["volume"] >= 20]  # bajo esto, la diferencia por zona es ruido
        if zones.empty:
            continue
        top = zones.loc[zones["diff_pps"].idxmax()]
        sign = "+" if top["diff_pps"] >= 0 else ""
        bullets.append(
            f"Zona a vigilar en {label}: {top['zone_label']} — {shot_quality.format_pps(top['pps'])} PPS "
            f"({sign}{shot_quality.format_pps(top['diff_pps'])} sobre lo esperado en {int(top['volume'])} tiros)."
        )

    if coverage["total"] and coverage["pct"] < 70:
        bullets.append(
            f"Cobertura de tiros localizados y clasificados: {coverage['pct']:.0f}% — léelo con margen."
        )
    return bullets


#: Sufijo de las sub-zonas de ala partidas por el arco de triple
#: (`shot_quality.zone_profile`: "Ala izq. (2)"/"Ala izq. (3)").
_WING_SUFFIX_RE = re.compile(r"\s\(\d\)$")


def _coarsen_zone_profile(zone_df: pd.DataFrame) -> pd.DataFrame:
    """Colapsa "Ala izq. (2)"/"Ala izq. (3)" (y su pareja "der.") en una sola "Ala izq."/"Ala der.".

    El mapa de tiro del dossier (§4 de la propuesta: rectángulos NATIVOS de
    pptx, sin dependencia nueva) necesita una geometría por zona, y
    `court_zones` solo tiene una caja rectangular real para "Ala izq."/"Ala
    der." completas (ids 2/3) — la partición en 2/3 puntos que pinta
    `components/court.py::_wing_area_layers` es un ARCO calculado con Altair
    en el momento, no un rectángulo de la tabla (sus filas 14-17 son puntos
    degenerados, `x_min == x_max`, solo para anclar una etiqueta). Reproducir
    ese arco a mano en pptx no compensa para una diapositiva que se lee en un
    minuto — se combinan los tiros/aciertos/puntos de las dos sub-zonas y se
    recalcula el PPS conjunto sobre el rectángulo entero, una simplificación
    de v1 explícita (igual criterio que acotar el resto de la propuesta 03).

    Args:
        zone_df: salida de `shot_quality.zone_profile`.

    Returns:
        `zone_label, volume, made, fg_pct, pps, league_pps, diff_pps` — mismas
        columnas que hacen falta para pintar el mapa, con las sub-zonas de ala
        ya fundidas. Vacío si `zone_df` lo está.
    """
    columns = ["zone_label", "volume", "made", "fg_pct", "pps", "league_pps", "diff_pps"]
    if zone_df.empty:
        return pd.DataFrame(columns=columns)

    df = zone_df.assign(
        _base_label=zone_df["zone_label"].str.replace(_WING_SUFFIX_RE, "", regex=True),
        _points=zone_df["pps"] * zone_df["volume"],
        _league_points=zone_df["league_pps"] * zone_df["volume"],
    )
    grouped = df.groupby("_base_label", as_index=False).agg(
        volume=("volume", "sum"), made=("made", "sum"),
        _points=("_points", "sum"), _league_points=("_league_points", "sum"),
    )
    grouped["fg_pct"] = 100.0 * grouped["made"] / grouped["volume"]
    grouped["pps"] = grouped["_points"] / grouped["volume"]
    grouped["league_pps"] = grouped["_league_points"] / grouped["volume"]
    grouped["diff_pps"] = grouped["pps"] - grouped["league_pps"]
    return grouped.rename(columns={"_base_label": "zone_label"})[columns]


#: Diferencia de PPS (contra la media de la liga en esa zona) donde satura el
#: color del mapa — verificado sobre `data/baskonia.db` (temporada 2025-2026,
#: perfil del Baskonia): el rango real de `diff_pps` por zona en una
#: temporada entera va de -0,11 a +0,11, así que 0,15 satura solo los casos
#: más extremos sin aplanar todo el mapa a un solo tono.
_ZONE_DIFF_CAP = 0.15
_ZONE_LOW = (0xC6, 0x28, 0x28)     # rojo — peor que la liga (mismo tono que `components/court.py`)
_ZONE_NEUTRAL = (0xF0, 0xED, 0xE4)  # "igual que la liga"
_ZONE_HIGH = (0x00, 0x83, 0x00)    # verde Baskonia — mejor que la liga
_ZONE_EMPTY = RGBColor(0xE6, 0xE4, 0xDC)  # sin tiros en esa zona


def _lerp_rgb(a: tuple, b: tuple, t: float) -> RGBColor:
    t = max(0.0, min(1.0, t))
    return RGBColor(*(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)))


def _zone_fill_color(diff_pps) -> RGBColor:
    """Rojo (peor que la liga) → neutro → verde (mejor), saturado a `_ZONE_DIFF_CAP` PPS."""
    if diff_pps is None or pd.isna(diff_pps):
        return _ZONE_EMPTY
    weight = min(abs(float(diff_pps)) / _ZONE_DIFF_CAP, 1.0)
    base = _ZONE_HIGH if diff_pps >= 0 else _ZONE_LOW
    return _lerp_rgb(_ZONE_NEUTRAL, base, weight)


def _draw_shot_zone_map(
    slide, zone_geometry: dict, zone_df: pd.DataFrame, *, left: float, top: float, width: float, height: float
) -> None:
    """Dibuja un medio campo de rectángulos NATIVOS de pptx, coloreados por `diff_pps`.

    Args:
        zone_geometry: `zone_label -> (x_min, x_max, y_min, y_max)` en el
            sistema de coordenadas de `court_zones` (x: 0-500, y: 0-460, y=0
            lejos del aro). Solo se pintan las zonas presentes aquí Y en
            `zone_df` — una zona sin geometría (p.ej. si `court_zones` cambia)
            simplemente no sale, no revienta.
        zone_df: salida de `_coarsen_zone_profile` — una fila por zona con
            `volume`/`pps`/`diff_pps`.
        left/top/width/height: caja completa del diagrama, en pulgadas. El
            rectángulo de cada zona se escala proporcionalmente dentro de
            ella (x/500 y y/460), así que el aro queda siempre en la base.
    """
    x_span, y_span = 500.0, 460.0
    court = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    court.fill.solid()
    court.fill.fore_color.rgb = _ZONE_EMPTY
    _deck.strip_shape_chrome(court)

    by_label = zone_df.set_index("zone_label") if not zone_df.empty else zone_df
    for label, (x_min, x_max, y_min, y_max) in zone_geometry.items():
        rect_left = left + (x_min / x_span) * width
        rect_top = top + (y_min / y_span) * height
        rect_w = max((x_max - x_min) / x_span * width, 0.05)
        rect_h = max((y_max - y_min) / y_span * height, 0.05)

        row = by_label.loc[label] if (not by_label.empty and label in by_label.index) else None
        diff_pps = row["diff_pps"] if row is not None else None
        volume = int(row["volume"]) if row is not None else 0

        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(rect_left), Inches(rect_top), Inches(rect_w), Inches(rect_h)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = _zone_fill_color(diff_pps)
        shape.shadow.inherit = False
        shape.line.color.rgb = _deck.WHITE
        shape.line.width = Pt(1.0)

        weight = 0.0 if diff_pps is None or pd.isna(diff_pps) else min(abs(float(diff_pps)) / _ZONE_DIFF_CAP, 1.0)
        text_color = _deck.WHITE if weight > 0.35 else _deck.DARK
        tf = shape.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        p.text = shot_quality.format_pps(row["pps"]) if row is not None else "—"
        p.font.size, p.font.bold, p.font.color.rgb = Pt(11), True, text_color
        if volume:
            p2 = tf.add_paragraph()
            p2.alignment = PP_ALIGN.CENTER
            p2.text = f"({volume})"
            p2.font.size, p2.font.color.rgb = Pt(8), text_color


def _build_shot_quality_slide(
    prs: Presentation,
    bullets: List[str],
    rival_name: str,
    *,
    zones_df: Optional[pd.DataFrame] = None,
    attack_zone_profile: Optional[pd.DataFrame] = None,
    defense_zone_profile: Optional[pd.DataFrame] = None,
) -> None:
    """Viñetas de veredicto + dos mapas de tiro (ataque/defensa) por zona, si hay geometría.

    Sin `zones_df` (o vacío) degrada a solo las viñetas, a toda página —
    exactamente el comportamiento de antes de que existiera el mapa (§7 de la
    propuesta: "v1... sin mapa de tiros", ya cerrado).
    """
    zone_geometry = {}
    if zones_df is not None and not zones_df.empty:
        # Solo las filas con un rectángulo REAL (`x_min != x_max`): descarta
        # "Mate" (id 10, un punto) y las sub-zonas de ala degeneradas (14-17,
        # ver `_coarsen_zone_profile`) — "Línea de fondo" (13) ya no aparece
        # en `zone_profile` porque `shot_quality.ZONE_MERGES` la funde con
        # "Pintura" antes de llegar aquí.
        zone_geometry = {
            row.label: (row.x_min, row.x_max, row.y_min, row.y_max)
            for row in zones_df.itertuples() if row.x_min != row.x_max
        }

    has_maps = bool(zone_geometry) and (
        (attack_zone_profile is not None and not attack_zone_profile.empty)
        or (defense_zone_profile is not None and not defense_zone_profile.empty)
    )

    slide = _deck.add_bullets_slide(
        prs,
        title=f"Calidad de tiro (xPPS) — {rival_name}",
        subtitle="Lo que genera atacando y lo que concede defendiendo, contra la media de la liga en cada zona.",
        bullets=bullets,
        fallback_text=f"Sin datos de calidad de tiro para {rival_name} en esta temporada.",
        body_left=8.6 if has_maps else 0.6,
        body_width=4.15 if has_maps else 12.1,
    )
    if not has_maps:
        return

    diagram_top, diagram_w = 1.85, 3.7
    diagram_h = diagram_w * 460.0 / 500.0
    gap = 0.3
    lefts = (0.6, 0.6 + diagram_w + gap)
    labels = ("Ataque (genera)", "Defensa (concede)")
    profiles = (attack_zone_profile, defense_zone_profile)

    for left, label, profile in zip(lefts, labels, profiles):
        header = slide.shapes.add_textbox(Inches(left), Inches(diagram_top - 0.35), Inches(diagram_w), Inches(0.3))
        p = header.text_frame.paragraphs[0]
        p.text = label
        p.font.size, p.font.bold, p.font.color.rgb = Pt(14), True, _deck.DARK
        _draw_shot_zone_map(
            slide, zone_geometry, profile if profile is not None else pd.DataFrame(),
            left=left, top=diagram_top, width=diagram_w, height=diagram_h,
        )

    caption = slide.shapes.add_textbox(
        Inches(0.6), Inches(diagram_top + diagram_h + 0.15), Inches(2 * diagram_w + gap), Inches(0.7)
    )
    tf = caption.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = (
        "Color: PPS por zona contra la media de la liga esa misma zona (verde = mejor, rojo = peor, "
        "saturado a ±0,15 PPS). Cifra grande = PPS de la zona; entre paréntesis, tiros en la temporada."
    )
    p.font.size, p.font.italic, p.font.color.rgb = Pt(11), True, _deck.MUTED


# ========================================================== jugadores clave ==


def _num(row: dict, key: str):
    value = row.get(key)
    return float(value) if value is not None and pd.notna(value) else None


def _player_season_summary_for_prompt(row: dict) -> dict:
    """Medias + percentiles de un jugador en la temporada, para el prompt del LLM.

    Mismo criterio que `postgame_ppt._stat_summary_for_prompt`: solo entran
    las claves con dato real, nunca un `null` que el modelo pueda confundir
    con "cero".
    """
    out = {"partidos": int(row["gp"])} if _num(row, "gp") is not None else {}
    for key, label in (
        ("min_avg", "minutos_promedio"), ("pts_avg", "puntos_promedio"),
        ("reb_avg", "rebotes_promedio"), ("ast_avg", "asistencias_promedio"),
        ("efg_pct", "acierto_tiro_efg_pct"), ("stl_avg", "robos_promedio"),
        ("blk_avg", "tapones_promedio"), ("tov_avg", "perdidas_promedio"),
        ("pf_avg", "faltas_promedio"), ("pir_avg", "valoracion_pir_promedio"),
    ):
        value = _num(row, key)
        if value is not None:
            out[label] = round(value, 1)
    for key, label in (
        ("pts_pct", "percentil_puntos"), ("reb_pct", "percentil_rebotes"),
        ("ast_pct", "percentil_asistencias"), ("efg_pct_pct", "percentil_acierto"),
        ("stl_pct", "percentil_robos"), ("blk_pct", "percentil_tapones"),
        ("tov_pct", "percentil_cuida_balon"), ("pir_pct", "percentil_valoracion"),
    ):
        value = _num(row, key)
        if value is not None:
            out[label] = int(round(value * 100))
    return out


def _rule_based_player_highlights(row: dict, max_items: int = _MAX_HIGHLIGHTS) -> List[str]:
    """Fallback determinista de la diapositiva de jugador: percentiles extremos, no genéricos.

    Mismo espíritu que `postgame_ppt._rule_based_highlights` (candidatos con
    puntuación, se quedan los `max_items` más llamativos, nunca vacío) pero
    sobre medias de TEMPORADA + percentil de liga en vez de un partido suelto
    — un rival se prepara con su perfil, no con su última salida.
    """
    candidates: List[tuple] = []
    gp = _num(row, "gp")
    pts_avg = _num(row, "pts_avg")

    def _extreme(pct_key: str, high_text: str, low_text: str, *, weight: float = 1.0, min_pts: float = 0.0):
        pct = _num(row, pct_key)
        if pct is None or (min_pts and (pts_avg is None or pts_avg < min_pts)):
            return
        if pct >= _HIGH_PERCENTILE and high_text:
            candidates.append((weight * (1 + pct), high_text.format(pct=int(round(pct * 100)))))
        elif pct <= _LOW_PERCENTILE and low_text:
            candidates.append((weight * (1 + (1 - pct)), low_text.format(pct=int(round(pct * 100)))))

    avg = lambda key, dec=1: f"{_num(row, key):.{dec}f}" if _num(row, key) is not None else "?"

    _extreme(
        "pts_pct",
        f"Máxima amenaza anotadora: {avg('pts_avg')} pts/partido (percentil {{pct}})",
        f"Aporta poco en anotación: {avg('pts_avg')} pts/partido (percentil {{pct}})",
        weight=6, min_pts=0,
    )
    _extreme(
        "reb_pct",
        f"Domina el rebote: {avg('reb_avg')} reb/partido (percentil {{pct}})",
        None,
        weight=5,
    )
    _extreme(
        "ast_pct",
        f"Genera juego: {avg('ast_avg')} ast/partido (percentil {{pct}})",
        None,
        weight=5,
    )
    _extreme(
        "efg_pct_pct",
        f"Muy eficiente tirando ({avg('efg_pct')}% eFG, percentil {{pct}})",
        f"Poco eficiente tirando ({avg('efg_pct')}% eFG, percentil {{pct}})",
        weight=4.5, min_pts=6,
    )
    _extreme(
        "stl_pct",
        f"Roba mucho balón: {avg('stl_avg')} robos/partido (percentil {{pct}})",
        None,
        weight=4,
    )
    _extreme(
        "blk_pct",
        f"Protege el aro: {avg('blk_avg')} tapones/partido (percentil {{pct}})",
        None,
        weight=4,
    )
    _extreme(
        "tov_pct",  # ya invertido en la vista: percentil bajo = pierde mucho balón
        None,
        f"Cuida poco el balón: {avg('tov_avg')} pérdidas/partido (percentil {{pct}})",
        weight=4,
    )
    _extreme(
        "pf_pct",  # ídem: percentil bajo = comete muchas faltas
        None,
        f"Acumula faltas rápido: {avg('pf_avg')} faltas/partido (percentil {{pct}})",
        weight=3.5,
    )
    _extreme(
        "pir_pct",
        f"Impacto global muy alto (PIR {avg('pir_avg')}, percentil {{pct}})",
        None,
        weight=5,
    )

    candidates.sort(key=lambda c: c[0], reverse=True)
    picked = [text for _, text in candidates[:max_items]]
    if picked:
        return picked

    basic = f"{avg('min_avg')} min/partido · {avg('pts_avg')} pts/partido"
    if gp is not None:
        basic += f" · {int(gp)} partidos"
    return [basic]


_PLAYER_SYSTEM_PROMPT = (
    "Eres un analista de scouting preparando el partido contra un equipo rival. Te paso las medias "
    "de temporada y los percentiles de liga (0-100, donde alto es mejor salvo que se indique lo "
    "contrario) de sus jugadores más importantes. Para cada jugador, elige entre 2 y 4 notas "
    "VERDADERAMENTE útiles para defenderlo o aprovecharlo — basadas en percentiles altos o bajos "
    "reales, nunca genéricas ni inventadas. Frases cortas en español, listas para una diapositiva "
    "(máximo 10 palabras cada una), sin emojis y sin adornos.\n\n"
    "Responde EXCLUSIVAMENTE con JSON válido, sin explicación ni bloque de código, con esta forma "
    'exacta: {"<player_id>": ["frase 1", "frase 2"], ...} — una entrada por cada player_id recibido.'
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_llm_json_dict(text: str, max_items: int) -> Optional[Dict[str, List[str]]]:
    """Igual que `postgame_ppt._parse_llm_json`: `{clave: [frases]}` o `None` si no es JSON usable."""
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    result: Dict[str, List[str]] = {}
    for key, value in data.items():
        if not isinstance(value, list):
            continue
        phrases = [str(item).strip() for item in value if str(item).strip()]
        if phrases:
            result[str(key)] = phrases[:max_items]
    return result or None


def _llm_player_highlights(client: LLMClient, rows: List[dict], rival_name: str) -> Optional[Dict[str, List[str]]]:
    """Una única llamada con todos los jugadores clave, mismo motivo que `postgame_ppt._llm_highlights`."""
    payload = {
        "rival": rival_name,
        "jugadores": [{"player_id": row["id"], "nombre": row["name"], **_player_season_summary_for_prompt(row)} for row in rows],
    }
    message = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    response = client.chat([message], [], system=_PLAYER_SYSTEM_PROMPT)
    return _parse_llm_json_dict(response.text, _MAX_HIGHLIGHTS)


def select_player_highlights(
    client: Optional[LLMClient], rows: List[dict], rival_name: str
) -> Dict[str, List[str]]:
    """Puntos destacados por jugador clave: LLM si está disponible y responde, reglas si no."""
    highlights = {row["id"]: _rule_based_player_highlights(row) for row in rows}
    if client is None or not rows:
        return highlights
    try:
        llm_result = _llm_player_highlights(client, rows, rival_name)
    except LLMError as exc:
        logger.warning("Dossier de scouting: el LLM ha fallado en jugadores, se usa el fallback por reglas (%s)", exc)
        return highlights
    if not llm_result:
        return highlights
    for player_id, phrases in llm_result.items():
        if player_id in highlights and phrases:
            highlights[player_id] = phrases
    return highlights


def _build_player_slides(prs: Presentation, rows: List[dict], highlights: Dict[str, List[str]], rival_name: str) -> None:
    if not rows:
        _deck.add_bullets_slide(
            prs,
            title=f"Jugadores clave — {rival_name}",
            bullets=[],
            fallback_text=f"Sin plantilla con partidos registrados para {rival_name} en esta temporada.",
        )
        return

    for row in rows:
        bits = []
        gp = _num(row, "gp")
        if gp is not None:
            bits.append(f"{int(gp)} PJ")
        min_avg = _num(row, "min_avg")
        if min_avg is not None:
            bits.append(f"{min_avg:.1f}'")
        pts_avg = _num(row, "pts_avg")
        if pts_avg is not None:
            bits.append(f"{pts_avg:.1f} pts")
        reb_avg = _num(row, "reb_avg")
        if reb_avg is not None:
            bits.append(f"{reb_avg:.1f} reb")
        ast_avg = _num(row, "ast_avg")
        if ast_avg is not None:
            bits.append(f"{ast_avg:.1f} ast")

        name = row["name"]
        number = row.get("number")
        header = f"#{int(number)} · {name}" if number is not None and pd.notna(number) else name

        _deck.add_photo_bullets_slide(
            prs,
            name=header,
            subtitle=row.get("position") or "",
            stat_line=" · ".join(bits) if bits else "Sin partidos con estadísticas todavía",
            bullets=highlights.get(row["id"], []),
            photo_local_path=row.get("photo_local_path"),
            photo_url=row.get("photo_url"),
            fallback_text="Sin percentiles todavía para este jugador (menos de 5 partidos jugados).",
        )


# ============================================================== quintetos ==


def _build_lineups_slide(prs: Presentation, lineups_df: pd.DataFrame, quarters_df: pd.DataFrame, rival_name: str) -> None:
    if lineups_df.empty:
        _deck.add_bullets_slide(
            prs,
            title=f"Quintetos y cuartos — {rival_name}",
            bullets=[],
            fallback_text=f"Sin quintetos reconstruidos para {rival_name} en esta temporada.",
        )
        return

    total_combos = int(lineups_df["total_combos"].iloc[0])
    rows = [
        [r.jugadores, f"{r.minutes:.1f}", f"{r.plus_minus:+.0f}", int(r.stints)]
        for r in lineups_df.head(6).itertuples()
    ]
    footer = f"{len(lineups_df)} quintetos más usados de {total_combos} combinaciones distintas en la temporada."
    if not quarters_df.empty:
        best = quarters_df.assign(net=quarters_df["avg_points_for"] - quarters_df["avg_points_against"])
        best_q = best.loc[best["net"].idxmax()]
        worst_q = best.loc[best["net"].idxmin()]
        footer += (
            f" Mejor cuarto: {int(best_q['quarter'])}º ({best_q['net']:+.1f} de diferencial). "
            f"Peor cuarto: {int(worst_q['quarter'])}º ({worst_q['net']:+.1f})."
        )

    _deck.add_table_slide(
        prs,
        title=f"Quintetos más usados — {rival_name}",
        columns=["Quinteto", "Min. juntos", "+/-", "Tramos"],
        rows=rows,
        footer=footer,
        col_widths=[7.5, 1.7, 1.4, 1.5],
    )


# ========================================================= claves del partido ==


def _win_threshold_key_candidates(ctx: dict) -> List[tuple]:
    """Candidatos de "claves del partido" a partir de los umbrales de victoria (propuesta 09).

    `ctx["win_threshold_cards"]` ya viene con el ajuste al perfil DEL RIVAL
    aplicado (`win_thresholds.rival_adjusted_card`, ver `generate_scouting_
    ppt`) — es justo el enganche que §6 de la propuesta 09 pedía ("es también
    la fuente natural de la diapositiva 'claves del partido' del dossier:
    calcularlo una vez, usarlo en los dos sitios"). El peso se escala por la
    separación de cada tarjeta (mismo orden de magnitud que las demás
    candidatas de esta función, 1.5-6) para que un umbral muy separador
    compita de verdad con el resto, no se cuele solo por existir.
    """
    cards = ctx.get("win_threshold_cards") or []
    rival_name = ctx["rival_name"]
    out = []
    for card in cards:
        headline = win_thresholds.card_headline(card)
        text = f"Objetivo del partido: {headline[0].lower()}{headline[1:]}"
        if card.get("is_rival_adjusted"):
            text += f" (ajustado al perfil de {rival_name})."
        else:
            text += "."
        weight = 2.5 + card.get("separation", 0.0) / 10.0
        out.append((weight, text))
    return out


def _rule_based_game_keys(ctx: dict, max_items: int = _MAX_KEYS) -> List[str]:
    """Fallback determinista de "Claves del partido": percentiles extremos +
    cara a cara + objetivos de umbrales de victoria (propuesta 09) + zona más
    castigable + reparto por cuarto, priorizado por lo extremo que sea cada
    señal — nunca vacío."""
    candidates: List[tuple] = _win_threshold_key_candidates(ctx)
    rival_name = ctx["rival_name"]
    style_row = ctx["style_row"]

    if style_row is not None:
        for pct_key, high_text, low_text in (
            ("pace_pct",
             f"{rival_name} juega rápido: {style_row['pace']:.1f} posesiones/partido (percentil {{pct}})",
             f"{rival_name} juega lento: {style_row['pace']:.1f} posesiones/partido (percentil {{pct}})"),
            ("ortg_pct",
             f"Ataque muy eficiente ({style_row['ortg']:.1f} ORtg, percentil {{pct}})",
             f"Ataque poco eficiente ({style_row['ortg']:.1f} ORtg, percentil {{pct}})"),
            ("drtg_pct",
             f"Defensa sólida ({style_row['drtg']:.1f} DRtg, percentil {{pct}})",
             f"Defensa floja ({style_row['drtg']:.1f} DRtg, percentil {{pct}}) — atacable"),
            ("net_rating_pct",
             f"Equipo dominante: {style_row['net_rating']:+.1f} de net rating (percentil {{pct}})",
             f"Equipo con net rating negativo: {style_row['net_rating']:+.1f} (percentil {{pct}})"),
        ):
            pct = _num(style_row, pct_key)
            if pct is None:
                continue
            if pct >= _HIGH_PERCENTILE:
                candidates.append(((pct - 0.5) * 10, high_text.format(pct=int(round(pct * 100)))))
            elif pct <= _LOW_PERCENTILE:
                candidates.append(((0.5 - pct) * 10, low_text.format(pct=int(round(pct * 100)))))

    h2h = ctx["h2h_summary"]
    if h2h is not None and h2h["total"] >= 2:
        candidates.append(
            (2.0, f"Cara a cara histórico: Baskonia {h2h['wins']}–{h2h['losses']} en {h2h['total']} enfrentamientos.")
        )

    if ctx["top_player"] is not None:
        top = ctx["top_player"]
        pct = _num(top, "pts_pct")
        if pct is not None and pct >= _HIGH_PERCENTILE:
            candidates.append(
                (3.0 + pct, f"Máxima amenaza: {top['name']} ({_num(top, 'pts_avg'):.1f} pts/partido, percentil {int(round(pct * 100))}).")
            )

    for diff_key, subject in (("attack_diff", f"El ataque de {rival_name}"), ("defense_diff", f"La defensa de {rival_name}")):
        diff = ctx.get(diff_key)
        if diff is not None and abs(diff) >= 0.05:
            side = "por encima" if diff > 0 else "por debajo"
            candidates.append(
                (2.5 + abs(diff) * 10, f"{subject} sale {shot_quality.format_pps(abs(diff))} PPS {side} de lo esperado por zona.")
            )

    quarters_df = ctx["quarters_df"]
    if not quarters_df.empty and len(quarters_df) >= 2:
        net = quarters_df["avg_points_for"] - quarters_df["avg_points_against"]
        spread = float(net.max() - net.min())
        if spread >= 4:
            best_q = int(quarters_df.loc[net.idxmax(), "quarter"])
            worst_q = int(quarters_df.loc[net.idxmin(), "quarter"])
            candidates.append(
                (1.5 + spread / 10,
                 f"Reparto por cuarto desigual: mejor en el {best_q}º, se hunde en el {worst_q}º "
                 f"(diferencial {spread:.1f} de diferencia entre ambos).")
            )

    candidates.sort(key=lambda c: c[0], reverse=True)
    return [text for _, text in candidates[:max_items]]


_KEYS_SYSTEM_PROMPT = (
    "Eres el analista del Baskonia preparando la reunión técnica del día antes de un partido. Te "
    "paso el perfil del rival ya resuelto (percentiles de liga, cara a cara, calidad de tiro por "
    "zona, reparto por cuarto, jugador más peligroso, objetivos numéricos de umbrales de victoria "
    "ya ajustados a este rival). Redacta EXACTAMENTE 5 claves del partido: "
    "frases cortas en español (máximo 14 palabras), concretas y accionables, basadas SOLO en los "
    "datos que te paso, sin inventar nada que no esté ahí. Sin emojis ni adornos.\n\n"
    "Responde EXCLUSIVAMENTE con JSON válido, sin explicación ni bloque de código: una lista de "
    'exactamente 5 cadenas, forma exacta: ["clave 1", "clave 2", "clave 3", "clave 4", "clave 5"].'
)


def _parse_llm_json_list(text: str, max_items: int) -> Optional[List[str]]:
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    phrases = [str(item).strip() for item in data if str(item).strip()]
    return phrases[:max_items] or None


def _llm_game_keys(client: LLMClient, ctx: dict) -> Optional[List[str]]:
    style_row = ctx["style_row"]
    payload = {
        "rival": ctx["rival_name"],
        "identidad": (
            {k: (None if pd.isna(v) else round(float(v), 1)) for k, v in style_row.items()
             if k in ("pace", "pace_pct", "ortg", "ortg_pct", "drtg", "drtg_pct", "net_rating", "net_rating_pct")}
            if style_row is not None else None
        ),
        "cara_a_cara": ctx["h2h_summary"],
        "calidad_tiro": {"diff_ataque_pps": ctx.get("attack_diff"), "diff_defensa_pps": ctx.get("defense_diff")},
        "jugador_mas_peligroso": (
            {"nombre": ctx["top_player"]["name"], "pts_avg": round(float(ctx["top_player"]["pts_avg"]), 1)}
            if ctx["top_player"] is not None and pd.notna(ctx["top_player"].get("pts_avg")) else None
        ),
        # Propuesta 09 (§6): los umbrales de victoria ya ajustados al perfil
        # del rival, como candidatos de "objetivo del partido" — el modelo
        # los redacta, no los recalcula (los números vienen ya resueltos por
        # `win_thresholds`, igual que el resto de este payload).
        "objetivos_umbral": [
            {
                "batalla": card["label"],
                "objetivo": win_thresholds.card_value_text(card),
                "ajustado_al_rival": bool(card.get("is_rival_adjusted")),
            }
            for card in (ctx.get("win_threshold_cards") or [])
        ],
    }
    message = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    response = client.chat([message], [], system=_KEYS_SYSTEM_PROMPT)
    return _parse_llm_json_list(response.text, _MAX_KEYS)


def select_game_keys(client: Optional[LLMClient], ctx: dict) -> List[str]:
    """Claves del partido: LLM si está disponible y responde con 5 frases válidas, reglas si no."""
    fallback = _rule_based_game_keys(ctx)
    if client is None:
        return fallback
    try:
        llm_result = _llm_game_keys(client, ctx)
    except LLMError as exc:
        logger.warning("Dossier de scouting: el LLM ha fallado en claves del partido, se usa el fallback (%s)", exc)
        return fallback
    return llm_result if llm_result else fallback


def _build_keys_slide(prs: Presentation, keys: List[str], rival_name: str) -> None:
    _deck.add_bullets_slide(
        prs,
        title="Claves del partido",
        bullets=keys,
        fallback_text=_FALLBACK_KEYS_TEXT.replace("claves automáticas", f"claves automáticas sobre {rival_name}"),
    )


# =================================================================== fachada ==


def rotation_bullets(engine, rival_team_id: str, season_id: int, rival_name: str) -> List[str]:
    """Resumen del patrón de rotación del rival (propuesta 13), el mismo que pinta "Próximo rival".

    Lista vacía si la base de datos no tiene tramos (`lineup_stints`) para
    ese rival: la diapositiva simplemente no se añade.
    """
    rows = queries.team_stint_rows(engine, rival_team_id, season_id)
    if rows.empty:
        return []
    rotation = rotation_patterns.player_rotation_table(rotation_patterns.minute_shares(rows))
    closers, close_games = rotation_patterns.closing_players(rows)
    return rotation_patterns.rotation_insights(
        rival_name,
        rotation,
        rotation_patterns.starting_lineups(rows),
        closers,
        close_games,
        rotation_patterns.block_performance(rows),
        queries_assistant.player_on_off(engine, rival_team_id, season_id),
        n_games=int(rows["game_id"].nunique()),
    )


def _build_rotation_slide(prs: Presentation, bullets: List[str], rival_name: str) -> None:
    _deck.add_bullets_slide(
        prs,
        title=f"Rotación de {rival_name}",
        subtitle="Quién sale, cuándo descansan sus principales, quién cierra y dónde sufre",
        bullets=bullets,
        fallback_text="Sin tramos de quinteto para este rival.",
    )


def generate_scouting_ppt(
    engine,
    *,
    rival_team_id: str,
    rival_name: str,
    is_home: bool,
    competition: str,
    fecha: str,
    own_team_id: str,
    scouting_season_id: int,
    scouting_season_label: str,
    is_fallback_season: bool,
    today: dt.date,
    llm_client: Optional[LLMClient] = None,
    max_players: int = _MAX_PLAYERS,
    match_date: Optional[dt.date] = None,
) -> bytes:
    """Punto de entrada único para la página: datos + dos capas de texto + `.pptx`, en bytes.

    Args:
        rival_team_id/rival_name: el rival, tal como los da `queries.next_matchup`.
        is_home/competition/fecha: contexto del PARTIDO (puede ser de una
            temporada distinta a la de los DATOS, ver `scouting_season_id` —
            mismo caso real que documenta `queries.team_scouting_season`).
        own_team_id: `queries.get_own_team_id` — para el cara a cara y el récord.
        scouting_season_id/scouting_season_label/is_fallback_season: de qué
            temporada sale el scouting (la del partido, o la última con datos
            del rival si esa está vacía todavía).
        today: para acotar récord/partidos ya jugados, igual que el resto de
            `queries.py` (determinismo/caché, ver su docstring de módulo).
        llm_client: `assistant.llm.build_llm_client()` ya construido, o
            `None` para quedarse solo con las reglas en jugadores/claves.
        max_players: cuántas diapositivas de jugador generar, por producción
            (minutos primero, después puntos) — 5 a 8 según la propuesta.
        match_date: fecha del partido, para la predicción de la portada
            (propuesta 16: ajuste con lo anterior y descanso hasta ese día).
            `None` = `today`.
    """
    h2h_df = queries.head_to_head(engine, own_team_id, rival_team_id)
    h2h_summary = None
    if not h2h_df.empty:
        wins = int((h2h_df["pts_favor"] > h2h_df["pts_contra"]).sum())
        h2h_summary = {"wins": wins, "losses": len(h2h_df) - wins, "total": len(h2h_df)}

    record_df = queries.team_record(engine, rival_team_id, scouting_season_id, today)
    rival_record = list(record_df.itertuples(index=False, name=None)) if not record_df.empty else []

    style_df = queries_assistant.team_style_row(engine, rival_team_id, scouting_season_id)
    style_row = style_df.iloc[0] if not style_df.empty else None

    quarters_df = queries.team_quarter_profile(engine, rival_team_id, scouting_season_id)

    roster_df = queries_assistant.team_roster_production(engine, rival_team_id, scouting_season_id)
    # Solo quien de verdad ha jugado esta temporada (descarta bajas/altas sin
    # minutos), por minutos primero — el rol real, no el dorsal ni el nombre.
    roster_df = roster_df.dropna(subset=["gp"]).sort_values("min_avg", ascending=False)
    top_rows = roster_df.head(max_players).to_dict("records")
    # Bio (foto, dorsal) por separado: `team_roster_production` no la trae
    # (está pensada para el asistente, sin foto) — se completa con
    # `player_bio`, ya cacheada, una llamada por jugador clave (5-8, no
    # merece una consulta nueva por lotes).
    for row in top_rows:
        bio = queries.player_bio(engine, row["id"])
        if bio:
            row["photo_url"] = bio.get("photo_url")
            row["photo_local_path"] = bio.get("photo_local_path")
        # Solo se copian los PERCENTILES, nunca las medias en bruto: las de
        # `team_roster_production` son la temporada COMBINADA (todas las
        # competiciones), las de `player_percentile_row` son de su
        # competición con más partidos (el percentil siempre vive dentro de
        # una sola competición, ver el docstring de `team_style_row`) — para
        # un rival que juega dos competiciones (ACB + Euroliga) mezclar la
        # media combinada con el percentil de una sola sería presentar dos
        # cosas distintas como si fueran la misma.
        percentiles = queries_assistant.player_percentile_row(engine, row["id"], scouting_season_id)
        if not percentiles.empty:
            prow = percentiles.iloc[0].to_dict()
            row["league_players"] = prow.get("league_players")
            row["percentile_competition"] = prow.get("competition")
            for pct_col in (
                "min_pct", "pts_pct", "reb_pct", "ast_pct", "efg_pct_pct",
                "stl_pct", "blk_pct", "tov_pct", "pf_pct", "pir_pct",
            ):
                row[pct_col] = prow.get(pct_col)
            # Estas cuatro SÍ se copian en bruto: `team_roster_production` no
            # las trae en absoluto (boxscore ampliado, Fase 1), así que no hay
            # media combinada con la que puedan entrar en conflicto.
            for avg_col in ("stl_avg", "blk_avg", "tov_avg", "pf_avg", "pir_avg"):
                row[avg_col] = prow.get(avg_col)

    top_player = None
    if top_rows:
        by_pts = sorted((r for r in top_rows if pd.notna(r.get("pts_avg"))), key=lambda r: r["pts_avg"], reverse=True)
        top_player = by_pts[0] if by_pts else None

    attack_counts = queries.team_shot_counts(engine, rival_team_id, scouting_season_id)
    zones_baseline = shot_quality.league_baseline(queries.league_shot_counts(engine, scouting_season_id))
    attack_valued = shot_quality.with_expected(attack_counts, zones_baseline)
    defense_valued = shot_quality.with_expected(
        queries.team_shot_counts(engine, rival_team_id, scouting_season_id, conceded=True), zones_baseline
    )
    attack_summary = shot_quality.summarize(attack_valued)
    defense_summary = shot_quality.summarize(defense_valued)
    _, shot_coverage = shot_quality.split_usable(attack_counts)

    # Propuesta 09 (§6): los umbrales de victoria de liga, ajustados al perfil
    # de ESTE rival, son la fuente natural de "claves del partido" — se
    # calculan una vez aquí y los usan tanto `_rule_based_game_keys` como el
    # prompt del LLM (`ctx["win_threshold_cards"]`). Vacío (nunca `None`) si
    # la temporada no tiene aún estadísticas avanzadas suficientes: el resto
    # del dossier sigue construyéndose igual, sin esta fuente de claves.
    league_factor_rows = queries.game_factor_rows(engine, scouting_season_id)
    win_threshold_cards: List[dict] = []
    if not league_factor_rows.empty:
        objective_cards = win_thresholds.league_objectives(league_factor_rows)
        rival_avg = win_thresholds.rival_concession_averages(league_factor_rows, rival_team_id)
        league_avg = win_thresholds.league_concession_averages(league_factor_rows)
        win_threshold_cards = [
            win_thresholds.rival_adjusted_card(card, rival_avg, league_avg) for card in objective_cards
        ]

    # Propuesta 16: margen esperado y qué lo mueve, en una frase de portada.
    # `None` si la temporada no tiene partidos para ajustar el modelo.
    prediction_result = queries_prediction.matchup_prediction(
        engine, own_team_id, rival_team_id, scouting_season_id, match_date or today, bool(is_home), competition
    )
    prediction_sentence = (
        None if prediction_result is None else prediction_summary(prediction_result["prediction"], rival_name)
    )

    ctx = {
        "rival_name": rival_name,
        "is_home": is_home,
        "competition": competition,
        "fecha": fecha,
        "h2h_summary": h2h_summary,
        "rival_record": rival_record,
        "is_fallback_season": is_fallback_season,
        "scouting_season_label": scouting_season_label,
        "style_row": style_row,
        "quarters_df": quarters_df,
        "top_player": top_player,
        "attack_diff": attack_summary["diff_shrunk"] if attack_summary["reliable"] else None,
        "defense_diff": defense_summary["diff_shrunk"] if defense_summary["reliable"] else None,
        "win_threshold_cards": win_threshold_cards,
        "prediction_sentence": prediction_sentence,
    }

    lineups_df = queries.season_lineups(engine, rival_team_id, scouting_season_id)
    shot_quality_bullets = _shot_quality_bullets(attack_valued, defense_valued, shot_coverage, rival_name)
    player_highlights = select_player_highlights(llm_client, top_rows, rival_name)
    game_keys = select_game_keys(llm_client, ctx)

    # Mapa de tiro (§4/§7 de la propuesta 03): rectángulos NATIVOS de pptx
    # sobre la geometría real de `court_zones`, sin dependencia nueva — ver el
    # docstring de `_coarsen_zone_profile` para por qué se colapsan las
    # sub-zonas de ala partidas por el arco en vez de reproducirlo a mano.
    zones_df = queries.court_zones(engine)
    attack_zone_profile = _coarsen_zone_profile(shot_quality.zone_profile(attack_valued))
    defense_zone_profile = _coarsen_zone_profile(shot_quality.zone_profile(defense_valued))

    return build_scouting_ppt(
        ctx, style_df, top_rows, player_highlights, lineups_df, shot_quality_bullets, game_keys,
        zones_df=zones_df, attack_zone_profile=attack_zone_profile, defense_zone_profile=defense_zone_profile,
        rotation_summary=rotation_bullets(engine, rival_team_id, scouting_season_id, rival_name),
    )


def build_scouting_ppt(
    ctx: dict,
    style_df: pd.DataFrame,
    player_rows: List[dict],
    player_highlights: Dict[str, List[str]],
    lineups_df: pd.DataFrame,
    shot_quality_bullets: List[str],
    game_keys: List[str],
    *,
    zones_df: Optional[pd.DataFrame] = None,
    attack_zone_profile: Optional[pd.DataFrame] = None,
    defense_zone_profile: Optional[pd.DataFrame] = None,
    rotation_summary: Optional[List[str]] = None,
) -> bytes:
    """Bytes del `.pptx`: las seis diapositivas del dossier, con todo el texto ya resuelto.

    Pura (nada de base de datos) — mismo motivo que
    `postgame_ppt.build_postgame_ppt`: los tests ejercitan la maquetación con
    datos de mentira, sin fixture de BD; `generate_scouting_ppt` es la única
    que toca `engine`, y le basta con construir estos argumentos y llamar aquí.

    Args:
        zones_df/attack_zone_profile/defense_zone_profile: geometría de
            `court_zones` y los dos perfiles de zona YA COARSENED
            (`_coarsen_zone_profile`), para dibujar el mapa de tiro de la
            diapositiva de calidad de tiro (§4 de la propuesta 03). `None` (o
            vacío) degrada a la diapositiva sin mapas, solo con las viñetas —
            mismo criterio de "nunca un hueco vacío" que el resto del módulo.
        rotation_summary: frases de `rotation_bullets` (propuesta 13). Si
            viene vacío o `None`, la diapositiva de rotación no se añade.
    """
    rival_name = ctx["rival_name"]
    prs = _deck.new_presentation()
    _build_cover_slide(prs, ctx)
    _build_identity_slide(prs, style_df, rival_name)
    _build_shot_quality_slide(
        prs, shot_quality_bullets, rival_name,
        zones_df=zones_df, attack_zone_profile=attack_zone_profile, defense_zone_profile=defense_zone_profile,
    )
    _build_player_slides(prs, player_rows, player_highlights, rival_name)
    _build_lineups_slide(prs, lineups_df, ctx["quarters_df"], rival_name)
    if rotation_summary:
        _build_rotation_slide(prs, rotation_summary, rival_name)
    _build_keys_slide(prs, game_keys, rival_name)

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
