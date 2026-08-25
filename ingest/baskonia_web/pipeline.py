"""Orquesta fetch -> parse -> fotos -> load para la plantilla de baskonia.com."""
import logging
from typing import Dict

from sqlalchemy.engine import Engine

from .loader import load_roster
from .scraper import download_player_photos, fetch_roster_html, parse_roster

logger = logging.getLogger(__name__)


def run(engine: Engine, download_photos: bool = True) -> Dict[str, list]:
    """Descarga la plantilla actual y la vuelca (upsert + desactivación de bajas).

    Args:
        engine: engine de la BD de scouting.
        download_photos: si `True` (por defecto), descarga a disco la foto de cada
            jugador (ver `scraper.download_player_photos`) antes de cargar — se puede
            desactivar para una ejecución rápida sin tráfico de imágenes (p.ej. `--skip-photos`
            en `cli.py`, o tests que no quieran ejercitar la parte de red de fotos).
    """
    html = fetch_roster_html()
    players = parse_roster(html)
    logger.info("baskonia_web: %d jugadores encontrados en la plantilla", len(players))

    if download_photos:
        download_player_photos(players)

    with engine.begin() as conn:
        return load_roster(conn, players)
