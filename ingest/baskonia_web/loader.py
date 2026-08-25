"""Carga idempotente de la plantilla scrapeada de baskonia.com.

Solo toca `players` (equipo `'bas'`) y `player_external_ids`
(`source='baskonia_web'`) — nunca `player_game_stats`, `games` ni ninguna
tabla de partido, tal como pide la tarea.

`external_id`: el id numérico estable del miembro de plantilla en el JSON
embebido de baskonia.com (ver `scraper.py`), no un nombre normalizado.
"""
import logging
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ingest.common.identity import resolve_or_create_player

from .scraper import ScrapedPlayer

logger = logging.getLogger(__name__)

SOURCE = "baskonia_web"
OWN_TEAM_ID = "bas"


def load_roster(conn: Connection, players: List[ScrapedPlayer]) -> Dict[str, list]:
    """Upsert de la plantilla scrapeada; desactiva (no borra) a quien ya no aparece."""
    seen_player_ids = set()
    for player in players:
        player_id = resolve_or_create_player(
            conn, SOURCE, player.external_id, player.name, OWN_TEAM_ID,
            number=player.number, position=player.position,
        )
        conn.execute(
            text(
                "UPDATE players SET active = 1, photo_url = COALESCE(:photo_url, photo_url),"
                " photo_local_path = COALESCE(:photo_local_path, photo_local_path),"
                " birth_date = COALESCE(:birth_date, birth_date),"
                " nationality = COALESCE(:nationality, nationality)"
                " WHERE id = :id"
            ),
            {
                "photo_url": player.photo_url, "photo_local_path": player.photo_local_path,
                "birth_date": player.birth_date,
                "nationality": player.nationality, "id": player_id,
            },
        )
        seen_player_ids.add(player_id)

    existing_ids = [
        row[0]
        for row in conn.execute(
            text("SELECT id FROM players WHERE team_id = :team_id"), {"team_id": OWN_TEAM_ID}
        ).all()
    ]
    deactivated = [pid for pid in existing_ids if pid not in seen_player_ids]
    for player_id in deactivated:
        conn.execute(text("UPDATE players SET active = 0 WHERE id = :id"), {"id": player_id})

    logger.info(
        "baskonia_web: %d jugadores en plantilla, %d desactivados", len(seen_player_ids), len(deactivated)
    )
    return {"active": sorted(seen_player_ids), "deactivated": sorted(deactivated)}
