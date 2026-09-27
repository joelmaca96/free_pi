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

# Minutaje en notación de reloj (`20:28`), que es como se escribe el tiempo de
# juego en baloncesto y como lo escribe el modelo sin que nadie se lo pida.
# Medido en vivo: con "21 puntos en 20:28" la regex de números lo partía en
# dos y marcaba el "28" de los segundos como cifra sin verificar, con la
# respuesta entera correcta. Un falso positivo aquí es caro de una forma
# particular — el aviso de cifras sin verificar ES el mecanismo de confianza
# del asistente, y uno que salta sin motivo enseña a ignorar también los que
# sí lo tienen.
#
# Se comprueba ENTERO, convertido a minutos decimales, no por trozos: así un
# minutaje inventado sigue saltando. Los segundos se acotan a `[0-5]\d` para
# no tragarse un marcador escrito a la europea ("94:88").
_CLOCK_RE = re.compile(r"(?<![\w:.,])(\d{1,3}):([0-5]\d)(?![\d:])")

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


def _clock_minutes(match) -> float:
    """`20:28` -> 20.47 minutos, que es como lo guarda `player_game_stats.minutes`."""
    return int(match.group(1)) + int(match.group(2)) / 60.0


def _numbers_in(value: Any, seen: Set[float], depth: int = 0) -> None:
    """Recoge recursivamente todos los números de un resultado de herramienta."""
    if depth > 6:  # pragma: no cover - defensivo ante estructuras raras
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        seen.add(round(float(value), 2))
    elif isinstance(value, str):
        # Un minutaje que ya viene en formato reloj desde la herramienta cuenta
        # por su valor decimal además de por sus dos mitades: si no, la
        # respuesta que lo copia tal cual no casaría con nada.
        for match in _CLOCK_RE.finditer(value):
            seen.add(round(_clock_minutes(match), 2))
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


def numbers_in_text(text: str) -> Set[float]:
    """Todas las cifras que aparecen en un texto, con la misma lectura que el verificador.

    Existe para el banco de pruebas (`tools/assistant_eval.py`, propuesta 13),
    que necesita preguntar lo contrario que `verify_numbers`: no "qué cifras de
    la respuesta no salen del dato", sino "¿ha citado la respuesta los 21
    puntos que tenía que citar?".

    Es una función de cuatro líneas porque la alternativa era escribir un
    segundo extractor de números en el guion de evaluación, y ahí está la
    trampa: ese segundo extractor no sabría que `20:29` son 20.48 minutos ni
    que `acb-104714` no son dos cifras, así que el set dorado mediría con una
    regla distinta de la que usa el asistente en producción — y las dos
    derivarían en cuanto una de las dos se tocase.
    """
    seen: Set[float] = set()
    _numbers_in(_DATE_RE.sub(" ", text or ""), seen)
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

    def _matches(number: float) -> bool:
        return any(abs(number - candidate) <= _TOLERANCE for candidate in available)

    cleaned = _DATE_RE.sub(" ", text)

    # Se recoge todo con su posición y se ordena al final, para que el aviso
    # siga saliendo en orden de aparición aunque el minutaje se escanee aparte.
    found: List[tuple] = []
    for match in _CLOCK_RE.finditer(cleaned):
        minutes = _clock_minutes(match)
        halves = (float(match.group(1)), float(match.group(2)))
        # O cuadra como minutaje, o cuadran sus dos mitades por separado (un
        # marcador a la europea, "94:58", no es un minutaje inventado).
        verified = _matches(minutes) or all(_matches(half) for half in halves)
        found.append((match.start(), match.group(0), verified))

    # Enmascarado conservando la longitud: el minutaje ya está comprobado
    # entero y no debe volver a mirarse por trozos, pero las posiciones del
    # resto del texto no pueden moverse.
    masked = _CLOCK_RE.sub(lambda m: " " * len(m.group(0)), cleaned)
    for match in _NUMBER_RE.finditer(masked):
        number = _to_float(match.group(0))
        if number is None or abs(number) in _IGNORED:
            continue
        found.append((match.start(), match.group(0), _matches(number)))

    unverified: List[str] = []
    for _, raw, verified in sorted(found):
        if not verified and raw not in unverified:
            unverified.append(raw)
    return unverified
