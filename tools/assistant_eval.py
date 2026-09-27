"""Banco de pruebas del asistente: pasa el set dorado contra el proveedor configurado.

Es la propuesta 13 y el defecto D3 de `local/features/006-revision-asistente/01_revision.md`.
El diseño cita el set dorado tres veces —criterio de aceptación de la fase 2,
mitigación del riesgo de deriva al tocar prompt o modelo (§14) y paso 3
obligatorio del procedimiento de migración (§16.3)— y hasta hoy no existía.

POR QUÉ EXISTE, dicho con el caso real: los cuatro defectos arreglados el
2026-09-12 se validaron con UNA ejecución suelta cada uno contra el proveedor
real. Una ejecución suelta de un modelo no determinista no distingue una
mejora de una casualidad, así que aquello demostró el mecanismo (para eso
están los tests) pero no el sistema. Esto mide el sistema.

Cinco números por pregunta, que son los que pide §12.4:

    acierto de herramienta · cifras sin verificar · vueltas · latencia · tokens

CÓMO ESTÁ CONSTRUIDO, y por qué así:

- **No entra en pytest.** La suite es 100% offline (`tests/conftest.py` corta
  los sockets) y este guion llama al proveedor de verdad. Meterlo en la suite
  la volvería roja o verde según la red y según lo que hubiera al otro lado
  ese día, que es exactamente lo que ese conftest existe para impedir.
- **Aserciones deterministas primero.** Herramientas llamadas, herramientas
  NO llamadas, cifras con tolerancia y ambigüedad de `resolve_entity`. La
  única aserción sobre prosa es la lista de marcadores de rechazo, y vive en
  el JSON del set (no en este fichero) para que se vea qué se está exigiendo.
- **El juez con LLM (`--judge`) es opcional y solo puntúa redacción.** Nunca
  cifras: de eso ya se ocupa `verify.verify_numbers`, que es determinista y
  gratis. Un juez que opina sobre números añade un segundo modelo que también
  se equivoca, encima del que estás evaluando.
- **El set en JSON y no en YAML.** El repo no añade dependencias por
  comodidad; mismo criterio que la nota de "sin scipy" de
  `app/analytics/signals.py`.
- **No gasta el contador diario de `budget.py`.** Sí lo respeta: si el día ya
  está agotado no arranca. Pero una pasada del set son ~24 peticiones, y que
  evaluar el asistente sea lo que deja al entrenador sin asistente el día de
  la demo es justo el fallo que `budget.py` existe para evitar. Con
  `--count-budget` se cuenta, si algún día se quiere que cuente.

USO:

    .venv/Scripts/python.exe tools/assistant_eval.py
    .venv/Scripts/python.exe tools/assistant_eval.py --dry-run
    .venv/Scripts/python.exe tools/assistant_eval.py --only fenerbahce-estilo --repeat 3
    .venv/Scripts/python.exe tools/assistant_eval.py --env .env.cerebras --judge

COMPARAR DOS PROVEEDORES sin tocar código, que es medio motivo de que esto
exista (y el paso 3 de §16.3): cada pasada se guarda con su fecha y su
`provider_label()`, y la siguiente se compara sola contra la más reciente
anterior. Con un segundo fichero de entorno son dos órdenes:

    .venv/Scripts/python.exe tools/assistant_eval.py                      # línea base
    .venv/Scripts/python.exe tools/assistant_eval.py --env .env.local     # candidato

Los informes van a `ASSISTANT_STATE_DIR/eval/` (el mismo volumen escribible
que el contador y el feedback: en la Raspberry Pi `data/` está montado de
solo lectura).
"""
import argparse
import datetime as dt
import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

from packages.baskonia_core import config  # noqa: E402,F401  (carga el .env)


def _silence_streamlit_cache_warnings() -> None:
    """`queries.py` lleva decoradores `st.cache_data`, que fuera de Streamlit avisan por cada llamada.

    Son quince líneas de "No runtime found, using MemoryCacheStorageManager"
    antes de la primera pregunta, y esconden el aviso del presupuesto, que sí
    hay que leer. No se toca `queries.py`: el aviso es correcto, lo que no
    encaja es el contexto (aquí no hay runtime de Streamlit y da igual).

    Se hace por la API de Streamlit y no con `logging.getLogger(...).setLevel`,
    que era lo primero que probé y NO funciona: `streamlit.logger.get_logger`
    crea cada logger perezosamente y le fija el nivel global en ese momento,
    así que pisa cualquier nivel puesto antes de que el logger exista — que es
    siempre, porque estos se crean en la primera llamada cacheada.
    """
    try:
        from streamlit import logger as st_logger

        st_logger.set_log_level("error")
    except (ImportError, AttributeError):  # pragma: no cover - defensivo ante otra versión
        logging.getLogger("streamlit").setLevel(logging.ERROR)


