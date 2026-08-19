"""Middleware de la API: request_id, access log y ETag/Cache-Control.

- **request_id**: genera un UUID por petición, lo expone en la cabecera
  `X-Request-Id` y lo guarda en `request.state` para que el log y los errores
  lo incluyan.
- **ETag / Cache-Control**: `Cache-Control: public, max-age=60` + `ETag`
  derivado del `mtime` de la BD. Un `If-None-Match` tras una respuesta previa
  devuelve `304`.
"""
import hashlib
import logging
import os
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .settings import settings

logger = logging.getLogger("baskonia.api.middleware")


def _db_mtime() -> str:
    """Devuelve el mtime de la BD como string (para derivar el ETag)."""
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        try:
            return str(os.path.getmtime(path))
        except OSError:
            return "0"
    return "0"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Añade request_id y ETag/Cache-Control a cada respuesta."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-Id"] = request_id

        # ETag derivado del mtime de la BD: el dato solo cambia tras una
        # ejecución del pipeline, así que el ETag es estable entre lecturas.
        etag = hashlib.sha1(_db_mtime().encode()).hexdigest()
        response.headers["ETag"] = f'"{etag}"'
        response.headers["Cache-Control"] = f"public, max-age={settings.cache_max_age}"

        # 304 si el cliente ya tiene la versión actual.
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match and if_none_match.strip('"') == etag and response.status_code < 400:
            response = Response(status_code=304, headers=dict(response.headers))

        logger.info(
            "request_id=%s method=%s path=%s status=%d elapsed_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
