"""Informe "PPT para Paolo": una diapositiva por jugador del Baskonia con lo
más destacado — para bien o para mal — de UN partido concreto.

El encargo es literal: un botón que genera un `.pptx` para el cuerpo técnico,
no un chat. Por eso este módulo no pasa por `assistant.agent.Agent` (sin
herramientas que llamar, sin historial, una sola pregunta con toda la
estadística ya resuelta) — solo reutiliza el mismo `LLMClient` que construye
`assistant.llm.build_llm_client()`, con una única llamada que cubre los doce
jugadores de golpe (una llamada por jugador sería doce veces el coste y la
espera para el mismo resultado).

Dos capas, en este orden de preferencia:

1. **LLM** (`_llm_highlights`): más jugoso — puede cruzar varias estadísticas
   a la vez ("8 puntos en solo 9 minutos, muy eficiente") en vez de mirarlas
   una a una. Es opcional: si no hay proveedor configurado (`build_llm_client()
   -> None`) o la llamada falla (`LLMError`, JSON inválido...), no se rompe
   nada — se seguía sin él.
2. **Reglas** (`_rule_based_highlights`): umbrales fijos sobre cada estadística
   por separado. Es el suelo: SIEMPRE hay algo que enseñar en cada
   diapositiva, con o sin modelo configurado — mismo principio que el resto
   de la interfaz ("el asistente no es un requisito de arranque", aquí
   aplicado a este botón).

`select_highlights` combina las dos: calcula el fallback de reglas para
TODOS los jugadores primero y, si el LLM responde con datos válidos para
alguno, lo sustituye. Un jugador que el LLM no menciona (o si el LLM entero
falla) se queda con su fallback, nunca con una diapositiva vacía.
"""
import io
import json
import logging
import re
from typing import Dict, List, Optional

import pandas as pd
from pptx import Presentation

# `queries.py`/`assistant.llm`/`_deck` se importan igual venga el paquete por
# `app.*` (pytest, raíz en sys.path) o por su nombre corto (Streamlit, `app/`
# en sys.path) — mismo motivo y mismo patrón que `assistant/tools/context.py`.
try:  # pragma: no cover - depende de cómo se arranque el proceso, no de la lógica
    from app.assistant.llm import LLMClient, LLMError
    from app.components.branding import CREST_PATH
    from app.data import queries
    from app.reports import _deck
except ImportError:  # pragma: no cover
    from assistant.llm import LLMClient, LLMError
    from components.branding import CREST_PATH
    from data import queries
    from reports import _deck

logger = logging.getLogger(__name__)

_MAX_HIGHLIGHTS = 4
_FALLBACK_TEXT = "Sin nada destacado que señalar en este partido."


# ============================================================ estadísticas ==


def _num(row: dict, key: str):
    """`row[key]` como número, o `None` si falta (NULL de SQL == NaN en pandas)."""
    value = row.get(key)
    return float(value) if pd.notna(value) else None


def _stat_summary_for_prompt(row: dict) -> dict:
    """La línea de un jugador, legible y sin huecos, para el prompt del LLM.

    Mismas claves en español que se le piden de vuelta como frases — el
    modelo no tiene que adivinar qué es `tov` ni convertir minutos decimales.
    Los campos que faltan en ESTE partido (boxscore ampliado o triples sin
    coordenadas, ver `queries.game_player_report`) simplemente no aparecen,
    en vez de mandar un `null` que el modelo podría malinterpretar como "cero".
    """
    out = {
        "dorsal": int(row["number"]),
        "minutos": round(float(row["minutes"]), 1),
        "puntos": int(row["pts"]),
        "rebotes": int(row["reb"]),
        "asistencias": int(row["ast"]),
    }
    tpa = _num(row, "tpa")
    if tpa:
        out["triples"] = f"{int(row['tpm'])}/{int(tpa)}"
    fta = _num(row, "fta")
    if fta:
        out["tiros_libres"] = f"{int(row['ftm'])}/{int(fta)}"
    efg = _num(row, "efg_pct")
    if efg is not None:
        out["acierto_tiro_efg_pct"] = round(efg, 1)
    for key, label in (
        ("stl", "robos"), ("tov", "perdidas"), ("blk", "tapones"), ("pf", "faltas"),
        ("oreb", "rebotes_ofensivos"), ("dreb", "rebotes_defensivos"), ("dunks", "mates"),
    ):
        value = _num(row, key)
        if value is not None:
            out[label] = int(value)
    plus_minus = _num(row, "plus_minus")
    if plus_minus is not None:
        out["mas_menos"] = int(plus_minus)
    pir = _num(row, "pir")
    if pir is not None:
        out["valoracion_pir"] = int(pir)
    return out


