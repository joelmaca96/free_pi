"""Orquesta fetch -> parse -> load para la plantilla de baskonia.com."""
import logging
from typing import Dict

from sqlalchemy.engine import Engine

from .loader import load_roster
from .scraper import fetch_roster_html, parse_roster

logger = logging.getLogger(__name__)


def run(engine: Engine) -> Dict[str, list]:
    """Descarga la plantilla actual y la vuelca (upsert + desactivación de bajas)."""
    html = fetch_roster_html()
    players = parse_roster(html)
    logger.info("baskonia_web: %d jugadores encontrados en la plantilla", len(players))

    with engine.begin() as conn:
        return load_roster(conn, players)
