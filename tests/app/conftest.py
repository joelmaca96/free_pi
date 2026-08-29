"""Fixtures compartidas por todos los tests de `tests/app/` (no solo el asistente).

La caché de `st.cache_data.clear` ya existía como fixture `autouse` en
`tests/app/assistant/conftest.py` (necesaria ahí porque el fixture `engine`
de esa carpeta usa un fichero por test) pero solo se aplicaba a esa
subcarpeta — pytest limita un `conftest.py` a su propio directorio y los de
abajo, nunca a los hermanos. El resto de `tests/app/*.py` usa engines
`sqlite:///:memory:` DISTINTOS por test (uno por fixture `engine()`), y las
consultas de `app/data/` cachean por sus argumentos con el motor como
`_engine` (sin hashear, por convención de Streamlit) — así que dos tests en
CUALQUIER par de ficheros de `tests/app/` que llamen a la misma consulta con
los mismos argumentos (team_id/season_id/...) sobre bases de datos distintas
se devuelven el resultado cacheado el uno del otro. Es un falso verde (o un
falso rojo, ver `test_fatigue.py::test_rolling_load_counts_a_dnp_as_zero_
minutes_not_as_a_gap`, que solo se cuela cuando el orden de recolección de
pytest hace coincidir sus argumentos con los de otro test) silencioso y
dependiente del orden — se limpia la caché entre cada test, en todo
`tests/app/`, para que cada test vea siempre su propia base de datos.
"""
import pytest
import streamlit as st


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()