def _rule_based_highlights(row: dict, max_items: int = _MAX_HIGHLIGHTS) -> List[str]:
    """Fallback determinista: umbrales fijos, uno por estadística, sin LLM.

    Cada candidato lleva una puntuación de "cuánto destaca" — no es una
    fórmula científica, es una ordenación razonable para quedarse con los
    `max_items` más llamativos (para bien o para mal) en vez de listarlos
    todos. Si ninguna estadística cruza su umbral, la diapositiva no se queda
    en blanco: cae a la línea básica (minutos + puntos, con rebotes/asistencias
    si aportan algo).
    """
    minutes = float(row["minutes"])
    pts, reb, ast = int(row["pts"]), int(row["reb"]), int(row["ast"])
    candidates: List[tuple] = []

    tpa = _num(row, "tpa")
    if tpa and tpa >= 3:
        tpm = int(row["tpm"])
        pct = 100.0 * tpm / tpa
        if pct >= 50:
            candidates.append((6 + pct / 20, f"{tpm}/{int(tpa)} en triples ({pct:.0f}%)"))
        elif tpm == 0 and tpa >= 4:
            candidates.append((5, f"0/{int(tpa)} en triples"))

    if pts >= 20:
        candidates.append((4 + (pts - 20) / 5, f"{pts} puntos"))
    elif pts <= 2 and minutes >= 15:
        candidates.append((3, f"Solo {pts} puntos en {minutes:.0f} min"))

    if minutes < 12 and pts >= 8:
        candidates.append((4.5, f"{pts} puntos en solo {minutes:.0f} min"))

    if reb >= 10:
        candidates.append((4.5, f"{reb} rebotes"))
    if ast >= 6:
        candidates.append((4, f"{ast} asistencias"))

    tov = _num(row, "tov")
    if tov is not None and tov >= 4:
        candidates.append((4.5, f"{int(tov)} pérdidas"))
    stl = _num(row, "stl")
    if stl is not None and stl >= 3:
        candidates.append((3.5, f"{int(stl)} robos"))
    blk = _num(row, "blk")
    if blk is not None and blk >= 2:
        candidates.append((3.5, f"{int(blk)} tapones"))
    pf = _num(row, "pf")
    if pf is not None and pf >= 5:
        candidates.append((3.5, f"{int(pf)} faltas cometidas"))
    dunks = _num(row, "dunks")
    if dunks and dunks >= 2:
        candidates.append((3, f"{int(dunks)} mates"))

    pir = _num(row, "pir")
    if pir is not None:
        if pir >= 20:
            candidates.append((5, f"Valoración (PIR) de {int(pir)}"))
        elif pir <= 0:
            candidates.append((4, f"Valoración (PIR) de {int(pir)} en {minutes:.0f} min"))

    plus_minus = _num(row, "plus_minus")
    if plus_minus is not None:
        if plus_minus >= 15:
            candidates.append((4, f"+{int(plus_minus)} en el +/-"))
        elif plus_minus <= -15:
            candidates.append((4, f"{int(plus_minus)} en el +/-"))

    fta = _num(row, "fta")
    if fta and fta >= 4:
        ftm = int(row["ftm"])
        pct = 100.0 * ftm / fta
        if pct >= 99:
            candidates.append((3, f"{ftm}/{int(fta)} en libres, perfecto"))
        elif pct < 50:
            candidates.append((3, f"{ftm}/{int(fta)} en libres"))

    efg = _num(row, "efg_pct")
    if efg is not None and pts >= 8:
        if efg >= 65:
            candidates.append((3, f"{efg:.0f}% de acierto en el tiro (eFG%)"))
        elif efg <= 25:
            candidates.append((3, f"Solo {efg:.0f}% de acierto en el tiro (eFG%)"))

    candidates.sort(key=lambda c: c[0], reverse=True)
    picked = [text for _, text in candidates[:max_items]]
    if picked:
        return picked

    basic = f"{minutes:.0f} min · {pts} puntos"
    if reb >= 4 or ast >= 3:
        basic += f" · {reb} reb · {ast} ast"
    return [basic]


# ==================================================================== LLM ==

