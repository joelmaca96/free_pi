"""Ficha biográfica (posición, altura, nacimiento, nacionalidad) desde la web de acb.com.

POR QUÉ ESTA FUENTE. `ingest/euroleague/roster.py` rellena la ficha de los
jugadores de clubes de Euroliga, y dejó escrito el hueco: "los equipos que
solo juegan ACB se quedan sin ficha [...] la alternativa sería scrapear la
ficha de acb.com". Es esto. La API de acb.com (`api2.acb.com`) no tiene
endpoint de jugador (ver el docstring de `roster.py`), pero la PÁGINA de
jugador sí trae la ficha, renderizada en el servidor:

    https://www.acb.com/jugador/ver/{licencia}
      -> 301 a https://acb.com/es/liga/jugadores/{slug}-{licencia}

con un bloque `PlayerInfoGrid` de pares etiqueta/valor (verificado en vivo
el 2026-09-28 con la licencia 30002444):

    Posición          "Ala-pívot"
    Altura            "2,03 m"
    Fecha nacimiento  "07/03/1995 (31 años)"
    Lugar nacimiento  "Chesterfield (Virginia), EE.UU."   (no se guarda)
    Nacionalidad      "EE.UU."
    Licencia          "EXT"                               (no se guarda)

Dos trampas del HTML, las dos cubiertas por el fixture de los tests
(`tests/ingest/fixtures/acb_player_profile_30002444.html`):

- La página lleva DOS copias del bloque: primero un esqueleto de carga con
  las mismas etiquetas y los valores vacíos (`<span class="_skeleton…">`), y
  después el bueno. Leer "la primera `Altura`" da una cadena vacía. Se toma,
  por etiqueta, el primer valor NO vacío.
- Las clases son CSS modules con sufijo hash
  (`PlayerInfoGrid-module-scss-module__REJd4q__playerInfoGrid__label`), que
  cambia en cada despliegue de la web. Se casa por el final estable
  (`playerInfoGrid__label` / `__value`), nunca por la clase completa.

Una licencia que acb.com no conoce no da 404: redirige a
`/es/liga/equipos`. Se detecta porque la URL final ya no termina en la
licencia, y el jugador se cuenta como "no encontrado".

LA LICENCIA ES EL `external_id` DE ACB. El boxscore (`player.id`) usa el
mismo espacio de ids que la página (comprobado: 30002444 en los dos), y es
lo que `ingest/acb/adapter.py` guarda en `player_external_ids` con
`source='acb'`. Si una fila de `players` tiene MÁS de una licencia de ACB no
se toca: es la huella de dos personas fusionadas en una fila (ver
`ingest/common/identity.py::find_merged_players`), y rellenarle la ficha
con la de una de las dos solo escondería el problema.

MISMAS REGLAS QUE `euroleague/roster.py`:

- SOLO ACTUALIZA, NUNCA CREA. Se parte de las filas que ya tienen licencia.
- RELLENA HUECOS, NO PISA. `COALESCE(campo, :nuevo)`: lo que ya hay gana.
  La posición, que es `NOT NULL` y usa `''` como "sin dato", solo se rellena
  si está vacía. La del boxscore de ACB (`gameRole`) y la de baskonia.com
  ganan siempre; esta solo cubre a quien no tiene ninguna.
- Un jugador que falla (red, página rara) se registra y se salta.

CUÁNDO CORRE Y CUÁNTO CUESTA. Solo pide la ficha de jugadores con algún
hueco (posición en blanco, o altura/nacimiento/nacionalidad NULL), con un
tope por pasada (`limit`) y una pausa entre peticiones (`delay`) para no
martillear acb.com. Un jugador cuya página ya se leyó y que sigue con
huecos (acb.com no publica ese dato) se apunta en una caché JSON pequeña y
no se vuelve a pedir hasta pasados `retry_days`: sin eso, los mismos
jugadores sin fecha de nacimiento se comerían el tope de cada pasada para
siempre. Un fallo de RED no se apunta: se reintenta en la siguiente.

robots.txt de acb.com (comprobado 2026-09-28): `User-agent: *` -> `Allow: /`.
Aun así se consulta en cada ejecución con el mismo helper y el mismo
User-Agent identificable que `ingest/baskonia_web/scraper.py`.

Uso:
    python -m ingest.acb.profiles --limit 20
    python -m ingest.acb.profiles --limit 5 --delay 2 --database-url sqlite:///data/baskonia.db
"""
import argparse
import json
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ingest.baskonia_web.scraper import USER_AGENT, _is_allowed_by_robots

