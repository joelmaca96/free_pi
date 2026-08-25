"""Verificador determinista de cifras: ¿cada número de la respuesta sale de algún dato?

Post-proceso barato y sin modelo (§7.2): se extraen los números de la
respuesta con una expresión regular y se comprueba que cada uno aparece en
algún `tool_result` de ESTE turno, con tolerancia de redondeo.

Dos decisiones deliberadas:

- **No bloquea la respuesta, la marca.** Bloquear convertiría un falso
  positivo (un número que el verificador no sabe casar) en una respuesta
  perdida. Marcarla deja la decisión en quien lee, que es quien puede
  juzgarla.
- **El registro es la métrica de calidad del sistema a lo largo del tiempo.**
  Si la proporción de cifras sin verificar sube al cambiar de modelo o de
  prompt, eso se ve aquí antes que en ninguna otra parte (§13, fase 5).

Con capa gratuita y modelos pequeños esto pasa de conveniencia a red de
seguridad imprescindible (§8.4).
"""
import re
from typing import Any, Iterable, List, Set

# Números de la respuesta: enteros y decimales, con coma o punto. Se admite
# el signo para no perder un "+2.1 de net rating", que es exactamente el tipo
# de cifra que interesa comprobar. La mirada atrás evita trocear lo que no es
# un número suelto: `acb-104714` daría "104" y "714", y un identificador
# marcado como "cifra sin verificar" es ruido puro.
_NUMBER_RE = re.compile(r"(?<![\w./,-])[-+]?\d{1,3}(?:[.,]\d+)?(?![\w/])")

# Fechas ISO y etiquetas de temporada se quitan ANTES de buscar números: no
# son cifras que verificar y se trocean fatal (2026-05-03 -> 202, 6, 05, 03).
_DATE_RE = re.compile(r"\b\d{4}[-/]\d{2,4}(?:[-/]\d{2})?\b")

# Números que no vale la pena comprobar: aparecen en cualquier texto ("los 5
# jugadores", "el segundo cuarto") y perseguirlos llena el aviso de ruido
# hasta que deja de leerse.
_IGNORED = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 100.0}

# Tolerancia al comparar. El modelo redondea al citar ("57.2%" -> "57%"), y
# exigir igualdad exacta marcaría como inventada una cifra correcta.
_TOLERANCE = 0.55


def _to_float(raw: str):
    try:
        return float(raw.replace(",", "."))
    except ValueError:  # pragma: no cover - la regex no deja pasar otra cosa
        return None


def _numbers_in(value: Any, seen: Set[float], depth: int = 0) -> None:
    """Recoge recursivamente todos los números de un resultado de herramienta."""
    if depth > 6:  # pragma: no cover - defensivo ante estructuras raras
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        seen.add(round(float(value), 2))
    elif isinstance(value, str):
        for match in _NUMBER_RE.findall(value):
            number = _to_float(match)
            if number is not None:
                seen.add(round(number, 2))
    elif isinstance(value, dict):
        for item in value.values():
            _numbers_in(item, seen, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _numbers_in(item, seen, depth + 1)


def numbers_from_invocations(invocations: Iterable) -> Set[float]:
    """Todos los números que las herramientas devolvieron en este turno.

    Se incluyen también los derivados obvios (el porcentaje de una fracción
    que ya está en el dato, el total de una suma) no: si el modelo calcula,
    la cifra sale sin verificar y eso es correcto — un cálculo del modelo es
    exactamente lo que hay que poder detectar.
    """
    seen: Set[float] = set()
    for invocation in invocations:
        _numbers_in(getattr(invocation, "result", invocation), seen)
    return seen


def verify_numbers(text: str, invocations: Iterable) -> List[str]:
    """Cifras del texto que no aparecen en ningún resultado de herramienta.

    Returns:
        Lista de las cifras tal y como estaban escritas en la respuesta, sin
        repetir y en orden de aparición. Vacía si todo cuadra (o si la
        respuesta no lleva cifras).
    """
    if not text:
        return []

    available = numbers_from_invocations(invocations)
    unverified: List[str] = []
    for match in _NUMBER_RE.findall(_DATE_RE.sub(" ", text)):
        number = _to_float(match)
        if number is None or abs(number) in _IGNORED:
            continue
        if any(abs(number - candidate) <= _TOLERANCE for candidate in available):
            continue
        if match not in unverified:
            unverified.append(match)
    return unverified
