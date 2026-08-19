"""Routers de informes: /reports/... (endpoints 17 y 18).

En F3 estos endpoints devuelven 501 (Not Implemented): la implementación real
de los informes es trabajo de la fase F6. El contrato queda congelado (ruta y
schema de respuesta), pero el cuerpo es un placeholder.
"""
from fastapi import APIRouter, Depends, HTTPException

from packages.baskonia_core.db import models

from ..deps import get_team
from ..schemas.reports import ReportNotImplemented

router = APIRouter(tags=["reports"])


@router.get(
    "/teams/{slug}/reports/scouting.pdf",
    response_model=ReportNotImplemented,
    responses={501: {"model": ReportNotImplemented}},
)
def team_report(team: models.Team = Depends(get_team)) -> None:
    """Informe de equipo (endpoint 17) — implementado en F6."""
    raise HTTPException(
        status_code=501,
        detail="Informe de equipo no implementado en F3 (previsto en F6).",
    )


@router.get(
    "/teams/{slug}/reports/roster.pptx",
    response_model=ReportNotImplemented,
    responses={501: {"model": ReportNotImplemented}},
)
def matchup_report(team: models.Team = Depends(get_team)) -> None:
    """Informe de enfrentamiento (endpoint 18) — implementado en F6."""
    raise HTTPException(
        status_code=501,
        detail="Informe de enfrentamiento no implementado en F3 (previsto en F6).",
    )