# Se llama AQUÍ, entre los imports y a sabiendas de que rompe el orden: los
# avisos salen al DECORAR con `st.cache_data`, no al llamar a la función
# decorada, o sea durante el `import app.data.queries` de dos líneas más
# abajo. Silenciarlo dentro de `main()` llegaba tarde y no servía de nada.
_silence_streamlit_cache_warnings()

from app.assistant import budget, prompt as prompt_module  # noqa: E402
from app.assistant.agent import Agent  # noqa: E402
from app.assistant.capabilities import probe  # noqa: E402
from app.assistant.llm import LLMError, build_llm_client, provider_label  # noqa: E402
from app.assistant.tools import ToolCatalog  # noqa: E402
from app.assistant.tools.base import ToolContext  # noqa: E402
from app.assistant.verify import numbers_from_invocations, numbers_in_text  # noqa: E402
from app.data import queries  # noqa: E402
from app.data.db import get_read_engine  # noqa: E402

DEFAULT_SET = REPO_ROOT / "tools" / "assistant_golden_set.json"

#: Tolerancia al comparar una cifra exigida con lo que escribió el modelo.
#: Es la misma que usa `verify._TOLERANCE` y por el mismo motivo: el modelo
#: redondea al citar ("57.2%" -> "57%"), y exigir igualdad exacta daría por
#: fallada una respuesta correcta. Si aquella cambia, esta también.
DEFAULT_TOLERANCE = 0.55

#: Prefijo de los fallos que NO son culpa del modelo (ver `_check_numbers`).
_PIN = "[pin viejo?]"


# ---------------------------------------------------------------- resultados --


@dataclass
class Attempt:
    """Una ejecución de una pregunta: si pasa, por qué no, y los cinco números."""

    passed: bool = False
    failures: List[str] = field(default_factory=list)
    tool_ok: bool = False
    tools: List[str] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)
    iterations: int = 0
    seconds: float = 0.0
    tokens: int = 0
    cached_tokens: int = 0
    stopped_reason: Optional[str] = None
    #: El turno llegó a un tope pero redactó con lo ya consultado. Se guarda
    #: aparte de `stopped_reason` porque miden cosas distintas: uno dice que el
    #: presupuesto se quedó corto, el otro si el usuario recibió respuesta.
    wrapped_up: bool = False
    text: str = ""
    #: Cada `run_sql` del turno con el propósito que declaró el modelo. §4.6
    #: dice que ese registro es la lista priorizada de qué herramienta propia
    #: falta escribir, y hasta ahora solo vivía en el log del servidor a nivel
    #: INFO — o sea, en ningún sitio que nadie lea. Aquí queda en el informe,
    #: al lado de la pregunta que lo provocó.
    sql_escapes: List[Dict[str, Any]] = field(default_factory=list)
    judge: Optional[Dict[str, Any]] = None
    #: El proveedor falló y no hubo turno. Se distingue de un fallo de
    #: aserción a propósito: un 429 no dice nada sobre el modelo.
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pasa": self.passed,
            "fallos": self.failures,
            "acierto_herramienta": self.tool_ok,
            "herramientas": self.tools,
            "cifras_sin_verificar": self.unverified,
            "vueltas": self.iterations,
            "segundos": round(self.seconds, 2),
            "tokens": self.tokens,
            "tokens_cacheados": self.cached_tokens,
            "parada": self.stopped_reason,
            "rescatado": self.wrapped_up,
            "escapes_sql": self.sql_escapes,
            "respuesta": self.text,
            "juez": self.judge,
            "error": self.error,
        }


@dataclass
class EntryResult:
    """Todas las ejecuciones de una entrada del set (una, o `--repeat N`)."""

    entry_id: str
    question: str
    skipped: Optional[str] = None
    attempts: List[Attempt] = field(default_factory=list)

    @property
    def usable(self) -> List[Attempt]:
        """Intentos que llegaron a producir un turno (los errores de proveedor no cuentan)."""
        return [a for a in self.attempts if a.error is None]

    @property
    def passes(self) -> int:
        return sum(1 for a in self.usable if a.passed)

    def mean(self, attribute: str) -> float:
        values = [getattr(a, attribute) for a in self.usable]
        return statistics.fmean(values) if values else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.entry_id,
            "pregunta": self.question,
            "saltada": self.skipped,
            "aciertos": f"{self.passes}/{len(self.usable)}" if self.usable else "-",
            "intentos": [a.to_dict() for a in self.attempts],
        }


# -------------------------------------------------------------------- el set --


