"""Utilidades de fechas del dominio compartido.

Contiene `parse_bbr_date`, que necesitan los servicios (`calendar`, `matchup`) y
la capa de presentación (`app.py`). Se extrae aquí para que no viva en la UI.
"""
from datetime import datetime


def parse_bbr_date(value: str) -> "datetime | None":
    """Parsea una fecha en formato BBR ('Sun, Nov 23, 2025'), o None si no cuadra."""
    try:
        return datetime.strptime(value, "%a, %b %d, %Y")
    except (ValueError, TypeError):
        return None
