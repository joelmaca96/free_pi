"""Canonicalización de nombres de árbitro (Fase 5, perfil arbitral).

Ver `doc/features/propuestas/05_perfil_arbitral.md` §3/§5/§6. `games.referees`
guarda la terna cruda de cada fuente, unida por " · "
(`ingest/acb/adapter.py`, `ingest/euroleague/adapter.py`) — este módulo decide
qué nombre CANÓNICO le corresponde a cada árbitro antes de que
`ingest/common/loader.py::_replace_game_referees` lo escriba en
`game_referees`, para que el mismo árbitro escrito de dos formas no parta su
muestra en dos (§3: la unidad de análisis es el árbitro individual, y con
104 nombres distintos sobre 736 partidos cada partido cuenta).

Dos pasos, igual que la identidad de equipos en `names.py`:

1. `normalize_name` (acentos, mayúsculas, espacios) ya fusiona solo la mitad
   de los duplicados reales de `data/baskonia.db` — los que difieren
   ÚNICAMENTE en acento/mayúscula ("Arnau Padros" / "Arnau Padrós", "Carlos
   Cortes" / "Carlos Cortés": Euroliga da el nombre sin acentos, ACB con
   ellos).
2. `_REFEREE_ALIASES`, a mano, para los que además difieren en NÚMERO de
   palabras: Euroliga da a veces el nombre más corto que ACB para la MISMA
   persona (sin segundo apellido) — verificado en vivo cruzando competición
   por nombre en `data/baskonia.db` (`Emilio Perez`, 32 partidos, todos
   Euroliga, frente a `Emilio Pérez Pizarro`, 39 partidos de ACB/Copa/
   Supercopa; mismo patrón con `Juan Carlos Garcia`/`Juan Carlos García
   González`). Sin este alias, `normalize_name` los deja como dos árbitros
   distintos porque el número de palabras no coincide.

Revisado a mano el 2026-08-29 sobre los 104 nombres reales de
`data/baskonia.db` (ver el documento §5): con los dos pasos de arriba quedan
100 árbitros distintos. Si una reingesta futura trae nombres nuevos, hay que
repetir esta revisión — no hay forma automática de saber si un nombre nuevo
es un árbitro nuevo o una tercera forma de escribir uno ya conocido.
"""
from .names import normalize_name

# Clave: `normalize_name(nombre_corto)`. Valor: el nombre COMPLETO a mostrar
# (se prefiere la forma de ACB, más completa, sobre la de Euroliga). Incluye
# también los dos pares que solo difieren en acento: aunque `normalize_name`
# ya los fusiona para agrupar, sin esta entrada el nombre canónico que se
# ENSEÑARÍA sería el que haya llegado antes a la base de datos (podría ser la
# forma sin acentos) — con la entrada, siempre gana la forma completa.
_REFEREE_ALIASES = {
    "arnau padros": "Arnau Padrós",
    "carlos cortes": "Carlos Cortés",
    "emilio perez": "Emilio Pérez Pizarro",
    "juan carlos garcia": "Juan Carlos García González",
}


def canonical_referee_name(raw: str) -> str:
    """Nombre canónico de un árbitro, para agregar su muestra en un solo sitio.

    Un nombre sin alias conocido se devuelve tal cual (recortado de espacios),
    no en minúsculas ni sin acentos: `normalize_name` es solo la CLAVE de
    búsqueda, nunca lo que se enseña en pantalla.
    """
    stripped = raw.strip()
    return _REFEREE_ALIASES.get(normalize_name(stripped), stripped)
