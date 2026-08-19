"""Router de admin: /admin/data-quality (endpoint 19)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from packages.baskonia_core import insights

from ..deps import get_session
from ..schemas.reports import DataQualityResponse

router = APIRouter(tags=["admin"])


@router.get("/admin/data-quality", response_model=DataQualityResponse)
def data_quality(session: Session = Depends(get_session)) -> DataQualityResponse:
    """Validación de calidad de datos (endpoint 19)."""
    warnings = insights.validate_data(session)
    return DataQualityResponse(warnings=warnings, healthy=not warnings)