logger = logging.getLogger(__name__)

SOURCE = "acb"
PROFILE_URL = "https://www.acb.com/jugador/ver/{license}"

#: Tope por pasada de `run_all`. Con la pausa por defecto son ~2 minutos: lo
#: bastante barato para correr en cada ingesta, y el hueco inicial (unos
#: cientos de jugadores) se cubre en pocas pasadas.
DEFAULT_LIMIT = int(os.getenv("ACB_PROFILES_LIMIT", "100"))
#: Segundos entre peticiones a acb.com (misma idea que un `REQUEST_DELAY`).
REQUEST_DELAY = float(os.getenv("ACB_PROFILES_DELAY", "1.0"))
_REQUEST_TIMEOUT = 20

#: Caché de licencias ya leídas que siguen con huecos (ver el docstring).
#: Al lado de la BD y de las fotos, en `data/`.
DEFAULT_CACHE_PATH = os.getenv("ACB_PROFILES_CACHE", "data/acb_profiles_seen.json")
RETRY_DAYS = 30

#: Vocabulario de posiciones de la app (`app/analytics/impact.py::_position_ok`,
#: `app/analytics/minutes_plan.py`), que compara por igualdad exacta de
#: etiqueta. La clave es la etiqueta sin acentos, mayúsculas, espacios ni
#: guiones, para aceptar "Ala Pívot"/"ala-pivot"/"Ala-Pívot" sin inventar nada.
_POSITION_LABELS = {
    "base": "Base",
    "escolta": "Escolta",
    "alero": "Alero",
    "alapivot": "Ala-pívot",
    "pivot": "Pívot",
}

#: Rango de alturas creíble en cm; fuera de él se descarta (probable error de
#: formato de la página, no un jugador de 20 cm).
_HEIGHT_RANGE_CM = (150, 240)

_LABEL_SUFFIX = re.compile(r"playerInfoGrid__label$")
_VALUE_SUFFIX = re.compile(r"playerInfoGrid__value$")
_ITEM_SUFFIX = re.compile(r"playerInfoGrid__item$")


# --------------------------------------------------------------------------
# Parser (puro, sin red: es lo que prueban los tests contra el fixture)
# --------------------------------------------------------------------------

def _has_class(tag, pattern: re.Pattern) -> bool:
    return any(pattern.search(cls) for cls in (tag.get("class") or []))


def parse_info_grid(html: str) -> Dict[str, str]:
    """Pares etiqueta -> valor del `PlayerInfoGrid`, con el primer valor NO vacío por etiqueta.

    Las copias esqueleto (valor vacío) se ignoran, estén antes o después de
    la buena. Devuelve `{}` si la página no tiene el bloque (licencia
    desconocida, o acb.com cambió la maqueta).
    """
    soup = BeautifulSoup(html, "html.parser")
    pairs: Dict[str, str] = {}
    for item in soup.find_all(lambda tag: _has_class(tag, _ITEM_SUFFIX)):
        label_tag = item.find(lambda tag: _has_class(tag, _LABEL_SUFFIX))
        value_tag = item.find(lambda tag: _has_class(tag, _VALUE_SUFFIX))
        if label_tag is None or value_tag is None:
            continue
        label = " ".join(label_tag.get_text(" ", strip=True).split())
        value = " ".join(value_tag.get_text(" ", strip=True).split())
        if label and value and label not in pairs:
            pairs[label] = value
    return pairs


