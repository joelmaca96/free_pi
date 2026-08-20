"""Traducción de excepciones de dominio a `application/problem+json` (RFC 9457).

Único sitio donde una excepción se convierte en una respuesta de error HTTP.
Los routers nunca construyen una respuesta de error a mano: lanzan la excepción
de dominio (`packages/baskonia_core/errors.py`) y este módulo la traduce.

Formato de toda respuesta >= 400:
    {"type", "title", "status", "detail", "instance", "request_id"}
"""
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.baskonia_core.errors import (
    DomainError,
    GameNotFound,
    InvalidFilter,
    JobNotFound,
    TeamNotFound,
)

logger = logging.getLogger("baskonia.api.errors")

# Mapa de excepciones de dominio -> (status, type, title).
_DOMAIN_STATUS = {
    TeamNotFound: (404, "team-not-found", "Equipo no encontrado"),
    GameNotFound: (404, "game-not-found", "Partido no encontrado"),
    InvalidFilter: (400, "invalid-filter", "Filtro no aplicable"),
    JobNotFound: (404, "job-not-found", "Job no encontrado"),
}


def _problem_response(
    request: Request,
    status: int,
    type_slug: str,
    title: str,
    detail: str,
    request_id: str,
) -> JSONResponse:
    """Construye una respuesta `application/problem+json`."""
    return JSONResponse(
        status_code=status,
        content={
            "type": f"https://baskonia.local/errors/{type_slug}",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": str(request.url.path),
            "request_id": request_id,
        },
        media_type="application/problem+json",
    )


def _request_id(request: Request) -> str:
    """Devuelve el request_id de la petición (generado por el middleware)."""
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def register_exception_handlers(app: FastAPI) -> None:
    """Registra los handlers de error de la API en la app FastAPI.

    Args:
        app: instancia FastAPI sobre la que registrar los handlers.
    """
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        status, type_slug, title = _DOMAIN_STATUS.get(type(exc), (500, "internal", "Error interno"))
        rid = _request_id(request)
        if status >= 500:
            logger.error("Error de dominio no mapeado: %s (request_id=%s)", exc, rid)
        return _problem_response(request, status, type_slug, title, str(exc), rid)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = _request_id(request)
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in e.get('loc', []))}: {e.get('msg', '')}"
            for e in exc.errors()
        )
        return _problem_response(
            request, 422, "validation-error", "Parámetros inválidos", detail, rid
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = _request_id(request)
        return _problem_response(
            request,
            exc.status_code,
            "http-error",
            exc.detail if isinstance(exc.detail, str) else "Error HTTP",
            str(exc.detail),
            rid,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        logger.exception("Excepción no controlada (request_id=%s): %s", rid, exc)
        return _problem_response(
            request, 500, "internal", "Error interno", "Error interno del servidor", rid
        )
