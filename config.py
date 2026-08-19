"""Configuración central del pipeline de scraping.

Carga la configuración desde variables de entorno (archivo .env) y
expone los parámetros usados por el resto de módulos.
"""
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe
load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    """Lee una variable de entorno como booleano."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# --- Parámetros de red ---
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
)
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "20"))
TIMEOUT = float(os.getenv("TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# --- Base de datos ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/baskonia.db")

# --- Temporada y equipos ---
SEASON = int(os.getenv("SEASON", "2026"))
TEAMS = [t.strip() for t in os.getenv("TEAMS", "vitoria,bilbao").split(",") if t.strip()]
LEAGUES = [l.strip() for l in os.getenv("LEAGUES", "acb,euroleague").split(",") if l.strip()]

# Número de partidos recientes por equipo cuyo box score se descarga (además
# de los enfrentamientos directos entre los equipos de TEAMS).
LAST_N_GAMES = int(os.getenv("LAST_N_GAMES", "10"))

# --- URLs base de Basketball-Reference ---
BBR_BASE = "https://www.basketball-reference.com"
BBR_INTERNATIONAL = f"{BBR_BASE}/international"

# Mapa de ligas a slugs de BBR
LEAGUE_SLUGS = {
    "acb": "spain-liga-acb",
    "euroleague": "euroleague",
}

# Nombres de equipo a mostrar en la UI, cuando el slug de BBR no coincide con
# el nombre habitual (p.ej. BBR usa "vitoria" como slug para el Baskonia).
TEAM_DISPLAY_NAMES = {
    "vitoria": "Baskonia",
}

# --- Backfill de temporada histórica y fuentes adicionales ---
# Competiciones canónicas a cubrir en el backfill de temporada histórica.
BACKFILL_COMPETITIONS = ["euroleague", "acb", "copa-del-rey", "supercopa"]
# Nombre del Baskonia tal como lo usa la API de la ACB (verificar en desarrollo).
ACB_TEAM_NAME = os.getenv("ACB_TEAM_NAME", "Baskonia")
# Nombre del Baskonia tal como lo usa RealGM (verificar en desarrollo).
REALGM_TEAM_NAME = os.getenv("REALGM_TEAM_NAME", "Baskonia")
# Temporada por defecto para la descarga puntual de rivales (--scout-team).
SCOUT_SEASON = int(os.getenv("SCOUT_SEASON", "2026"))