def _fold(label: str) -> str:
    """Minúsculas, sin acentos, sin espacios ni guiones: "Ala Pívot" -> "alapivot"."""
    decomposed = unicodedata.normalize("NFKD", label)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[\s\-_/.]+", "", ascii_only).lower()


def normalize_position(value: Optional[str]) -> Optional[str]:
    """Etiqueta de acb.com -> etiqueta de la app, o `None` si no es una de las cinco.

    Una etiqueta desconocida NO se escribe tal cual: la app compara por
    igualdad exacta, y una sexta etiqueta ("Base-escolta", si algún día
    aparece) dejaría a ese jugador fuera de todos los filtros de posición en
    silencio. Mejor seguir en blanco y que se vea.
    """
    if not value:
        return None
    return _POSITION_LABELS.get(_fold(value))


def parse_height_cm(value: Optional[str]) -> Optional[int]:
    """"2,03 m" -> 203. También "2.03 m", "2,03m" y "203 cm". `None` si no cuadra."""
    if not value:
        return None
    raw = value.strip().lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(cm|m)?", raw)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "cm" or (unit is None and number > 10):
        cm = round(number)
    else:
        cm = round(number * 100)
    low, high = _HEIGHT_RANGE_CM
    return cm if low <= cm <= high else None


def parse_birth_date(value: Optional[str]) -> Optional[str]:
    """"07/03/1995 (31 años)" -> "1995-03-07" (día/mes/año, formato español)."""
    if not value:
        return None
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_profile(html: str) -> Dict[str, Optional[object]]:
    """Los cuatro campos de ficha que se guardan, ya en las unidades de la BD.

    `nationality` se guarda tal cual la da acb.com ("EE.UU.", "España"): ya
    está en castellano y con el mismo criterio que baskonia.com, que es la
    forma que la app quiere (ver "nacionalidad" en `euroleague/roster.py`).
    """
    grid = parse_info_grid(html)
    nationality = grid.get("Nacionalidad")
    return {
        "position": normalize_position(grid.get("Posición")),
        "height_cm": parse_height_cm(grid.get("Altura")),
        "birth_date": parse_birth_date(grid.get("Fecha nacimiento")),
        "nationality": nationality.strip() if nationality else None,
    }


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------

_INCOMPLETE_SQL = (
    "(p.position IS NULL OR TRIM(p.position) = ''"
    " OR p.height_cm IS NULL OR p.birth_date IS NULL OR p.nationality IS NULL)"
)


def select_candidates(conn: Connection) -> List[Tuple[str, str]]:
    """`(player_id, licencia)` de los jugadores con licencia ACB única y algún hueco.

    Primero los que no tienen posición (el hueco que más usa la app: filtros
    de quinteto y suelos del planificador de minutos), luego por número de
    huecos. Las filas con dos o más licencias se excluyen (ver el docstring).
    """
    rows = conn.execute(
        text(
            "SELECT p.id AS player_id, MIN(e.external_id) AS license,"
            " (CASE WHEN p.position IS NULL OR TRIM(p.position) = '' THEN 1 ELSE 0 END) AS no_position,"
            " (CASE WHEN p.height_cm IS NULL THEN 1 ELSE 0 END"
            "  + CASE WHEN p.birth_date IS NULL THEN 1 ELSE 0 END"
            "  + CASE WHEN p.nationality IS NULL THEN 1 ELSE 0 END) AS gaps"
            " FROM players p JOIN player_external_ids e"
            "   ON e.player_id = p.id AND e.source = :source"
            f" WHERE {_INCOMPLETE_SQL}"
            " GROUP BY p.id"
            " HAVING COUNT(*) = 1"
            " ORDER BY no_position DESC, gaps DESC, p.id"
        ),
        {"source": SOURCE},
    ).all()
    return [(row.player_id, str(row.license)) for row in rows]


