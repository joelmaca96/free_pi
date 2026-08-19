"""Configuración de la aplicación API.

Carga la configuración desde variables de entorno (`.env`) con valores por
defecto razonables para desarrollo. La URL de la BD se delega en
`packages.baskonia_core.config.DATABASE_URL` (misma BD que el pipeline).
"""
import os

from dotenv import load_dotenv

from packages.baskonia_core import config as core_config

load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    """Lee una variable de entorno como booleano."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class ApiSettings:
    """Configuración de la API.

    Attributes:
        database_url: cadena de conexión a la BD compartida (por defecto la del
            dominio, `config.DATABASE_URL`).
        cors_origins: orígenes permitidos para CORS (lista separada por comas).
        log_level: nivel de logging (INFO por defecto).
        cache_max_age: segundos del `Cache-Control` de las respuestas.
    """

    def __init__(self) -> None:
        self.database_url: str = os.getenv("API_DATABASE_URL", core_config.DATABASE_URL)
        self.cors_origins: list[str] = [
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:8501,http://localhost:5173").split(",")
            if o.strip()
        ]
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")
        self.cache_max_age: int = int(os.getenv("CACHE_MAX_AGE", "60"))
        self.debug: bool = _get_bool("API_DEBUG", False)


settings = ApiSettings()
