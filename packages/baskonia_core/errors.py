"""Excepciones de dominio de baskonia_core.

Definidas aquí (no en `apps/api`) para que los servicios del dominio puedan
lanzarlas sin conocer HTTP. `apps/api/errors.py` las traduce a
`application/problem+json` en un único `exception_handler`.

Regla de capas: estas excepciones no saben nada de códigos de estado ni de
formato de respuesta; solo identifican el fallo de dominio.
"""


class DomainError(Exception):
    """Base de las excepciones de dominio. No se lanza directamente."""


class TeamNotFound(DomainError):
    """No existe ningún equipo con el slug solicitado."""

    def __init__(self, slug: str):
        self.slug = slug
        super().__init__(f"No existe ningún equipo con slug '{slug}'.")


class GameNotFound(DomainError):
    """No existe ningún partido con el id solicitado."""

    def __init__(self, game_id: int):
        self.game_id = game_id
        super().__init__(f"No existe ningún partido con id '{game_id}'.")


class InvalidFilter(DomainError):
    """Un parámetro de filtro no es aplicable al recurso solicitado.

    P.ej. pasar `season`/`league` a `/players/load`, que deliberadamente los
    ignora (la carga física es transversal a temporada y competición).
    """

    def __init__(self, message: str):
        super().__init__(message)