def apply_profile(conn: Connection, player_id: str, fields: Dict[str, Optional[object]]) -> List[str]:
    """Rellena los huecos de `player_id` con `fields`; devuelve qué campos se han rellenado.

    Nunca crea la fila ni pisa un valor existente (ver el docstring).
    """
    current = conn.execute(
        text("SELECT position, height_cm, birth_date, nationality FROM players WHERE id = :id"),
        {"id": player_id},
    ).first()
    if current is None:
        return []

    filled = []
    if fields.get("position") and not (current.position or "").strip():
        filled.append("position")
    for column in ("height_cm", "birth_date", "nationality"):
        if fields.get(column) is not None and getattr(current, column) is None:
            filled.append(column)
    if not filled:
        return []

    conn.execute(
        text(
            "UPDATE players SET"
            " position = CASE WHEN position IS NULL OR TRIM(position) = ''"
            "   THEN COALESCE(:position, position) ELSE position END,"
            " height_cm = COALESCE(height_cm, :height_cm),"
            " birth_date = COALESCE(birth_date, :birth_date),"
            " nationality = COALESCE(nationality, :nationality)"
            " WHERE id = :id"
        ),
        {
            "position": fields.get("position"),
            "height_cm": fields.get("height_cm"),
            "birth_date": fields.get("birth_date"),
            "nationality": fields.get("nationality"),
            "id": player_id,
        },
    )
    return filled


# --------------------------------------------------------------------------
# Caché de licencias ya leídas
# --------------------------------------------------------------------------

def _load_cache(path: Optional[Path]) -> Dict[str, str]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("acb_profiles: caché ilegible en %s (%s); se ignora", path, exc)
        return {}


