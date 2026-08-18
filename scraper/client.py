"""Cliente HTTP con rate-limiting y reintentos para Basketball-Reference.

Basketball-Reference bloquea bots sin un User-Agent realista y puede
banear IPs que hagan demasiadas peticiones seguidas. Este cliente:
- Usa un User-Agent configurable.
- Aplica un retardo entre peticiones (rate-limiting).
- Reintenta con backoff exponencial ante errores transitorios.
"""
import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class BBRClient:
    """Cliente HTTP seguro para Basketball-Reference."""

    def __init__(
        self,
        user_agent: Optional[str] = None,
        delay: Optional[float] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        """Inicializa el cliente con los parámetros de red.

        Args:
            user_agent: User-Agent a usar en las peticiones.
            delay: Segundos de espera entre peticiones.
            timeout: Timeout de cada petición en segundos.
            max_retries: Número máximo de reintentos ante fallos.
        """
        self.user_agent = user_agent or config.USER_AGENT
        self.delay = delay if delay is not None else config.REQUEST_DELAY
        self.timeout = timeout if timeout is not None else config.TIMEOUT
        self.max_retries = max_retries if max_retries is not None else config.MAX_RETRIES
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        self._last_request_time = 0.0

    def _wait_for_rate_limit(self) -> None:
        """Espera el tiempo necesario para respetar el rate-limiting."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def get(self, url: str) -> requests.Response:
        """Realiza una petición GET con rate-limiting y reintentos.

        Args:
            url: URL a solicitar.

        Returns:
            La respuesta HTTP.

        Raises:
            requests.RequestException: Si falla tras todos los reintentos.
        """
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_rate_limit()
            try:
                response = self.session.get(url, timeout=self.timeout)
                self._last_request_time = time.time()
                # BBR sirve las páginas en UTF-8 real pero sin declarar el
                # charset en el header Content-Type ("text/html" a secas).
                # Sin esto, requests usa el default de la RFC (ISO-8859-1)
                # para decodificar `.text`, corrompiendo cualquier caracter
                # acentuado (p.ej. "Río Breogán" -> mojibake).
                response.encoding = "utf-8"
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                logger.warning("Error en %s (intento %d/%d): %s", url, attempt, self.max_retries, exc)
                if attempt == self.max_retries:
                    raise
                # Backoff exponencial: 2^attempt segundos
                time.sleep(2 ** attempt)
        raise requests.RequestException(f"Fallo al obtener {url}")
