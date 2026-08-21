"""Conversión de (cuarto, reloj de partido) a segundos transcurridos desde el inicio.

Cuartos reglamentarios de 10 minutos (`Q1`..`Q4`), prórrogas de 5 minutos
(`OT1`, `OT2`...). El reloj cuenta hacia atrás (`MM:SS` restantes en el
periodo), convención habitual de las fuentes de play-by-play (ACB/Euroliga).
"""
import re

_REGULATION_PERIOD_SECONDS = 10 * 60
_OVERTIME_PERIOD_SECONDS = 5 * 60
_REGULATION_PERIODS = 4

_CLOCK_RE = re.compile(r"^(?P<minutes>\d{1,2}):(?P<seconds>\d{2})(?:\.\d+)?$")
_QUARTER_RE = re.compile(r"^(?:Q(?P<regular>\d+)|OT(?P<overtime>\d+))$", re.IGNORECASE)


def _period_index_and_length(quarter: str) -> tuple:
    match = _QUARTER_RE.match(quarter.strip())
    if not match:
        raise ValueError(f"cuarto no reconocido: {quarter!r}")
    if match.group("regular"):
        regular = int(match.group("regular"))
        if not 1 <= regular <= _REGULATION_PERIODS:
            raise ValueError(f"cuarto no reconocido: {quarter!r}")
        return regular - 1, _REGULATION_PERIOD_SECONDS
    return _REGULATION_PERIODS + int(match.group("overtime")) - 1, _OVERTIME_PERIOD_SECONDS


def game_clock_to_seconds(quarter: str, clock: str) -> float:
    """Segundos transcurridos desde el inicio del partido en el instante `(quarter, clock)`.

    Args:
        quarter: `"Q1"`..`"Q4"` (cuartos) o `"OT1"`, `"OT2"`... (prórrogas).
        clock: tiempo restante en el periodo, `"MM:SS"` (p.ej. `"08:24"`).
    """
    match = _CLOCK_RE.match(clock.strip())
    if not match:
        raise ValueError(f"reloj no reconocido: {clock!r}")
    remaining = int(match.group("minutes")) * 60 + int(match.group("seconds"))

    period_index = 0
    elapsed_before = 0.0
    period_index, period_length = _period_index_and_length(quarter)
    for i in range(period_index):
        elapsed_before += _REGULATION_PERIOD_SECONDS if i < _REGULATION_PERIODS else _OVERTIME_PERIOD_SECONDS

    return elapsed_before + (period_length - remaining)