def _save_cache(path: Optional[Path], cache: Dict[str, str]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_text(json.dumps(cache, sort_keys=True, indent=0), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # una caché que no se puede escribir no es motivo de fallo
        logger.warning("acb_profiles: no se pudo guardar la caché en %s (%s)", path, exc)


def _recently_seen(cache: Dict[str, str], license_id: str, today: date, retry_days: int) -> bool:
    seen = cache.get(license_id)
    if not seen:
        return False
    try:
        return today - datetime.strptime(seen, "%Y-%m-%d").date() < timedelta(days=retry_days)
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Red
# --------------------------------------------------------------------------

class ProfileNotFound(Exception):
    """acb.com no tiene página para esa licencia (redirige al listado de equipos)."""


def fetch_profile_html(session: requests.Session, license_id: str) -> str:
    """HTML de la ficha de `license_id`. `ProfileNotFound` si la licencia no existe.

    Errores de red / HTTP se propagan (`requests.RequestException`): el
    llamante los cuenta como fallo y NO los apunta en la caché.
    """
    url = PROFILE_URL.format(license=license_id)
    if not _is_allowed_by_robots(session, url):
        raise PermissionError(f"robots.txt de acb.com no permite {url}")
    response = session.get(url, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()
    # Licencia desconocida: 200 pero en /es/liga/equipos (ver el docstring).
    final_url = (response.url or url).rstrip("/")
    if not final_url.endswith(str(license_id)):
        raise ProfileNotFound(f"{license_id} redirige a {final_url}")
    return response.text


def run(
    engine: Engine,
    *,
    limit: Optional[int] = DEFAULT_LIMIT,
    delay: float = REQUEST_DELAY,
    cache_path: Optional[str] = DEFAULT_CACHE_PATH,
    retry_days: int = RETRY_DAYS,
    session: Optional[requests.Session] = None,
    sleep: Callable[[float], None] = time.sleep,
    today: Optional[date] = None,
) -> Dict[str, object]:
    """Rellena la ficha de hasta `limit` jugadores con licencia ACB y huecos.

    Args:
        limit: máximo de páginas a pedir en esta pasada (`None` = sin tope).
        delay: segundos entre peticiones.
        cache_path: JSON de licencias ya leídas; `None` desactiva la caché.
        retry_days: días antes de volver a pedir una ficha ya leída que
            seguía incompleta.
        session, sleep, today: inyectables para los tests.

    Returns:
        `{"candidates", "skipped_recent", "fetched", "updated", "not_found",
        "failed", "filled": {campo: n}}`. `candidates` son los jugadores con
        huecos y licencia única; `updated`, los que han ganado algún dato.
    """
    today = today or date.today()
    cache_file = Path(cache_path) if cache_path else None

    with engine.connect() as conn:
        candidates = select_candidates(conn)

    summary: Dict[str, object] = {
        "candidates": len(candidates), "skipped_recent": 0, "fetched": 0, "updated": 0,
        "not_found": 0, "failed": 0,
        "filled": {"position": 0, "height_cm": 0, "birth_date": 0, "nationality": 0},
    }
    if not candidates:
        return summary  # sin red ni disco: lo normal cuando ya está todo cubierto

    cache = _load_cache(cache_file)
    pending = []
    for player_id, license_id in candidates:
        if _recently_seen(cache, license_id, today, retry_days):
            summary["skipped_recent"] += 1
            continue
        pending.append((player_id, license_id))
    if limit is not None:
        pending = pending[: max(limit, 0)]

    if pending:
        session = session or requests.Session()
        session.headers["User-Agent"] = USER_AGENT

    for index, (player_id, license_id) in enumerate(pending):
        if index and delay > 0:
            sleep(delay)
        try:
            html = fetch_profile_html(session, license_id)
        except ProfileNotFound as exc:
            logger.info("acb_profiles: sin ficha en acb.com para %s (%s)", player_id, exc)
            summary["not_found"] += 1
            cache[license_id] = today.isoformat()
            continue
        except Exception as exc:  # noqa: BLE001 - un jugador roto no para al resto
            logger.warning("acb_profiles: no se pudo leer la ficha de %s (licencia %s): %s",
                           player_id, license_id, exc)
            summary["failed"] += 1
            continue

        summary["fetched"] += 1
        try:
            fields = parse_profile(html)
            with engine.begin() as conn:
                filled = apply_profile(conn, player_id, fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning("acb_profiles: ficha de %s ilegible (%s)", player_id, exc)
            summary["failed"] += 1
            continue
        # Leída: se apunta aunque haya rellenado algo. Si queda completa ya no
        # vuelve a ser candidata; si sigue con huecos, es que acb.com no los
        # tiene, y no merece la pena volver a preguntar en `retry_days`.
        cache[license_id] = today.isoformat()
        if filled:
            summary["updated"] += 1
            for column in filled:
                summary["filled"][column] += 1

    if pending:
        _save_cache(cache_file, cache)

    logger.info(
        "acb_profiles: %d candidatos, %d pedidos, %d actualizados (%s), %d sin ficha, %d fallos, "
        "%d saltados por caché",
        summary["candidates"], summary["fetched"], summary["updated"], summary["filled"],
        summary["not_found"], summary["failed"], summary["skipped_recent"],
    )
    return summary


def main() -> None:
    from ingest.common.db import get_engine
    from ingest.common.logging_utils import configure_logging

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Máximo de fichas a pedir (por defecto {DEFAULT_LIMIT}; 0 = ninguna).")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help=f"Segundos entre peticiones (por defecto {REQUEST_DELAY}).")
    parser.add_argument("--cache", default=DEFAULT_CACHE_PATH,
                        help="JSON de licencias ya leídas (ver el docstring del módulo).")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignora la caché: vuelve a pedir también las fichas leídas hace poco.")
    args = parser.parse_args()

    configure_logging()
    engine = get_engine(args.database_url)
    summary = run(engine, limit=args.limit, delay=args.delay,
                  cache_path=None if args.no_cache else args.cache)
    print(
        f"ACB fichas: {summary['updated']} jugadores actualizados de {summary['fetched']} fichas leídas "
        f"({summary['candidates']} con huecos; {summary['not_found']} sin ficha, {summary['failed']} fallos, "
        f"{summary['skipped_recent']} saltados por caché). Campos rellenados: {summary['filled']}"
    )


if __name__ == "__main__":
    main()
