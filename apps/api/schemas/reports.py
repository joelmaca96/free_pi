"""Schemas de los endpoints de informes (reports) y admin (data-quality)."""
from pydantic import BaseModel


class ReportNotImplemented(BaseModel):
    """Respuesta de los endpoints de informes en F3 (se completan en F6)."""

    detail: str


class DataQualityResponse(BaseModel):
    """Resultado de la validación de calidad de datos."""

    warnings: list[str]
    healthy: bool
