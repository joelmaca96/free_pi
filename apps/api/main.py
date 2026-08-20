"""Fábrica de la aplicación FastAPI (`create_app`) y punto de entrada uvicorn.

Ensambla los routers, el middleware de contexto (request_id, ETag, cache), los
manejadores de excepción (problem+json) y CORS. La API vive bajo el prefijo
`/api/v1` y expone el OpenAPI en `/api/v1/openapi.json`.

Uso (desde la raíz del repo):
    uvicorn apps.api.main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .errors import register_exception_handlers
from .middleware import RequestContextMiddleware
from .routers import admin, games, jobs, matchups, meta, players, reports, teams
from .settings import settings

API_PREFIX = "/api/v1"
API_TITLE = "Baskonia Pipeline API"
API_VERSION = "0.1.0"


def create_app() -> FastAPI:
    """Construye y configura la aplicación FastAPI."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description="API de análisis de Baskonia (fase F3 de la migración strangler-fig).",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    # Middleware de contexto (request_id, ETag, cache) antes que CORS.
    app.add_middleware(RequestContextMiddleware)

    # CORS para la UI Streamlit y el frontend de desarrollo.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Manejadores de excepción → problem+json (RFC 9457).
    register_exception_handlers(app)

    # Routers bajo el prefijo /api/v1.
    for r in (meta.router, teams.router, games.router, players.router,
              matchups.router, reports.router, admin.router, jobs.router):
        app.include_router(r, prefix=API_PREFIX)

    return app


# Instancia de módulo para uvicorn (`uvicorn apps.api.main:app`).
app = create_app()
