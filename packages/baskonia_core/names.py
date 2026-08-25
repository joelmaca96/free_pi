"""Normalización de nombres de equipo/jugador. Única en todo el proyecto, a propósito.

Nació en `ingest/common/identity.py` (que sigue siendo su consumidor
principal: es donde se decide "esto ya existe" vs "esto es nuevo" al cargar
datos de ACB/Euroliga/baskonia.com) y se ha movido aquí sin cambiar una coma
de su comportamiento porque el asistente la necesita también, para resolver
lo que el usuario escribe en el chat contra `players`/`teams`
(`app/assistant/resolve.py`).

El motivo de moverla en vez de copiarla es doble:

1. **Dos normalizaciones distintas es cómo se resuelve mal justo el caso
   raro** (`local/features/005-chatbot/01_design.md` §5.1). Si el chat
   normaliza distinto de como se cargaron los datos, "Fenerbahçe" encuentra
   al equipo y "Kosner Baskonia" no, sin que nada lo avise.
2. `ingest/` **no viaja en la imagen de la interfaz** (ver `app/Dockerfile`:
   copia `packages/` y `app/`, nada más). Importarla desde `ingest/`
   funcionaría en desarrollo y fallaría en la Raspberry Pi, que es la peor
   combinación posible.

`ingest.common.identity` la reexporta, así que todo lo que ya la importaba de
allí sigue funcionando igual.
"""
import re
import unicodedata

# Palabras/sufijos habituales en nombres de clubes que sobran para comparar
# (p.ej. "Valencia Basket Club" vs "Valencia" deben normalizar igual).
_CLUB_NOISE_WORDS = {
    "club", "baloncesto", "basket", "basquet", "cb", "bc", "sad", "s.a.d",
    "saski", "kirolak", "kk", "bk",
}

# Alias conocidos que la normalización genérica no resuelve (nombres de
# patrocinador, que cambian de temporada en temporada: "Kosner Baskonia",
# "Saski Baskonia"... todos son el mismo equipo "Baskonia").
_KNOWN_TEAM_ALIASES = {
    "kosner baskonia": "baskonia",
    "saski baskonia": "baskonia",
    "baskonia vitoria gasteiz": "baskonia",
    # Nombre real del club en el catálogo de clubes de Euroliga 2026-2027
    # (`EuroleagueClient.fetch_clubs`, verificado en vivo) - usado como
    # respaldo si no hay todavía ningún enlace en `team_external_ids` para
    # identificar al Baskonia (ver `ingest/euroleague/pipeline.py::
    # _own_team_euroleague_code`).
    "kosner baskonia vitoria gasteiz": "baskonia",
}


def normalize_name(raw: str) -> str:
    """Normaliza un nombre (jugador o equipo) para comparar entre fuentes.

    Quita acentos, pasa a minúsculas, elimina puntuación y palabras de ruido
    de club, y colapsa espacios. P.ej. "Valencia Basket Club" y "Valencia"
    normalizan ambos a "valencia". También resuelve alias conocidos de
    patrocinador (`_KNOWN_TEAM_ALIASES`).
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_only = ascii_only.lower()
    ascii_only = re.sub(r"[^a-z0-9\s]", " ", ascii_only)
    words = [w for w in ascii_only.split() if w not in _CLUB_NOISE_WORDS]
    normalized = " ".join(words).strip()
    return _KNOWN_TEAM_ALIASES.get(normalized, normalized)
