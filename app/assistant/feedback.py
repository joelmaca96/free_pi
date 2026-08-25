"""Pulgar arriba/abajo por respuesta, a fichero local. Es el corpus de evaluación gratis.

El set de preguntas doradas (§12.4) tiene que salir de algún sitio, y las
preguntas que se le ocurren a quien escribió el sistema no son las que hace
quien lo usa. Cada pulgar abajo es una pregunta real que salió mal, con su
traza de herramientas al lado: es exactamente el material con el que se
amplía el corpus (§13, fase 5).

Se guarda en JSON Lines (una línea por voto, se añade y no se reescribe) en
`ASSISTANT_STATE_DIR`, el mismo volumen escribible que el contador de
`budget.py` — `data/` está montado de solo lectura en la Raspberry Pi. Si no
hay sitio donde escribir, el voto se descarta con un aviso en el log: perder
un voto es molesto, tumbar la página por un voto es inaceptable.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_FEEDBACK_FILE = "assistant_feedback.jsonl"


def _feedback_path() -> Optional[Path]:
    directory = os.getenv("ASSISTANT_STATE_DIR", "").strip()
    if not directory:
        return None
    path = Path(directory)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("ASSISTANT_STATE_DIR no es escribible (%s): no se guarda el feedback", exc)
        return None
    return path / _FEEDBACK_FILE


def record(
    *,
    question: str,
    answer: str,
    helpful: bool,
    tools: List[str],
    unverified_numbers: Optional[List[str]] = None,
    provider: Optional[str] = None,
) -> bool:
    """Guarda un voto. Devuelve `False` si no había dónde escribirlo.

    Args:
        tools: nombres de las herramientas que se usaron, en orden. Es la
            mitad útil del registro: saber que una respuesta estuvo mal
            importa menos que saber con qué herramientas se construyó.
        provider: proveedor y modelo (`llm.provider_label()`), para poder
            comparar modelos con datos y no de oído (§12.4).
    """
    path = _feedback_path()
    if path is None:
        return False

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "helpful": bool(helpful),
        "question": question,
        "answer": answer,
        "tools": tools,
        "unverified_numbers": unverified_numbers or [],
        "provider": provider,
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover - depende del sistema de ficheros
        logger.warning("no se ha podido guardar el feedback (%s)", exc)
        return False
    return True
