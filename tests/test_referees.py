"""Tests de `packages/baskonia_core/referees.py` (Fase 5, perfil arbitral).

Los cuatro casos de aquí son los reales encontrados al revisar a mano los 104
nombres de árbitro de `data/baskonia.db` (ver el módulo y
`doc/features/propuestas/05_perfil_arbitral.md` §5) — no son ejemplos
inventados.
"""
from packages.baskonia_core.referees import canonical_referee_name


def test_accent_only_variants_collapse_to_the_accented_form():
    """Euroliga da el nombre sin acentos; ACB, con ellos. `normalize_name` ya
    los agrupa (misma clave), y el alias fija cuál de las dos formas se enseña."""
    assert canonical_referee_name("Arnau Padros") == "Arnau Padrós"
    assert canonical_referee_name("Arnau Padrós") == "Arnau Padrós"
    assert canonical_referee_name("Carlos Cortes") == "Carlos Cortés"


def test_short_euroleague_name_maps_to_the_full_acb_name():
    """Euroliga a veces da el nombre sin el segundo apellido para la MISMA
    persona que ACB registra completa — `normalize_name` por sí solo no los
    fusiona (distinto número de palabras), hace falta el alias a mano."""
    assert canonical_referee_name("Emilio Perez") == "Emilio Pérez Pizarro"
    assert canonical_referee_name("Emilio Pérez Pizarro") == "Emilio Pérez Pizarro"
    assert canonical_referee_name("Juan Carlos Garcia") == "Juan Carlos García González"


def test_name_without_a_known_alias_is_returned_as_is():
    assert canonical_referee_name("Carlos Peruga") == "Carlos Peruga"


def test_surrounding_whitespace_is_stripped():
    assert canonical_referee_name("  Carlos Peruga  ") == "Carlos Peruga"