_SYSTEM_PROMPT = (
    "Eres el analista de datos del Baskonia. Te paso las estadísticas de cada jugador propio en UN "
    "partido concreto (no medias de temporada). Para cada jugador, elige entre 2 y 4 datos "
    "VERDADERAMENTE destacados de ESE partido — para bien o para mal (mucho acierto en el triple, "
    "muchas pérdidas, doble-doble, un partido en blanco, un +/- muy alto o muy bajo, muchos rebotes, "
    "pocos minutos con mucho impacto, faltas en racha, etc.). No repitas puntos o minutos sin más si "
    "no destacan de verdad. Frases cortas en español, listas para una diapositiva (máximo 9 palabras "
    "cada una), sin emojis y sin adornos.\n\n"
    "Responde EXCLUSIVAMENTE con JSON válido, sin explicación ni bloque de código, con esta forma "
    'exacta: {"<player_id>": ["frase 1", "frase 2"], ...} — una entrada por cada player_id recibido, '
    "aunque solo tenga una frase."
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_llm_json(text: str) -> Optional[Dict[str, List[str]]]:
    """`{player_id: [frases]}` a partir del texto del modelo, o `None` si no es JSON usable.

    Tolera el modelo envolviendo la respuesta en un bloque ```json — se pidió
    que no lo hiciera, pero no todos los proveedores obedecen igual de bien
    (§8.4 en `prompt.py` documenta la misma cautela para otras respuestas).
    """
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
            result[str(key)] = phrases[:_MAX_HIGHLIGHTS]
    return result or None


def _llm_highlights(client: LLMClient, rows: List[dict], game_context: dict) -> Optional[Dict[str, List[str]]]:
    """Una única llamada al modelo con todos los jugadores, para no pagar (ni esperar) doce vueltas."""
    payload = {
        "partido": {
            "rival": game_context.get("rival"),
            "resultado": game_context.get("resultado"),
            "competicion": game_context.get("competicion"),
            "fecha": game_context.get("fecha"),
        },
        "jugadores": [{"player_id": row["player_id"], **_stat_summary_for_prompt(row)} for row in rows],
    }
    message = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    response = client.chat([message], [], system=_SYSTEM_PROMPT)
    return _parse_llm_json(response.text)


def select_highlights(
    client: Optional[LLMClient], rows: List[dict], game_context: dict
) -> Dict[str, List[str]]:
    """Puntos destacados por jugador: LLM si está disponible y responde, reglas si no (ver docstring del módulo)."""
    highlights = {row["player_id"]: _rule_based_highlights(row) for row in rows}
    if client is None or not rows:
        return highlights
    try:
        llm_result = _llm_highlights(client, rows, game_context)
    except LLMError as exc:
        logger.warning("PPT para Paolo: el LLM ha fallado, se usa el fallback por reglas (%s)", exc)
        return highlights
    if not llm_result:
        return highlights
    for player_id, phrases in llm_result.items():
        if player_id in highlights and phrases:
            highlights[player_id] = phrases
    return highlights


# ================================================================= diapositivas ==
#
# La maquetación en sí (banda de portada, foto+viñetas) vive en `_deck.py`,
# compartida con `scouting_ppt.py` — aquí solo se decide QUÉ texto va en cada
# hueco para el informe de post-partido.


def _add_title_slide(prs: Presentation, game_context: dict) -> None:
    _deck.add_band_title_slide(
        prs,
        title="Puntos destacados del partido",
        subtitle=game_context.get("subtitle", ""),
        footer="Generado automáticamente a partir del boxscore — revisa los datos antes de usarlo en pista.",
        crest_path=CREST_PATH,
    )


def _add_player_slide(prs: Presentation, row: dict, highlights: List[str]) -> None:
    minutes = float(row["minutes"])
    bits = [f"{minutes:.0f}'", f"{int(row['pts'])} pts", f"{int(row['reb'])} reb", f"{int(row['ast'])} ast"]
    pir = _num(row, "pir")
    if pir is not None:
        bits.append(f"PIR {int(pir)}")

    _deck.add_photo_bullets_slide(
        prs,
        name=f"#{int(row['number'])} · {row['player_name']}",
        subtitle=row.get("position") or "",
        stat_line=" · ".join(bits),
        bullets=highlights,
        photo_local_path=row.get("photo_local_path"),
        photo_url=row.get("photo_url"),
        fallback_text=_FALLBACK_TEXT,
    )


def build_postgame_ppt(rows: List[dict], highlights: Dict[str, List[str]], game_context: dict) -> bytes:
    """Bytes del `.pptx`: una portada + una diapositiva por fila de `rows`."""
    prs = _deck.new_presentation()

    _add_title_slide(prs, game_context)
    for row in rows:
        _add_player_slide(prs, row, highlights.get(row["player_id"]))

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


# =================================================================== fachada ==


def generate_postgame_ppt(
    engine, game_id: str, team_id: str, game_context: dict, *, llm_client: Optional[LLMClient] = None
) -> bytes:
    """Punto de entrada único para la página: datos + puntos destacados + `.pptx`, en bytes.

    Args:
        engine: motor de lectura (`data.db.get_read_engine()`).
        game_id: partido del que sacar el boxscore (normalmente el
            seleccionado en "Partidos anteriores", que por defecto es el
            último jugado — ver `queries.list_past_games`).
        team_id: equipo propio (`queries.get_own_team_id`) — el informe es
            "cada jugador del Baskonia", nunca del rival.
        game_context: `rival`, `resultado`, `competicion`, `fecha`, `subtitle`
            — ver cómo lo arma `app/pages/partidos_anteriores.py`.
        llm_client: `assistant.llm.build_llm_client()` ya construido, o
            `None` para saltarse el LLM y quedarse solo con las reglas.

    Raises:
        ValueError: el partido no tiene ningún jugador propio con minutos
            jugados (boxscore sin cargar todavía para este partido).
    """
    df = queries.game_player_report(engine, game_id, team_id)
    if df.empty:
        raise ValueError("Este partido no tiene boxscore de jugadores con minutos jugados todavía.")
    rows = df.to_dict("records")
    highlights = select_highlights(llm_client, rows, game_context)
    return build_postgame_ppt(rows, highlights, game_context)
