"""Schema del endpoint de refresco bajo demanda de un partido."""
from typing import Literal

from pydantic import BaseModel


class RefreshResponse(BaseModel):
    """Resultado de *encolar* un refresco (no del refresco en sí).

    El trabajo real corre después en background: `triggered` significa
    "aceptado y encolado", no "datos ya disponibles". El cliente comprueba el
    resultado reconsultando los endpoints de lectura; si el fetch a la fuente
    falla, esos endpoints siguen devolviendo `null`/`[]` (nunca un valor
    fabricado).

    Attributes:
        game_id: id del partido para el que se pidió el refresco.
        status: `triggered` (encolado), `already_in_progress` (ya hay un
            refresco en curso para ese partido) o `rejected_busy` (límite de
            refrescos concurrentes alcanzado; reintentar más tarde).
    """

    game_id: str
    status: Literal["triggered", "already_in_progress", "rejected_busy"]
