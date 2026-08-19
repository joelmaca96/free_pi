"""Tests para `scraper/acb_api.py`.

La API de la ACB requiere red y su esquema JSON está pendiente de verificar
en desarrollo, por lo que aquí solo se cubren las constantes y helpers puros
del módulo (sin red).
"""
from apps.ingest.scraper.acb_api import ACB_API_BASE, _headers


def test_api_base():
    """La base de la API de la ACB es la esperada."""
    assert ACB_API_BASE == "https://www.acb.com/api"


def test_headers_include_user_agent():
    """Las cabeceras incluyen un User-Agent y Accept JSON."""
    headers = _headers()
    assert "User-Agent" in headers
    assert headers["Accept"] == "application/json"