def load_set(path: Path) -> Dict[str, Any]:
    """Lee el set dorado y comprueba lo mínimo para no fallar a mitad de pasada."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"FALLO: no existe el set en {path}.")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FALLO: el set {path} no es JSON válido ({exc}).")

    entries = data.get("entries") or []
    if not entries:
        raise SystemExit(f"FALLO: el set {path} no tiene entradas.")

    ids = [entry.get("id") for entry in entries]
    if len(set(ids)) != len(ids):
        raise SystemExit("FALLO: hay ids repetidos en el set; el informe no sería comparable.")
    for entry in entries:
        if not entry.get("id") or not entry.get("question"):
            raise SystemExit(f"FALLO: entrada sin 'id' o sin 'question': {entry!r}")
    return data


def validate_against_catalog(entries: List[dict], catalog: ToolCatalog) -> List[str]:
    """Comprueba que las herramientas nombradas existen EN ESTA base de datos.

    Esta validación no está en pytest y no puede estarlo: el catálogo depende
    del sondeo de capacidades (§7.3), o sea de qué tiene la base de datos real,
    y la suite no la toca. Vive aquí y en `--dry-run`, que es donde hay una
    base de datos de verdad delante.

    Un nombre mal escrito en `expect_tools` no falla ruidosamente: convierte la
    entrada en un rojo permanente que parece culpa del modelo.
    """
    problems: List[str] = []
    known = set(catalog.tools)
    for entry in entries:
        for key in ("expect_tools", "forbid_tools"):
            for name in entry.get(key) or []:
                if name not in known:
                    problems.append(
                        f"{entry['id']}: {key} nombra {name!r}, que no está en el catálogo "
                        "de esta base de datos"
                    )
    return problems


def entry_skip_reason(entry: dict, capabilities) -> Optional[str]:
    """Por qué esta entrada no se puede juzgar hoy, si es que no se puede.

    Las capacidades de la base de datos cambian con la ingesta, y dos entradas
    del set dependen de ello en direcciones opuestas: la del quinteto en el
    clutch solo tiene sentido CON `lineup_stints` (sin él vuelve a ser un caso
    de rechazo, como en §2.3), y la de la crónica solo tiene sentido SIN
    `key_events`. Saltarlas con explicación es lo correcto: ponerlas rojas
    castigaría al set por que la base de datos haya mejorado, y ese rojo se
    acabaría ignorando como se ignora cualquier rojo que no significa nada.
    """
    flags = capabilities.to_dict()
    for name in entry.get("requires_capabilities_on") or []:
        if not flags.get(name):
            return f"requiere la capacidad {name!r}, que esta base de datos no tiene"
    for name in entry.get("requires_capabilities_off") or []:
        if flags.get(name):
            return (
                f"la capacidad {name!r} ya existe en esta base de datos: la pregunta "
                "ha dejado de ser un caso de rechazo y la entrada hay que reescribirla"
            )
    return None


# --------------------------------------------------------------- aserciones --


def _check_tools(entry: dict, tools: List[str]) -> List[str]:
    """Acierto de herramienta: las que debían salir y las que no debían."""
    failures = []
    for name in entry.get("expect_tools") or []:
        if name not in tools:
            failures.append(f"no llamó a {name}")
    for name in entry.get("forbid_tools") or []:
        if name in tools:
            failures.append(f"llamó a {name}, que esta pregunta no debía necesitar")
    return failures


def _check_ambiguity(entry: dict, invocations) -> List[str]:
    """`resolve_entity` marcó (o no) ambigüedad, que es lo que decide si pregunta o responde."""
    expected = entry.get("expect_ambiguous")
    if expected is None:
        return []
    flags = [
        bool((inv.result.get("data") or {}).get("ambiguous"))
        for inv in invocations
        if inv.name == "resolve_entity" and not inv.error
    ]
    if not flags:
        return ["no hay ningún resolve_entity con resultado del que leer ambiguous"]
    actual = any(flags)
    if actual != bool(expected):
        return [f"resolve_entity devolvió ambiguous={actual} y se esperaba {bool(expected)}"]
    return []


def _check_numbers(entry: dict, text: str, invocations) -> List[str]:
    """Las cifras exigidas aparecen en la respuesta, con tolerancia de redondeo.

    Se distinguen DOS fallos distintos, y la diferencia es la que hace que este
    guion se pueda seguir usando después de una reingesta: si la cifra exigida
    no aparece en la respuesta pero tampoco en NINGÚN `tool_result`, el que
    está mal es el set, no el modelo — el dato ha cambiado bajo los pies del
    pin. Sin esta distinción, reingerir pondría medio set en rojo y el rojo
    dejaría de significar nada.
    """
    if not entry.get("expect_numbers"):
        return []
    in_answer = numbers_in_text(text)
    in_tools = numbers_from_invocations(invocations)
    failures = []
    for spec in entry["expect_numbers"]:
        value = float(spec["valor"])
        tolerance = float(spec.get("tolerancia", DEFAULT_TOLERANCE))
        what = spec.get("es", "")
        if any(abs(value - found) <= tolerance for found in in_answer):
            continue
        if not any(abs(value - found) <= tolerance for found in in_tools):
            failures.append(
                f"{_PIN} {value} ({what}) no está en la respuesta NI en ningún tool_result: "
                f"revisa el pin de la entrada ({(entry.get('fijado') or {}).get('recalcular', '-')})"
            )
        else:
            failures.append(f"la respuesta no cita {value} ({what}), que sí estaba en el dato")
    return failures


def _check_refusal(entry: dict, text: str) -> List[str]:
    """La respuesta rechaza la pregunta.

    Única aserción de prosa del set, y por eso es una lista de marcadores
    alternativos declarada en la propia entrada: exigir una frase exacta sería
    evaluar redacción disfrazado de evaluar comportamiento, que es justo lo que
    §12.4 prohíbe ("aserciones sobre qué herramientas se eligen y qué cifras
    aparecen, nunca sobre la prosa exacta").
    """
    refusal = entry.get("expect_refusal")
    if not refusal:
        return []
    lowered = (text or "").lower()
    markers = refusal.get("marcadores") or []
    if any(marker.lower() in lowered for marker in markers):
        return []
    return ["la respuesta no rechaza la pregunta (ningún marcador de rechazo en el texto)"]


def evaluate(entry: dict, turn, seconds: float) -> Attempt:
    """Convierte un turno en los cinco números y en la lista de fallos."""
    tools = [inv.name for inv in turn.invocations]
    tool_failures = _check_tools(entry, tools)
    failures = list(tool_failures)
    failures += _check_ambiguity(entry, turn.invocations)
    failures += _check_numbers(entry, turn.text, turn.invocations)
    failures += _check_refusal(entry, turn.text)

    max_unverified = int(entry.get("max_unverified", 0))
    if len(turn.unverified_numbers) > max_unverified:
        failures.append(
            f"{len(turn.unverified_numbers)} cifras sin verificar "
            f"({', '.join(turn.unverified_numbers)}), el máximo es {max_unverified}"
        )
    if turn.stopped_reason and not turn.wrapped_up:
        # Un turno cortado por un tope y sin rescatar no ha contestado: lo que
        # el usuario ve es el mensaje de "me he quedado sin vueltas". Cuenta
        # como fallo aunque las herramientas que llegó a llamar fueran las
        # correctas.
        #
        # Si `wrapped_up` está puesto, el agente sí redactó con lo ya
        # consultado (ver `Agent._wrap_up`) y la respuesta se juzga como
        # cualquier otra: por sus herramientas y por sus cifras. El tope
        # alcanzado no desaparece del informe —sale en la columna de parada—,
        # pero deja de ser un suspenso automático, porque el usuario recibió
        # una respuesta.
        failures.append(f"turno cortado por {turn.stopped_reason}")

    return Attempt(
        passed=not failures,
        failures=failures,
        tool_ok=not tool_failures,
        tools=tools,
        unverified=list(turn.unverified_numbers),
        iterations=turn.iterations,
        seconds=seconds,
        tokens=int(turn.usage.get("total_tokens", 0) or 0),
        cached_tokens=int(
            turn.usage.get("cached_tokens") or turn.usage.get("cache_read_input_tokens") or 0
        ),
        stopped_reason=turn.stopped_reason,
        wrapped_up=turn.wrapped_up,
        text=turn.text,
        sql_escapes=[
            {
                "purpose": inv.arguments.get("purpose"),
                "sql": inv.arguments.get("sql"),
                "error": inv.error,
            }
            for inv in turn.invocations
            if inv.name == "run_sql"
        ],
    )


# -------------------------------------------------------- juez de redacción --

_JUDGE_SYSTEM = """\
Eres el revisor de estilo del asistente de scouting del Baskonia. Puntúas SOLO la REDACCIÓN.

NO juzgues si las cifras son correctas: eso ya lo comprueba un verificador determinista
contra los resultados de herramienta, y tú no tienes acceso a ellos. Da por buenos todos los
números que veas.

Criterios, que son las reglas de redacción del asistente:
1. De 2 a 5 frases, tono directo, sin floritura.
2. Ningún adjetivo de rendimiento sin su número y su referencia al lado.
3. Cita el número de partidos de cualquier agregado.
4. Si no puede contestar algo, lo dice y ofrece la pregunta adyacente que sí puede.

Responde SOLO con un objeto JSON: {"nota": 1-5, "motivo": "una frase"}."""


def judge_answer(client, question: str, answer: str) -> Dict[str, Any]:
    """Nota de redacción de 1 a 5, con el proveedor configurado y sin herramientas.

    Opcional (`--judge`) y deliberadamente aparte del resto: su nota NO entra
    en `passed`. Un juez con LLM que decide si una pregunta pasa o falla mete
    un segundo modelo no determinista encima del que estás midiendo, y
    entonces ya no sabes cuál de los dos se movió.
    """
    messages = [
        {
            "role": "user",
            "content": f"PREGUNTA DEL USUARIO:\n{question}\n\nRESPUESTA DEL ASISTENTE:\n{answer}",
        }
    ]
    try:
        response = client.chat(messages, [], system=_JUDGE_SYSTEM)
    except LLMError as exc:
        return {"error": str(exc)}
    raw = (response.text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return {"nota": parsed.get("nota"), "motivo": parsed.get("motivo")}
        except json.JSONDecodeError:
            pass
    return {"error": f"el juez no devolvió JSON: {raw[:120]!r}"}


# ------------------------------------------------------------------ informe --


def _fmt_seconds(value: float) -> str:
    if value < 60:
        return f"{value:.1f}s"
    return f"{int(value // 60)}m {value % 60:.0f}s"


def render_table(results: List[EntryResult], repeat: int) -> str:
    """La tabla de §12.4: cinco números por pregunta, en ASCII.

    Sin caracteres de dibujo ni emoji a propósito: esta tabla se pega en
    documentos y se lee en consolas Windows en cp1252, donde un carácter
    fuera de esa codificación revienta la salida entera (mismo motivo que el
    comentario de `tools.summarize`).
    """
    header = (
        f"{'ENTRADA':<28} {'ESTADO':>7} {'HERR':>5} {'SINVER':>7} "
        f"{'VUELTAS':>8} {'LATENCIA':>9} {'TOKENS':>8}"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        if result.skipped:
            lines.append(f"{result.entry_id:<28} {'SALTADA':>7}   {result.skipped[:60]}")
            continue
        if not result.usable:
            error = result.attempts[0].error if result.attempts else "sin intentos"
            lines.append(f"{result.entry_id:<28} {'ERROR':>7}   {str(error)[:60]}")
            continue
        state = f"{result.passes}/{len(result.usable)}" if repeat > 1 else (
            "PASA" if result.passes else "FALLA"
        )
        tool_ok = sum(1 for a in result.usable if a.tool_ok)
        lines.append(
            f"{result.entry_id:<28} {state:>7} "
            f"{f'{tool_ok}/{len(result.usable)}':>5} "
            f"{_mean_unverified(result):>7.1f} "
            f"{result.mean('iterations'):>8.1f} "
            f"{_fmt_seconds(result.mean('seconds')):>9} "
            f"{result.mean('tokens'):>8,.0f}"
        )
    return "\n".join(lines)


def _mean_unverified(result: EntryResult) -> float:
    """Media de cifras sin verificar por intento."""
    values = [len(a.unverified) for a in result.usable]
    return statistics.fmean(values) if values else 0.0


def aggregates(results: List[EntryResult]) -> Dict[str, Any]:
    """Los mismos cinco números, agregados. Es lo que se compara entre pasadas."""
    judged = [r for r in results if r.usable]
    attempts = [a for r in judged for a in r.usable]
    if not attempts:
        return {"entradas": 0}
    return {
        "entradas": len(judged),
        "saltadas": sum(1 for r in results if r.skipped),
        "intentos": len(attempts),
        "aciertos": sum(1 for a in attempts if a.passed),
        "acierto_pct": round(100 * sum(1 for a in attempts if a.passed) / len(attempts), 1),
        "acierto_herramienta_pct": round(
            100 * sum(1 for a in attempts if a.tool_ok) / len(attempts), 1
        ),
        "cifras_sin_verificar_total": sum(len(a.unverified) for a in attempts),
        "cifras_sin_verificar_media": round(
            statistics.fmean([len(a.unverified) for a in attempts]), 2
        ),
        "vueltas_media": round(statistics.fmean([a.iterations for a in attempts]), 2),
        "vueltas_max": max(a.iterations for a in attempts),
        "latencia_media_s": round(statistics.fmean([a.seconds for a in attempts]), 2),
        "latencia_total_s": round(sum(a.seconds for a in attempts), 1),
        "tokens_total": sum(a.tokens for a in attempts),
        "tokens_media": round(statistics.fmean([a.tokens for a in attempts])),
        "tokens_cacheados_total": sum(a.cached_tokens for a in attempts),
    }


def render_aggregates(totals: Dict[str, Any]) -> str:
    if not totals.get("entradas"):
        return "AGREGADOS: ninguna entrada evaluable."
    return "\n".join(
        [
            f"AGREGADOS ({totals['entradas']} entradas evaluadas, "
            f"{totals['saltadas']} saltadas, {totals['intentos']} intentos)",
            f"  aciertos              {totals['aciertos']}/{totals['intentos']} "
            f"({totals['acierto_pct']}%)   [criterio de la fase 2: >=90%]",
            f"  acierto de herram.    {totals['acierto_herramienta_pct']}%",
            f"  cifras sin verificar  {totals['cifras_sin_verificar_media']} por pregunta "
            f"({totals['cifras_sin_verificar_total']} en total)",
            f"  vueltas               {totals['vueltas_media']} de media "
            f"(máx {totals['vueltas_max']})",
            f"  latencia              {_fmt_seconds(totals['latencia_media_s'])} de media "
            f"(total {_fmt_seconds(totals['latencia_total_s'])})",
            f"  tokens                {totals['tokens_total']:,} en total "
            f"({totals['tokens_media']:,} por pregunta, "
            f"{totals['tokens_cacheados_total']:,} servidos de caché)",
        ]
    )


def render_comparison(totals: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    """Comparación con la pasada anterior. Sin esto no se sabe si has migrado o empeorado (§16.3)."""
    previous = baseline.get("agregados") or {}
    if not previous.get("entradas"):
        return ""
    lines = [
        f"COMPARACIÓN con {baseline.get('generado', '?')} · {baseline.get('proveedor', '?')}",
    ]
    for label, key, unit, better_up in [
        ("aciertos", "acierto_pct", "%", True),
        ("acierto de herram.", "acierto_herramienta_pct", "%", True),
        ("cifras sin verificar", "cifras_sin_verificar_media", "", False),
        ("vueltas", "vueltas_media", "", False),
        ("latencia", "latencia_media_s", "s", False),
        ("tokens por pregunta", "tokens_media", "", False),
    ]:
        before, now = previous.get(key), totals.get(key)
        if before is None or now is None:
            continue
        delta = now - before
        # El signo no basta: menos tokens es mejor y menos aciertos es peor.
        mark = "=" if abs(delta) < 1e-9 else ("mejor" if (delta > 0) == better_up else "PEOR")
        lines.append(f"  {label:<22} {before:>10,.2f}{unit} -> {now:>10,.2f}{unit}  {mark}")
    return "\n".join(lines)


# ------------------------------------------------------------------ guardado --


def report_dir(explicit: Optional[str]) -> Optional[Path]:
    """Dónde se guardan los informes, con la misma degradación que `budget.py`.

    En la Raspberry Pi `data/` está montado de solo lectura, así que el sitio
    canónico es `ASSISTANT_STATE_DIR`. Si no hay ninguno escribible se devuelve
    `None` y la pasada se imprime igual: perder el fichero es molesto, perder
    una pasada entera contra el proveedor es caro.
    """
    raw = explicit or os.getenv("ASSISTANT_STATE_DIR", "").strip() or "data/assistant_state"
    path = Path(raw)
    if explicit is None:
        path = path / "eval"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"AVISO: no se puede escribir en {path} ({exc}); el informe no se guarda.")
        return None
    return path


def _slug(label: str) -> str:
    keep = [c if c.isalnum() else "-" for c in label.lower()]
    return "".join(keep).strip("-").replace("---", "-").replace("--", "-")[:60]


def latest_report(directory: Path, entry_ids: List[str]) -> Optional[Dict[str, Any]]:
    """El informe más reciente que midió LAS MISMAS entradas, para comparar con él.

    Lo de "las mismas" no es un detalle: la primera versión cogía el último
    informe a secas, y bastó una pasada de comprobación con `--only
    maximo-anotador-acb` para que la siguiente pasada completa se comparase
    contra un informe de una sola pregunta —la única que fallaba— y anunciase
    una mejora del 0% al 62%. Una comparación entre conjuntos distintos es
    peor que no comparar: parece un dato y es un espejismo.
    """
    wanted = set(entry_ids)
    for path in sorted(directory.glob("eval_*.json"), reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if {r.get("id") for r in report.get("resultados") or []} == wanted:
            return report
    return None


# --------------------------------------------------------------------- main --


def build_agent(client, engine, today: dt.date):
    """Monta el agente EXACTAMENTE como lo monta `app/screens/asistente.py`.

    Si esto se separa de la pantalla, el set dorado deja de medir el asistente
    y pasa a medir una copia suya: sondeo de capacidades, catálogo completo,
    prompt de sistema con su bloque volátil al final, mismos topes de turno.
    """
    capabilities = probe(engine)
    own_team_id = queries.get_own_team_id(engine)
    seasons = queries.list_seasons(engine)
    # La misma preselección que `Home.py`: la temporada más reciente CON
    # partidos jugados, no la más reciente a secas (en cuanto la ingesta carga
    # el calendario siguiente hay una temporada con cero partidos por delante,
    # y evaluar contra ella daría ocho "no tengo datos" seguidos).
    with_games = seasons[seasons["games"] > 0]
    season = (with_games if not with_games.empty else seasons).iloc[0]
    season_id, season_label = int(season["id"]), str(season["label"])
    own_team_name = queries.team_name(engine, own_team_id) or own_team_id

    system_prompt = prompt_module.build_system_prompt(
        capabilities,
        season_label=season_label,
        own_team=own_team_name,
        today=today.isoformat(),
    )
    catalog = ToolCatalog(
        ToolContext(
            engine=engine,
            season_id=season_id,
            own_team_id=own_team_id,
            today=today,
            capabilities=capabilities,
        )
    )
    context = {
        "temporada": season_label,
        "equipo_propio": own_team_name,
        "partidos": capabilities.counts.get("games", 0),
        "herramientas": len(catalog.tools),
    }
    return Agent(client, catalog, system_prompt), catalog, capabilities, context


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--set", dest="set_path", default=str(DEFAULT_SET), help="Fichero del set dorado.")
    parser.add_argument("--only", action="append", help="Pasa solo estas entradas (repetible).")
    parser.add_argument("--repeat", type=int, default=1, help="Ejecuciones por pregunta (el modelo no es determinista).")
    parser.add_argument(
        "--env",
        help="Fichero .env adicional que se carga ENCIMA del actual. Es lo que permite "
        "pasar el mismo set contra otro proveedor sin tocar código (§16.3, paso 3).",
    )
    parser.add_argument("--judge", action="store_true", help="Añade la nota de redacción con LLM (solo prosa, nunca cifras).")
    parser.add_argument("--out", help="Directorio del informe (por defecto ASSISTANT_STATE_DIR/eval).")
    parser.add_argument("--baseline", help="Informe con el que comparar (por defecto, el último de --out).")
    parser.add_argument("--no-compare", action="store_true", help="No compara con ninguna pasada anterior.")
    parser.add_argument(
        "--count-budget",
        action="store_true",
        help="Cuenta cada pregunta en el contador diario de budget.py. Por defecto NO se "
        "cuenta: evaluar no debe dejar sin asistente al que lo usa.",
    )
    parser.add_argument("--force", action="store_true", help="Arranca aunque el tope diario ya esté agotado.")
    parser.add_argument("--dry-run", action="store_true", help="Valida el set contra el catálogo y no llama al proveedor.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Muestra las líneas assistant.timing del agente.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    if args.env:
        # `override=True` a propósito: la gracia es tapar lo que ya hay en
        # `.env` (mismo modelo, otro proveedor) sin editarlo.
        if not Path(args.env).exists():
            print(f"FALLO: no existe {args.env}.")
            return 2
        load_dotenv(args.env, override=True)

    data = load_set(Path(args.set_path))
    entries = data["entries"]
    if args.only:
        wanted = set(args.only)
        entries = [e for e in entries if e["id"] in wanted]
        missing = wanted - {e["id"] for e in entries}
        if missing:
            print(f"FALLO: no hay entrada con id {', '.join(sorted(missing))}.")
            return 2

    today = dt.date.today()
    try:
        engine = get_read_engine()
    except RuntimeError as exc:
        print(f"FALLO: {exc}")
        return 2

    client = None
    if not args.dry_run:
        try:
            client = build_llm_client()
        except LLMError as exc:
            print(f"FALLO: {exc}")
            return 2
        if client is None:
            print(
                "FALLO: no hay proveedor configurado (ver .env.example). Hace falta "
                "ASSISTANT_LLM_MODEL, más ASSISTANT_LLM_BASE_URL con el proveedor "
                "openai_compat o ASSISTANT_LLM_API_KEY con el proveedor anthropic."
            )
            return 2

    agent, catalog, capabilities, context = build_agent(client, engine, today)

    label = provider_label()
    print(f"Proveedor:  {label}")
    print(f"Set:        {args.set_path} (v{data.get('version', '?')}, {len(entries)} entradas)")
    print(
        f"Contexto:   {context['equipo_propio']} · temporada {context['temporada']} · "
        f"{context['partidos']} partidos · {context['herramientas']} herramientas"
    )

    problems = validate_against_catalog(entries, catalog)
    if problems:
        print("\nFALLO: el set nombra herramientas que no existen en esta base de datos:")
        for problem in problems:
            print(f"  - {problem}")
        return 2

    # Topes (§11.3): se RESPETAN pero no se gastan. Ver el docstring del módulo.
    status = budget.check(0, today=today)
    planned = len(entries) * max(1, args.repeat)
    print(
        f"Presupuesto: {status.daily_used}/{status.daily_limit} preguntas hoy; esta pasada son "
        f"{planned} y {'SÍ' if args.count_budget else 'NO'} cuentan para el tope diario."
    )
    if not status.allowed and not args.dry_run:
        print(f"FALLO: {status.reason}")
        if not args.force:
            print("(--force pasa el set igualmente, si lo que te importa es medir y no la cuota.)")
            return 2
    if status.daily_used + planned > status.daily_limit and args.count_budget:
        print(
            "AVISO: contando esta pasada se supera el tope diario y el asistente quedaría "
            "apagado para el resto del día. Quita --count-budget si no era la intención."
        )

    if args.dry_run:
        print("\n--dry-run: el set es válido contra este catálogo. Entradas:")
        for entry in entries:
            skip = entry_skip_reason(entry, capabilities)
            mark = f"SALTADA ({skip})" if skip else "lista"
            print(f"  {entry['id']:<28} {mark}")
            print(f"    {entry['question'][:100]}")
        return 0

    results: List[EntryResult] = []
    started = time.monotonic()
    for index, entry in enumerate(entries, start=1):
        result = EntryResult(entry_id=entry["id"], question=entry["question"])
        skip = entry_skip_reason(entry, capabilities)
        if skip:
            result.skipped = skip
            results.append(result)
            print(f"\n[{index}/{len(entries)}] {entry['id']}: SALTADA — {skip}")
            continue

        print(f"\n[{index}/{len(entries)}] {entry['id']}")
        for attempt_number in range(1, max(1, args.repeat) + 1):
            # Historial vacío en cada intento: cada entrada del set es una
            # pregunta en frío. Encadenarlas haría que el acierto de la tercera
            # dependiese de lo que quedó en el contexto de la primera, y
            # entonces el set mediría el orden del fichero.
            turn_started = time.monotonic()
            try:
                turn = agent.run(entry["question"], [])
            except LLMError as exc:
                result.attempts.append(Attempt(error=str(exc)))
                print(f"  intento {attempt_number}: ERROR DE PROVEEDOR — {exc}")
                continue
            elapsed = time.monotonic() - turn_started
            attempt = evaluate(entry, turn, elapsed)
            if args.judge and turn.text:
                attempt.judge = judge_answer(client, entry["question"], turn.text)
            result.attempts.append(attempt)
            state = "PASA" if attempt.passed else "FALLA"
            # El rescate se dice SIEMPRE, aunque la entrada pase: un turno que
            # llega al tope y se salva contestando sigue siendo un turno con el
            # presupuesto justo, y eso es lo que avisa antes de que la próxima
            # pregunta un poco más larga no se salve.
            rescued = " · RESCATADO (tope alcanzado)" if attempt.wrapped_up else ""
            print(
                f"  intento {attempt_number}: {state} · {len(attempt.tools)} llamadas "
                f"({', '.join(attempt.tools) or 'ninguna'}) · {attempt.iterations} vueltas · "
                f"{_fmt_seconds(attempt.seconds)} · {attempt.tokens:,} tokens{rescued}"
            )
            for failure in attempt.failures:
                print(f"      - {failure}")
            for escape in attempt.sql_escapes:
                # Se imprime SIEMPRE, pase o falle la entrada: un run_sql que
                # acierta también es una herramienta que falta (§4.6).
                print(f"      run_sql: {escape['purpose']!r}")
            if args.count_budget:
                budget.record(today=today)
        results.append(result)

    totals = aggregates(results)
    print("\n" + "=" * 78)
    print(render_table(results, max(1, args.repeat)))
    print()
    print(render_aggregates(totals))

    rescued = [
        (r.entry_id, sum(1 for a in r.usable if a.wrapped_up))
        for r in results
        if any(a.wrapped_up for a in r.usable)
    ]
    if rescued:
        print()
        print(
            "TURNOS RESCATADOS (llegaron al tope y redactaron con lo ya consultado). "
            "Cuentan como acierto si la respuesta es buena, pero avisan de presupuesto justo:"
        )
        for entry_id, count in rescued:
            total = len(next(r for r in results if r.entry_id == entry_id).usable)
            print(f"  - {entry_id}: {count} de {total}")

    pins = sorted(
        {f for r in results for a in r.usable for f in a.failures if f.startswith(_PIN)}
    )
    if pins:
        print(
            "\nCIFRAS QUE NO ESTABAN EN NINGÚN tool_result — esto es el set desactualizado, "
            "no el modelo mintiendo. Rehaz el pin de estas entradas:"
        )
        for pin in pins:
            print(f"  - {pin}")

    report = {
        "generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "proveedor": label,
        "set": {"fichero": str(args.set_path), "version": data.get("version"), "entradas": len(entries)},
        "repeticiones": max(1, args.repeat),
        "contexto": context,
        "juez": bool(args.judge),
        "agregados": totals,
        "resultados": [r.to_dict() for r in results],
        "duracion_s": round(time.monotonic() - started, 1),
    }

    directory = report_dir(args.out)
    baseline = None
    if directory is not None:
        if args.baseline:
            try:
                baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"AVISO: no se ha podido leer la línea base {args.baseline} ({exc}).")
        elif not args.no_compare:
            # Se busca ANTES de escribir la de hoy, para no compararse consigo misma.
            baseline = latest_report(directory, [e["id"] for e in entries])

        # Con segundos: dos pasadas cortas (`--only`) en el mismo minuto se
        # pisarían el fichero, y perder la línea base es perder la mitad del
        # valor de esto. El nombre ordena cronológicamente al ordenar por
        # texto, que es de lo que se fía `latest_report`.
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = directory / f"eval_{stamp}_{_slug(label)}.json"
        try:
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nInforme guardado en {path}")
        except OSError as exc:
            print(f"\nAVISO: no se ha podido guardar el informe ({exc}).")

    if baseline:
        comparison = render_comparison(totals, baseline)
        if comparison:
            print("\n" + comparison)

    # Código de salida: 0 solo si pasa el set entero. Así esto sirve de puerta
    # en el paso 3 de §16.3 sin que nadie tenga que leer la tabla.
    if not totals.get("intentos"):
        # Todo saltado o todo con error de proveedor. Devolver 0 aquí sería
        # decir "el set pasa" cuando el set no ha llegado a ejecutarse, que es
        # el peor verde posible en una puerta de migración.
        print("\nNinguna entrada llegó a evaluarse.")
        return 1
    failed = totals.get("intentos", 0) - totals.get("aciertos", 0)
    errors = sum(1 for r in results for a in r.attempts if a.error)
    if errors:
        print(f"\n{errors} intentos no llegaron a ejecutarse por un fallo del proveedor.")
    return 0 if failed == 0 and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
