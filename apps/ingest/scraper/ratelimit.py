"""Rate-limiting compartido por host para los scrapers HTTP (capa `scraper/`).

`client.BBRClient` ya aplica su propio retardo a Basketball-Reference, pero los
scrapers que usan `requests` directamente (RealGM, API ACB, CMS baskonia.com)
no tenían ninguno: `realgm.fetch_team_schedule` llega a hacer ~273 peticiones
seguidas (una por día de la temporada), lo que arriesga un baneo de IP y
contradice la política de rate-limiting del propio proyecto.

Este módulo centraliza esa espera. El estado es **por host**, de modo que
descargar de RealGM no penaliza a la API de la ACB y viceversa.
"""
import logging
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlparse

from packages.baskonia_core import config

logger = logging.getLogger(__name__)

# Marca de tiempo de la última petición hecha a cada host.
_last_request: Dict[str, float] = {}
_lock = threading.Lock()


def throttle(url: str, delay: Optional[float] = None) -> None:
    """Espera lo necesario para respetar el retardo mínimo entre peticiones.

    Se llama justo antes de cada petición HTTP. Si desde la última petición al
    mismo host ha pasado menos de `delay` segundos, duerme el resto.

    Args:
        url: URL que se va a pedir (solo se usa su host).
        delay: Segundos mínimos entre peticiones al mismo host. Por defecto
            `config.SOURCE_REQUEST_DELAY` (los 20 s de `config.REQUEST_DELAY`
            son específicos de Basketball-Reference, que los aplica ya
            `client.BBRClient`).
    """
    wait = config.SOURCE_REQUEST_DELAY if delay is None else delay
    if wait <= 0:
        return

    host = urlparse(url).netloc or url
    with _lock:
        elapsed = time.time() - _last_request.get(host, 0.0)
        remaining = wait - elapsed
        if remaining > 0:
            logger.debug("Rate-limit: esperando %.1fs antes de pedir a %s", remaining, host)
            time.sleep(remaining)
        _last_request[host] = time.time()


def reset(host: Optional[str] = None) -> None:
    """Olvida el estado de rate-limiting (usado en tests).

    Args:
        host: Host concreto a olvidar. Si es `None`, olvida todos.
    """
    with _lock:
        if host is None:
            _last_request.clear()
        else:
            _last_request.pop(host, None)
