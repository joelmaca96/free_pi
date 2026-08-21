"""Configuración de logging compartida por los scripts de ingesta."""
import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configura un logging básico a consola (idempotente: no duplica handlers)."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
