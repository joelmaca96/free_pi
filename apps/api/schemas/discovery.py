"""Schema del endpoint de descubrimiento de partidos ausentes de `games`."""
from typing import Literal

from pydantic import BaseModel


class MissingGamesResponse(BaseModel):
    """Partidos del calendario de la fuente que aún no están cargados.

    Solo reporta: este endpoint nunca dispara una carga. Los ids vienen ya
    prefijados (`"acb-105370"`, `"euroleague-7"`) para poder pasarlos tal cual a
    `POST /games/{game_id}/refresh?season_label=...`.

    Attributes:
        source: fuente consultada.
        season_label: temporada consultada (`'2025-2026'`).
        missing_game_ids: ids de partidos jugados/finalizados ausentes de
            `games`; lista vacía si no falta ninguno.
    """

    source: Literal["acb", "euroleague"]
    season_label: str
    missing_game_ids: list[str]
