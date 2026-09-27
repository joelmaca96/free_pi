"""Guardas globales de la suite. Hoy una: **prohibido salir a la red**.

El README (§6) promete que la suite es 100% offline —mocks y payloads reales
grabados, sin red— y esa promesa se sostenía sola, por disciplina. Dejó de
sostenerse en cuanto se añadió un paso de ingesta nuevo a `run_all` y los
tests que lo mockean todo se olvidaron de ese: `test_run_all_calls_the_three_
modules_in_order` empezó a llamar de verdad a `api-live.euroleague.net`, y no
se notó hasta que el servidor devolvió un 429 y la suite se puso roja sin que
nadie hubiera tocado nada. Un test que sale a internet no es solo lento: es
verde o rojo según la red y según lo que haya al otro lado ese día.

Este fixture corta cualquier conexión saliente y dice QUÉ test la intentó,
que es la mitad útil del aviso. Se permite `localhost` porque la BD de tests
es SQLite en fichero/memoria pero algún componente (Streamlit en
`AppTest`) abre sockets locales.

Si algún día hace falta un test que sí toque la red de verdad, que sea
explícito: `@pytest.mark.allow_network` lo deja pasar.
"""
import socket

import pytest

_real_connect = socket.socket.connect
_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("allow_network"):
        return

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _LOCAL:
            raise RuntimeError(
                f"{request.node.nodeid} ha intentado conectarse a {host}. La suite es offline "
                "(README §6): mockea la llamada, o marca el test con @pytest.mark.allow_network "
                "si de verdad tiene que salir."
            )
        return _real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_network: el test puede salir a la red de verdad.")
