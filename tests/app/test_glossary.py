"""Tests del glosario de siglas (`app/components/glossary.py`).

Dos cosas que merecen un test y una que no:

- **Que ninguna pantalla pida una sigla que no existe.** Es el único fallo
  real de este módulo, y es de los que no se ven: `help_text("efg")` en vez de
  `help_text("efg_pct")` revienta la pantalla entera al pintarla, y solo la
  pantalla que lo tenga. El test recorre `app/` buscando las claves que se
  piden de verdad, así que cubre también las pantallas que se escriban
  mañana sin tocar este fichero.
- **Que el HTML del `<abbr>` se escape.** Va a un `unsafe_allow_html=True`.
- Lo que NO se prueba es la redacción de cada descripción: es criterio
  editorial, no comportamiento, y un test que la fije solo estorba al
  reescribirla mejor.
"""
import io
import re
from pathlib import Path

import pytest

from app.components import glossary

_APP = Path(__file__).resolve().parents[2] / "app"


def test_every_term_is_filled_in():
    """Una entrada a medias es peor que ninguna: el hover saldría vacío."""
    for key, term in glossary.TERMS.items():
        assert term.sigla.strip(), key
        assert term.nombre.strip(), key
        # Descripción de verdad, no un sinónimo de la sigla: si no explica
        # nada, el tooltip no le ahorra la consulta a nadie.
        assert len(term.descripcion.strip()) > 20, key


def test_help_text_leads_with_what_the_abbreviation_means():
    """Al pasar por encima, primero qué es la sigla y después qué mide."""
    text = glossary.help_text("efg_pct")

    assert text.startswith("Porcentaje de tiro efectivo — ")
    assert "1,5" in text


def test_term_fails_loudly_on_an_unknown_key():
    """Una sigla que se pinta sin definir es un error de programación, no un hueco."""
    with pytest.raises(KeyError):
        glossary.term("no_existe")


def test_abbr_escapes_what_it_puts_in_the_html():
    """El `<abbr>` acaba en un `unsafe_allow_html=True`: nada se concatena sin escapar."""
    html = glossary.abbr("pts", text='<script>"x"')

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'title="Puntos — ' in html


def test_abbr_defaults_to_the_glossary_label():
    assert ">eFG%</abbr>" in glossary.abbr("efg_pct")


def test_rows_keeps_the_glossary_order_and_drops_duplicates():
    """El desplegable se lee por familias (básicas, avanzadas, tiro), no alfabético."""
    entries = glossary.rows(["ortg", "pts", "pts", "efg_pct"])

    assert [e.sigla for e in entries] == ["Pts", "ORtg", "eFG%"]


def _requested_keys():
    """Claves que las pantallas piden de verdad, leídas del código de `app/`."""
    for path in _APP.rglob("*.py"):
        source = io.open(path, encoding="utf-8").read()
        for key in re.findall(r'help_text\(\s*"([a-z_]+)"\s*\)', source):
            yield path.name, key
        for key in re.findall(r'\babbr\(\s*"([a-z_]+)"', source):
            yield path.name, key
        for block in re.findall(r"glossary_expander\(\s*\[(.*?)\]", source, re.S):
            for key in re.findall(r'"([a-z_]+)"', block):
                yield path.name, key


def test_no_screen_asks_for_a_term_that_does_not_exist():
    """El test que de verdad protege: una sigla sin definir rompe SU pantalla y solo esa."""
    missing = sorted({(name, key) for name, key in _requested_keys() if key not in glossary.TERMS})

    assert not missing, f"claves pedidas y no definidas en el glosario: {missing}"


def test_the_screens_actually_use_the_glossary():
    """Guarda contra el propio test de arriba: sin llamadas, no probaría nada."""
    assert len({key for _, key in _requested_keys()}) > 20
