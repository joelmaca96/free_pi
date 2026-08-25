"""Contrato común de las herramientas: contexto, registro, resultado y errores.

Vive aparte de `tools/__init__.py` (que es el registro y necesita importar
todos los módulos de herramientas) para que los módulos puedan importar el
contrato sin importar el registro que los importa a ellos.

**El contrato, en una línea**: entradas escalares y ya resueltas (ids, nunca
nombres libres — resolver es trabajo de `app/assistant/resolve.py`), salida
JSON compacta con `data` + `meta` + `artifact` opcional
(`local/features/005-chatbot/01_design.md` §4).

Dos reglas que parecen detalles y no lo son:

- **`meta` siempre lleva procedencia.** De qué tabla/vista sale y sobre
  cuántos partidos. Sin eso no es una herramienta de scouting, es un
  generador de frases (§1).
- **Una herramienta que falla devuelve una FRASE útil**, no un stack trace
  (§7.4). `{"error": "sin datos", "detail": "...", "suggestion": "..."}` hace
  que el modelo reintente con sentido en vez de rellenar el hueco.
"""
import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from ..capabilities import Capabilities

# Tope duro de filas que entra en el contexto del modelo. Un `SELECT *` sobre
# `shots` son 95.263 filas: sin tope, una sola herramienta agota la ventana
# (§11.2).
MAX_ROWS = 50

# Tipos de artefacto que la página sabe pintar (§9.3). El modelo NUNCA emite
# HTML, Vega ni markdown de tabla: marca qué resultado quiere enseñar y la
# página lo dibuja con el componente ya afinado del resto de la interfaz.
ARTIFACT_TYPES = ("table", "shot_chart", "bar", "none")


@dataclass
class ToolContext:
    """Todo lo que una herramienta necesita saber del turno, sin tocar Streamlit.

    Que esto sea un dato plano y no `st.session_state` es lo que permite
    probar cada herramienta como una función Python normal (§12.1).
    """

    engine: Engine
    season_id: int
    own_team_id: str
    today: dt.date
    capabilities: Capabilities


@dataclass(frozen=True)
class Tool:
    """Una herramienta registrada: qué se le enseña al modelo y qué ejecuta."""

    name: str
    family: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., Dict[str, Any]]
    artifact: str = "none"
    #: Nombre de la capacidad (`app/assistant/capabilities.py`) sin la cual
    #: esta herramienta NO se registra. Una herramienta que existe pero
    #: siempre falla es peor que su ausencia: sin ella el modelo ve que no
    #: puede y lo dice, con ella insiste (§4.4).
    requires: Optional[str] = None


#: Registro global, poblado por los `@register(...)` de cada módulo al
#: importarse. Es global y no una instancia porque el catálogo es estático:
#: lo que varía por base de datos es qué subconjunto se expone, y eso lo
#: decide `build_catalog` con las capacidades sondeadas.
REGISTRY: Dict[str, Tool] = {}


def register(
    name: str,
    *,
    family: str,
    description: str,
    parameters: Dict[str, Any],
    artifact: str = "none",
    requires: Optional[str] = None,
):
    """Decorador que da de alta una herramienta en `REGISTRY`.

    Raises:
        ValueError: nombre duplicado o tipo de artefacto desconocido. Los dos
            son errores de programación que conviene ver al importar y no
            tres turnos después dentro de una conversación.
    """
    if artifact not in ARTIFACT_TYPES:
        raise ValueError(f"artefacto desconocido: {artifact!r} (válidos: {ARTIFACT_TYPES})")

    def decorator(fn):
        if name in REGISTRY:
            raise ValueError(f"herramienta duplicada: {name!r}")
        REGISTRY[name] = Tool(
            name=name,
            family=family,
            description=description,
            parameters=parameters,
            fn=fn,
            artifact=artifact,
            requires=requires,
        )
        return fn

    return decorator


def schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    """JSON Schema estricto para los argumentos de una herramienta.

    `additionalProperties: false` no es cosmético: con esquemas abiertos, un
    modelo pequeño se inventa parámetros plausibles (`temporada`, `equipo`) y
    la llamada falla de una forma que parece un error de la herramienta (§8.4).
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _clean(value: Any) -> Any:
    """Valor de pandas/SQLAlchemy -> valor serializable en JSON.

    `NaN`/`NaT`/`pd.NA` se convierten en `None` a propósito: un `NaN` en el
    JSON del `tool_result` es JSON inválido en varios parsers, y —más
    importante— un hueco tiene que llegarle al modelo como hueco explícito,
    no como un número raro que pueda acabar citando.
    """
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 2)
    if isinstance(value, (pd.Timestamp, dt.date, dt.datetime)):
        return str(value)[:10]
    if hasattr(value, "item"):  # numpy scalar
        return _clean(value.item())
    if value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def records(df: pd.DataFrame, limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    """DataFrame -> lista de dicts compacta y serializable, con tope de filas."""
    if df is None or df.empty:
        return []
    return [
        {key: _clean(value) for key, value in row.items()}
        for row in df.head(limit).to_dict("records")
    ]


def clean_dict(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Igual que `records` para una fila suelta (`Optional[dict]` de `queries.py`)."""
    return None if row is None else {key: _clean(value) for key, value in row.items()}


def ok(
    data: Any,
    *,
    source: str,
    scope: Optional[str] = None,
    gp: Optional[int] = None,
    warnings: Optional[List[str]] = None,
    artifact: Optional[Dict[str, Any]] = None,
    truncated_from: Optional[int] = None,
) -> Dict[str, Any]:
    """Resultado correcto de una herramienta.

    Args:
        source: tabla o vista de la que sale el dato (`player_game_stats`,
            `team_style_percentiles`...). Es la mitad de la procedencia.
        scope: la otra mitad, en texto: partido, temporada, competición.
        gp: sobre cuántos partidos está calculado. El prompt obliga a citarlo
            ("en 43 partidos", no "esta temporada" a secas, §7.1).
        warnings: avisos que el modelo DEBE trasladar a la respuesta (rating
            estimado en Euroliga, equipo de quinteto inferido...).
        artifact: `{'type': ..., 'payload': [...]}` para que la página lo
            pinte después del texto (§9.3).
        truncated_from: total real de filas cuando se ha recortado a `MAX_ROWS`.
    """
    meta: Dict[str, Any] = {"source": source}
    if scope:
        meta["scope"] = scope
    if gp is not None:
        meta["gp"] = int(gp)
    if warnings:
        meta["warnings"] = warnings
    if truncated_from is not None:
        meta["truncated_from"] = int(truncated_from)

    result: Dict[str, Any] = {"data": data, "meta": meta}
    if artifact is not None:
        result["artifact"] = artifact
    return result


def fail(error: str, *, detail: Optional[str] = None, suggestion: Optional[str] = None) -> Dict[str, Any]:
    """Fallo de una herramienta, redactado para que el modelo pueda reaccionar (§7.4)."""
    result: Dict[str, Any] = {"error": error}
    if detail:
        result["detail"] = detail
    if suggestion:
        result["suggestion"] = suggestion
    return result


def artifact(kind: str, payload: Any, *, title: Optional[str] = None) -> Dict[str, Any]:
    """Artefacto visual asociado a un resultado (ver `app/assistant/render.py`)."""
    block: Dict[str, Any] = {"type": kind, "payload": payload}
    if title:
        block["title"] = title
    return block


@dataclass
class ToolInvocation:
    """Una llamada ejecutada, con lo que hace falta para pintar la traza (§9.2)."""

    call_id: str
    name: str
    arguments: Dict[str, Any]
    result: Dict[str, Any]
    error: bool = False
    artifact: Optional[Dict[str, Any]] = None
    summary: str = ""
    tags: List[str] = field(default_factory=list)
