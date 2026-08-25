"""Topes de uso del asistente: por sesión, por día y por turno (§11.3).

La app está publicada en internet por el túnel de Cloudflare: cualquiera que
dé con la URL puede consumir. Con capa gratuita el riesgo no es la factura,
es **agotar la cuota diaria del proveedor y quedarte tú sin asistente** — así
que los topes existen desde el primer día aunque hoy no se pague nada. Es la
misma pieza que hará falta el día que se pague, y añadirla después de
publicar es tarde.

Superado un tope, el asistente se apaga con un mensaje claro y **el resto de
la interfaz sigue funcionando**: el chat es una pestaña más, no un requisito
de arranque.

Sobre dónde se guarda el contador diario: en la Raspberry Pi, `data/` está
montado de solo lectura (`docker-compose.yml`) precisamente para que la
interfaz no pueda escribir en la base de datos. El contador va por eso a
`ASSISTANT_STATE_DIR` (un volumen aparte, escribible), y si ese directorio no
existe o no se puede escribir, el contador degrada a memoria del proceso y lo
dice en el log en vez de tumbar la página. Un contador que se pierde al
reiniciar es peor que uno persistente, pero es infinitamente mejor que una
pestaña que no arranca.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Preguntas por sesión de navegador. Generoso para uso propio, suficiente
#: para que un visitante casual no vacíe la cuota del día.
DEFAULT_SESSION_LIMIT = 30

#: Preguntas por día en toda la instalación. Dimensionado MUY por debajo de
#: la cuota real del proveedor (1.000 peticiones/día en Groq, 10.000
#: neuronas/día en Cloudflare) porque cada pregunta son varias peticiones:
#: una vuelta de herramienta es una llamada al modelo.
DEFAULT_DAILY_LIMIT = 200

_COUNTER_FILE = "assistant_usage.json"

# Contador en memoria: respaldo cuando no hay directorio escribible.
_memory_counter = {"date": "", "count": 0}


@dataclass
class BudgetStatus:
    """Resultado de comprobar los topes antes de atender una pregunta."""

    allowed: bool
    reason: Optional[str] = None
    session_used: int = 0
    session_limit: int = DEFAULT_SESSION_LIMIT
    daily_used: int = 0
    daily_limit: int = DEFAULT_DAILY_LIMIT


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default


def session_limit() -> int:
    return _env_int("ASSISTANT_SESSION_LIMIT", DEFAULT_SESSION_LIMIT)


def daily_limit() -> int:
    return _env_int("ASSISTANT_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)


def _counter_path() -> Optional[Path]:
    """Fichero del contador diario, o `None` si no hay sitio escribible."""
    directory = os.getenv("ASSISTANT_STATE_DIR", "").strip()
    if not directory:
        return None
    path = Path(directory)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("ASSISTANT_STATE_DIR no es escribible (%s): contador diario en memoria", exc)
        return None
    return path / _COUNTER_FILE


def _read_counter(today: str) -> int:
    path = _counter_path()
    if path is None:
        return _memory_counter["count"] if _memory_counter["date"] == today else 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return int(data.get("count", 0)) if data.get("date") == today else 0


def _write_counter(today: str, count: int) -> None:
    path = _counter_path()
    if path is None:
        _memory_counter.update({"date": today, "count": count})
        return
    try:
        path.write_text(json.dumps({"date": today, "count": count}), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - depende del sistema de ficheros
        logger.warning("no se ha podido escribir el contador diario (%s)", exc)
        _memory_counter.update({"date": today, "count": count})


def check(session_used: int, *, today: Optional[date] = None) -> BudgetStatus:
    """Comprueba los topes ANTES de gastar una petición.

    Args:
        session_used: preguntas ya hechas en esta sesión de Streamlit (lo
            lleva la página en `st.session_state`).
    """
    day = (today or date.today()).isoformat()
    daily_used = _read_counter(day)
    limits = BudgetStatus(
        allowed=True,
        session_used=session_used,
        session_limit=session_limit(),
        daily_used=daily_used,
        daily_limit=daily_limit(),
    )

    if session_used >= limits.session_limit:
        limits.allowed = False
        limits.reason = (
            f"Has llegado al tope de {limits.session_limit} preguntas de esta sesión. "
            "Vacía el chat para empezar otra."
        )
    elif daily_used >= limits.daily_limit:
        limits.allowed = False
        limits.reason = (
            f"Se ha alcanzado el tope diario de {limits.daily_limit} preguntas del asistente "
            "(protege la cuota gratuita del proveedor). Vuelve mañana."
        )
    return limits


def record(*, today: Optional[date] = None) -> int:
    """Anota una pregunta atendida y devuelve el total del día."""
    day = (today or date.today()).isoformat()
    count = _read_counter(day) + 1
    _write_counter(day, count)
    return count


def reset_memory_counter() -> None:
    """Limpia el contador en memoria. Solo para pruebas."""
    _memory_counter.update({"date": "", "count": 0})
