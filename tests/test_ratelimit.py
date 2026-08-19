"""Tests para `scraper/ratelimit.py`.

El rate-limiting compartido evita que los scrapers que usan `requests`
directamente (RealGM, API ACB, CMS) hagan cientos de peticiones seguidas.
Se verifica que espera cuando toca, que el estado es por host y que un retardo
de 0 lo desactiva.
"""
import time

import pytest

from apps.ingest.scraper import ratelimit


@pytest.fixture(autouse=True)
def _clean_state():
    """Cada test parte sin memoria de peticiones anteriores."""
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_first_request_does_not_wait():
    """La primera petición a un host no espera."""
    start = time.time()
    ratelimit.throttle("https://basketball.realgm.com/x", delay=0.2)
    assert time.time() - start < 0.1


def test_second_request_to_same_host_waits():
    """Dos peticiones seguidas al mismo host respetan el retardo."""
    ratelimit.throttle("https://basketball.realgm.com/a", delay=0.2)
    start = time.time()
    ratelimit.throttle("https://basketball.realgm.com/b", delay=0.2)
    assert time.time() - start >= 0.15


def test_other_host_is_not_penalised():
    """El estado es por host: RealGM no penaliza a la API de la ACB."""
    ratelimit.throttle("https://basketball.realgm.com/a", delay=0.2)
    start = time.time()
    ratelimit.throttle("https://www.acb.com/api/x", delay=0.2)
    assert time.time() - start < 0.1


def test_zero_delay_disables_wait():
    """Un retardo de 0 desactiva la espera (usado en tests y desarrollo)."""
    ratelimit.throttle("https://basketball.realgm.com/a", delay=0)
    start = time.time()
    ratelimit.throttle("https://basketball.realgm.com/b", delay=0)
    assert time.time() - start < 0.1


def test_reset_forgets_single_host():
    """`reset(host)` olvida solo ese host."""
    ratelimit.throttle("https://basketball.realgm.com/a", delay=0.2)
    ratelimit.reset("basketball.realgm.com")
    start = time.time()
    ratelimit.throttle("https://basketball.realgm.com/b", delay=0.2)
    assert time.time() - start < 0.1


def test_scrapers_call_throttle_before_requesting():
    """Los tres scrapers sin `BBRClient` importan el throttle.

    Regresión: `realgm`/`acb_api`/`baskonia_official` hacían peticiones sin
    ningún rate-limiting (`realgm.fetch_team_schedule` llega a ~273 seguidas).
    """
    from apps.ingest.scraper import acb_api, baskonia_official, realgm

    for module in (realgm, acb_api, baskonia_official):
        assert getattr(module, "throttle", None) is ratelimit.throttle, (
            f"{module.__name__} no usa el rate-limiting compartido"
        )
