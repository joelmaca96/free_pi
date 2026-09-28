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
#
# DESDE 2026-09-28 ESTA LISTA YA NO ES LA PRIMERA LÍNEA DE DEFENSA en ACB:
# cada equipo de la API de ACB trae un `clubId` estable entre temporadas y
# patrocinadores (verificado en vivo, ver `ingest/common/identity.py::
# resolve_or_create_team` y `doc/features/ingestor/01_estado.md`), y la
# ingesta lo guarda en `teams.acb_club_id` y empareja por él ANTES que por
# nombre. Un patrocinador nuevo de un club de ACB ya no crea un duplicado
# aunque no esté aquí. La lista sigue sirviendo, y manda, para:
#   - unir el mismo club ENTRE FUENTES ("Barça" de ACB con "FC Barcelona" de
#     Euroliga: el `clubId` de ACB no existe en Euroliga, y el `code` de
#     Euroliga no existe en ACB);
#   - filas antiguas que todavía no tienen `acb_club_id` (se rellena solo en
#     la siguiente ingesta que vuelva a ver ese equipo);
#   - forzar a mano un emparejamiento que el emparejador difuso solo sugiere
#     (`find_team_identity_suggestions`), que nunca fusiona por su cuenta.
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
    # HALLAZGO (2026-08-28): el mismo bug de arriba (ACB da un `id` de equipo
    # nuevo cada vez que cambia el patrocinador) le pasaba a CASI todos los
    # rivales, no solo al Baskonia - se detectó al preguntarle al asistente
    # por "Bilbao Basket" y devolver dos candidatos ("Bilbao Basket" y "Surne
    # Bilbao") que son el mismo club. `data/baskonia.db` ya tenía las filas
    # de `teams` duplicadas fusionadas a mano (ver
    # `local/features/.../migraciones` o el historial de chat); estas
    # entradas son lo que evita que la próxima ingesta (próximo cambio de
    # patrocinador) vuelva a crear una fila nueva.
    "barca": "fc barcelona",
    "surne bilbao": "bilbao",
    "amara lleida": "hiopos lleida",
    "cochesinternet lleida": "hiopos lleida",
    "ilerna lleida": "hiopos lleida",
    "kids us manresa": "baxi manresa",
    "occident manresa": "baxi manresa",
    "casademont zgz": "casademont zaragoza",
    "la laguna tfe": "la laguna tenerife",
    "dreamland gran canaria": "gran canaria",
    "tirma gran canaria": "gran canaria",
    "fiatc girona": "girona",
    "burgos grupo de santiago": "recoletas salud san pablo burgos",
    "recoletas salud": "recoletas salud san pablo burgos",
    "san pablo burgos segura y mayor": "recoletas salud san pablo burgos",
    "asisa joventut": "joventut badalona",
    "morabanc andorra undercoverlab": "morabanc andorra",
    "morabanc and": "morabanc andorra",
    "ucam murcia polymec": "ucam murcia",
    "oteclima granada": "coviran granada",
    "stellantis you granada": "coviran granada",
    "bayern munich": "fc bayern munich",
    "unicaja malaga": "unicaja",
    "cajasiete fundacion canarias": "fundacion canarias",
}


def normalize_name_without_aliases(raw: str) -> str:
    """`normalize_name` SIN resolver `_KNOWN_TEAM_ALIASES`.

    Para quien necesita las palabras reales del nombre y no la forma canónica
    del alias: el emparejador difuso de clubes
    (`ingest.common.identity.find_team_identity_suggestions`) busca palabras
    en común ("kids us manresa" y "baxi manresa" comparten `manresa`), y con
    el alias ya aplicado las dos serían "baxi manresa" y no habría nada que
    sugerir — ya serían una colisión exacta.
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_only = ascii_only.lower()
    ascii_only = re.sub(r"[^a-z0-9\s]", " ", ascii_only)
    words = [w for w in ascii_only.split() if w not in _CLUB_NOISE_WORDS]
    return " ".join(words).strip()


def normalize_name(raw: str) -> str:
    """Normaliza un nombre (jugador o equipo) para comparar entre fuentes.

    Quita acentos, pasa a minúsculas, elimina puntuación y palabras de ruido
    de club, y colapsa espacios. P.ej. "Valencia Basket Club" y "Valencia"
    normalizan ambos a "valencia". También resuelve alias conocidos de
    patrocinador (`_KNOWN_TEAM_ALIASES`).
    """
    normalized = normalize_name_without_aliases(raw)
    return _KNOWN_TEAM_ALIASES.get(normalized, normalized)
