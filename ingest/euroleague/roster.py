"""Ficha de jugador (altura, peso, nacimiento, nacionalidad y FOTO) desde Euroliga.

POR QUÉ ESTA FUENTE Y NO OTRA. `players.height_cm` llevaba desde el principio
en el esquema y estaba a 0 de 946: no la escribía nadie. Revisadas las tres
fuentes del proyecto, ninguna de las que ya se usaban la trae — el JSON
embebido de baskonia.com no tiene ningún campo de altura (lo dice su propio
scraper, verificado en vivo) y el boxscore de ACB tampoco (101 claves, ni una
de ficha; tampoco hay endpoint de jugador en `api2.acb.com`, probadas las
rutas plausibles y todas 404). La API de plantillas de Euroliga sí:
`person.height` en centímetros y `person.weight` en kilos, más `birthDate` y
`country`.

QUÉ CUBRE Y QUÉ NO. Solo jugadores de clubes de Euroliga — que en esta base
de datos son 350 de 947, incluido el Baskonia entero y todos sus rivales
europeos. Los equipos que solo juegan ACB los cubre desde 2026-09-28
`ingest/acb/profiles.py`, que lee la ficha de la web de acb.com con estas
mismas reglas (solo actualiza, rellena huecos).

POSICIÓN (2026-09-28). El boxscore de Euroliga no trae posición, así que un
jugador que solo ha jugado Euroliga quedaba con `position = ''` (unos 450).
Esta API sí la da, pero solo en tres categorías (Guard/Forward/Center): se
rellena el hueco con la etiqueta de la app más cercana, sin pisar nunca una
posición ya guardada. La tabla y sus consecuencias, en `_POSITION_BY_NAME`.

NACIONALIDAD EN CASTELLANO (2026-09-28). `country.name` viene en inglés; se
traduce con `_COUNTRY_ES` antes de rellenar el hueco, para que lo que
escribe esta fuente case con lo de acb.com y baskonia.com ("EE.UU."). Lo ya
guardado en inglés por pasadas anteriores no se toca (regla de abajo).

SOLO ACTUALIZA, NUNCA CREA. Este módulo no resuelve identidad: cruza por
`player_external_ids` (`source='euroleague'`) y actualiza la fila que ya
existe. Un jugador de la plantilla que todavía no haya disputado ningún
partido no tiene alias y se salta — aparecerá en la siguiente ingesta,
cuando el boxscore lo cree. Es deliberado: crear jugadores desde aquí
metería una cuarta vía de creación de identidad (la que más problemas ha
dado, ver `ingest/common/identity.py`) a cambio de adelantar unos días un
dato de ficha.

RELLENA HUECOS, NO PISA. `COALESCE(campo, :nuevo)` y no al revés: lo que ya
hay gana. Para altura y peso da igual (nadie más los escribe), pero para
nacionalidad y fecha de nacimiento no: baskonia.com los da en español y con
el criterio del club ("Eslovenia", "EE.UU."), y esta API en inglés
("Slovenia", "United States of America"). Dejar ganar a la última ingesta
que pasara volvía la columna una mezcla de dos idiomas — pasó de verdad la
primera vez que se corrió esto. Para una app en castellano, el dato del club
es el bueno; esta fuente completa a los 900 y pico jugadores que no lo
tenían (y desde 2026-09-28 lo escribe ya traducido, ver arriba).

FOTOS. `images.headshot` del MIEMBRO (no de `person`, que viene vacío) es un
PNG real de 750×1000 en el CDN de Euroliga. Hasta ahora la única fuente de
fotos era baskonia.com, que solo cubre la plantilla propia: cualquier rival
salía con el badge de iniciales. Se descargan a disco por el mismo motivo y
al mismo sitio que las del Baskonia (`data/player_photos/`, ver
`ingest/baskonia_web/scraper.py::download_player_photos`) — servir la
interfaz no debe depender de que el CDN esté arriba — y el nombre de fichero
lleva el `external_id` de Euroliga (`P014207.png`), que no choca con el id
numérico corto que usa baskonia.com.

Las fotos del club GANAN a las de Euroliga para la plantilla propia, por la
misma regla de "rellena huecos": son de cuerpo entero y con la equipación de
la temporada, mientras que el headshot es de busto. Y ojo, no lo arreglan
todo: un fichaje muy reciente no tiene foto en NINGUNA de las dos (la
temporada E2026 existe en la API con la plantilla al día pero con cero
headshots todavía), así que el badge de iniciales sigue haciendo falta.
"""
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import requests
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .client import EuroleagueClient

logger = logging.getLogger(__name__)

SOURCE = "euroleague"

#: Prefijo que separa el `person.code` de la API de plantillas del
#: `Player_ID` del boxscore, que es lo que guarda `player_external_ids`
#: (`P014207` contra `014207`). Comprobado contra la plantilla del Baskonia:
#: 21 de 23 casaban al añadirlo, y los 2 que no eran altas sin partido.
_EXTERNAL_ID_PREFIX = "P"

#: Mismo directorio que las fotos de baskonia.com — `app/components/avatar.py`
#: solo se queda con el NOMBRE del fichero y lo recompone contra
#: `PLAYER_PHOTOS_DIR`, así que las dos fuentes conviven sin saber la una de
#: la otra. La variable de entorno es la misma que ya usa `baskonia_web`.
DEFAULT_PHOTOS_DIR = os.getenv("BASKONIA_WEB_PHOTOS_DIR", "data/player_photos")
_PHOTO_TIMEOUT = 30

#: Ancho al que se guardan los headshots. El CDN los sirve a 750×1000 y ~600 KB
#: cada uno, y la interfaz los pinta a 140 px de ancho — pero `avatar.py` los
#: EMBEBE en el HTML como `data:` URI (Streamlit no sirve `data/` como
#: estático), así que el tamaño del fichero es tamaño de página: medido, la
#: pantalla de "Próximo rival" con 19 jugadores se iba a **15,5 MB de HTML por
#: carga**. En una Raspberry Pi detrás de un túnel eso no se puede usar. A
#: 320 px de ancho y JPEG quedan en ~25 KB sin diferencia visible al tamaño al
#: que se muestran (320 da margen para pantallas de densidad doble).
_MAX_PHOTO_WIDTH = 320
_JPEG_QUALITY = 85

_EXTENSION_BY_CONTENT_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def _existing_photo(photos_dir: Path, external_id: str) -> Optional[Path]:
    """Foto ya descargada para `external_id`, con cualquier extensión soportada.

    Es lo que hace idempotente la descarga: 350 jugadores son 350 peticiones
    al CDN la primera vez y ninguna las siguientes.
    """
    for ext in _EXTENSION_BY_CONTENT_TYPE.values():
        candidate = photos_dir / f"{external_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def shrink_photo(raw: bytes) -> Optional[bytes]:
    """Reduce la imagen a `_MAX_PHOTO_WIDTH` de ancho y la devuelve como JPEG.

    `None` si no se puede (sin Pillow, o el binario no es una imagen legible):
    el llamante guarda entonces el original. Que la ingesta siga funcionando
    en una máquina sin Pillow importa — el venv de la Pi no lo tiene, porque
    la interfaz corre en su propio contenedor y hasta ahora nada de `ingest/`
    necesitaba tratar imágenes.

    JPEG y no PNG a propósito: son fotos, no gráficos, y el PNG de origen es
    RGBA (fondo transparente). Se compone sobre blanco, que es el fondo real
    sobre el que se ven en la ficha y en la galería.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning(
            "euroleague: sin Pillow, las fotos se guardan a tamaño original "
            "(~600 KB cada una; `pip install Pillow` para reducirlas)"
        )
        return None

    import io

    try:
        image = Image.open(io.BytesIO(raw))
        if image.width > _MAX_PHOTO_WIDTH:
            height = round(image.height * _MAX_PHOTO_WIDTH / image.width)
            image = image.resize((_MAX_PHOTO_WIDTH, height), Image.LANCZOS)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            flattened = Image.new("RGB", image.size, (255, 255, 255))
            flattened.paste(image, mask=image.split()[-1])
            image = flattened
        else:
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - una foto ilegible no debe parar la ingesta
        logger.warning("euroleague: no se pudo reducir una foto (%s); se guarda el original", exc)
        return None


def download_headshot(
    external_id: str,
    url: str,
    photos_dir: Path,
    session: Optional[requests.Session] = None,
) -> Optional[Path]:
    """Descarga el headshot a `photos_dir`; devuelve la ruta, o `None` si no se pudo.

    Un fallo puntual (CDN caído, contenido que no es imagen) se registra y
    devuelve `None`: la ficha del jugador entra igual sin foto, y el resto de
    la plantilla no se ve afectado. Mismo principio que
    `baskonia_web.scraper.download_player_photos`, incluida la escritura
    atómica — si el proceso muere a media descarga, `_existing_photo` no
    puede encontrarse un PNG truncado y darlo por bueno para siempre.
    """
    existing = _existing_photo(photos_dir, external_id)
    if existing is not None:
        return existing

    session = session or requests.Session()
    try:
        response = session.get(url, timeout=_PHOTO_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("euroleague: no se pudo descargar la foto de %s (%s): %s", external_id, url, exc)
        return None

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        logger.warning("euroleague: %s no devolvió una imagen (Content-Type=%s)", url, content_type)
        return None

    # Reducida si se puede (ver `shrink_photo` y `_MAX_PHOTO_WIDTH`); el
    # original solo si Pillow no está o la imagen no se deja leer.
    shrunk = shrink_photo(response.content)
    if shrunk is not None:
        payload, extension = shrunk, ".jpg"
    else:
        payload = response.content
        extension = _EXTENSION_BY_CONTENT_TYPE.get(content_type, ".png")

    photos_dir.mkdir(parents=True, exist_ok=True)
    target = photos_dir / f"{external_id}{extension}"
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(payload)
    tmp.replace(target)
    return target


#: Posición de Euroliga -> etiqueta de la app. La API solo distingue TRES
#: puestos (`positionName` Guard/Forward/Center, `position` 1/2/3; comprobado
#: en vivo el 2026-09-28 en las 18 plantillas de E2025: 164/130/89 jugadores,
#: y ni `/v2/people/{code}` ni el XML de `/v1/players` dan nada más fino), y
#: la app usa CINCO ('Base','Escolta','Alero','Ala-pívot','Pívot', comparadas
#: por igualdad exacta en `app/analytics/impact.py::_position_ok` y en los
#: suelos de `app/analytics/minutes_plan.py`). Lo que se decide y por qué:
#:
#: - Center -> 'Pívot'. Sin pérdida.
#: - Forward -> 'Alero'. Pierde el 'Ala-pívot': un 4 de Euroliga cuenta como
#:   alero en los filtros. No afecta a ningún suelo (el planificador solo
#:   exige 'Base' y 'Pívot').
#: - Guard -> 'Base', no 'Escolta'. Pierde el 'Escolta', y a propósito hacia
#:   el lado permisivo: con 'Escolta', un rival solo-Euroliga no tendría
#:   NINGÚN base y el filtro de quintetos "al menos un Base" no devolvería
#:   nada (y `applicable_position_floors` diría "sin bases disponibles", que
#:   es falso). Con 'Base', ese filtro pasa a significar "al menos un
#:   exterior" para esos jugadores — más laxo, pero no vacío ni engañoso.
#:   Consecuencia para el planificador de minutos (solo plantilla propia): el
#:   suelo de 'Base' lo cubriría cualquier escolta que hubiera recibido la
#:   posición por esta vía. En la práctica no pasa, porque la plantilla
#:   propia tiene la posición de baskonia.com y del `gameRole` de ACB, que
#:   ganan siempre (esto solo rellena huecos).
#:
#: Y el boxscore de ACB PISA la posición en cada ingesta
#: (`identity.resolve_or_create_player`), así que un jugador que acabe
#: jugando ACB cambia esta etiqueta gruesa por la buena sin hacer nada.
_POSITION_BY_NAME = {"guard": "Base", "forward": "Alero", "center": "Pívot"}
_POSITION_BY_CODE = {1: "Base", 2: "Alero", 3: "Pívot"}


def _member_position(member: Dict) -> Optional[str]:
    """`positionName` (o, si falta, el código `position`) en el vocabulario de la app."""
    name = (member.get("positionName") or "").strip().lower()
    if name in _POSITION_BY_NAME:
        return _POSITION_BY_NAME[name]
    code = member.get("position")
    return _POSITION_BY_CODE.get(code) if isinstance(code, int) else None


#: País de la API (inglés) -> castellano, con el criterio de acb.com y
#: baskonia.com ("EE.UU."). Cubre todos los países que aparecen en las
#: plantillas de E2025 (comprobado en vivo el 2026-09-28) y unos pocos
#: habituales más. Solo afecta a lo que ESTE módulo escribe en un hueco: no
#: pisa nada ya guardado (ver "RELLENA HUECOS, NO PISA"), así que los valores
#: en inglés escritos antes de existir este mapa siguen ahí. Un país que no
#: esté en la lista entra tal cual, en inglés: mejor el dato que el hueco.
_COUNTRY_ES = {
    "United States of America": "EE.UU.", "United States": "EE.UU.", "USA": "EE.UU.",
    "France": "Francia", "Serbia": "Serbia", "Spain": "España", "Italy": "Italia",
    "Germany": "Alemania", "Greece": "Grecia", "Lithuania": "Lituania", "Israel": "Israel",
    "Turkiye": "Turquía", "Turkey": "Turquía", "Canada": "Canadá", "Latvia": "Letonia",
    "Brazil": "Brasil", "Slovenia": "Eslovenia", "Senegal": "Senegal", "Nigeria": "Nigeria",
    "North Macedonia": "Macedonia del Norte", "Bosnia and Herzegovina": "Bosnia y Herzegovina",
    "Denmark": "Dinamarca", "Georgia": "Georgia", "Czech Republic": "República Checa",
    "Czechia": "República Checa", "Mali": "Mali", "Finland": "Finlandia", "Croatia": "Croacia",
    "Angola": "Angola", "Bahamas": "Bahamas", "Bulgaria": "Bulgaria", "Australia": "Australia",
    "United Kingdom": "Reino Unido", "Great Britain": "Reino Unido", "Burkina Faso": "Burkina Faso",
    "Gabon": "Gabón", "Ireland": "Irlanda", "Guinea": "Guinea", "Colombia": "Colombia",
    "Cameroon": "Camerún", "Belgium": "Bélgica", "Ukraine": "Ucrania",
    "Russian Federation": "Rusia", "Russia": "Rusia", "Cabo Verde": "Cabo Verde",
    "Cape Verde": "Cabo Verde", "Ivory Coast": "Costa de Marfil",
    "Dominican Republic": "República Dominicana", "Hungary": "Hungría",
    "Montenegro": "Montenegro", "Argentina": "Argentina", "Puerto Rico": "Puerto Rico",
    "Poland": "Polonia", "Netherlands": "Países Bajos", "Sweden": "Suecia", "Estonia": "Estonia",
    "Uruguay": "Uruguay", "Austria": "Austria", "Portugal": "Portugal", "Mexico": "México",
    "Belarus": "Bielorrusia", "Venezuela": "Venezuela", "Congo": "Congo",
    "DR Congo": "República Democrática del Congo", "South Sudan": "Sudán del Sur",
    "Sudan": "Sudán", "Egypt": "Egipto", "Japan": "Japón", "China": "China",
    "New Zealand": "Nueva Zelanda", "Switzerland": "Suiza", "Slovakia": "Eslovaquia",
    "Romania": "Rumanía", "Iceland": "Islandia", "Norway": "Noruega", "Jamaica": "Jamaica",
    "Cyprus": "Chipre", "Kosovo": "Kosovo", "Albania": "Albania", "Lebanon": "Líbano",
}


def _country_es(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = name.strip()
    return _COUNTRY_ES.get(name, name)


def _person_fields(person: Dict) -> Dict[str, Optional[object]]:
    """Los cuatro campos de ficha, ya en las unidades de la base de datos."""
    country = person.get("country") or {}
    birth = person.get("birthDate")
    return {
        # La API los da como número; se fuerza a int para no meter un float
        # en una columna INTEGER, y se descarta el 0 (ficha sin rellenar).
        "height_cm": int(person["height"]) if person.get("height") else None,
        "weight_kg": int(person["weight"]) if person.get("weight") else None,
        # `birthDate` viene ISO con hora ("1997-02-14T00:00:00") y la columna
        # es DATE: se recorta, igual que hace baskonia_web con su `birthday`.
        "birth_date": birth.split("T")[0] if birth else None,
        # En castellano cuando se conoce el país (ver `_COUNTRY_ES`).
        "nationality": _country_es(country.get("name")),
    }


def update_player_profiles(
    conn: Connection,
    people: List[Dict],
    *,
    photos_dir: Optional[Path] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, int]:
    """Actualiza la ficha de los jugadores de `people` que ya existan.

    Args:
        people: salida de `EuroleagueClient.fetch_club_people` (miembros de un
            club; el cuerpo técnico se descarta aquí, no en el cliente).
        photos_dir: dónde dejar los headshots. `None` no descarga ninguno —
            así los tests de la parte de ficha no tocan disco ni red.

    Returns:
        `{"players": fichas actualizadas, "photos": fotos nuevas en disco,
        "positions": posiciones en blanco rellenadas}`.
    """
    updated = 0
    photos = 0
    positions = 0
    for member in people:
        if member.get("type") != "J":  # J = jugador; el resto es cuerpo técnico
            continue
        person = member.get("person") or {}
        code = person.get("code")
        if not code:
            continue

        external_id = f"{_EXTERNAL_ID_PREFIX}{code}"
        row = conn.execute(
            text(
                "SELECT player_id FROM player_external_ids"
                " WHERE source = :source AND external_id = :external_id"
            ),
            {"source": SOURCE, "external_id": external_id},
        ).first()
        if row is None:
            continue  # sin alias todavía: ver "SOLO ACTUALIZA, NUNCA CREA"

        fields = _person_fields(person)

        # La foto vive en `images` del MIEMBRO, no de `person` (ahí viene
        # vacío) — ver el docstring del módulo.
        headshot_url = (member.get("images") or {}).get("headshot")
        local_path = None
        if headshot_url and photos_dir is not None:
            downloaded = download_headshot(external_id, headshot_url, photos_dir, session)
            if downloaded is not None:
                local_path = str(downloaded)
                photos += 1

        position = _member_position(member)
        if position is not None:
            # Solo si está en blanco (`NOT NULL`, '' = sin dato): la posición
            # de baskonia.com y la del `gameRole` de ACB son más finas que
            # estas tres categorías y ganan siempre (ver `_POSITION_BY_NAME`).
            positions += conn.execute(
                text(
                    "UPDATE players SET position = :position"
                    " WHERE id = :id AND (position IS NULL OR TRIM(position) = '')"
                ),
                {"position": position, "id": row[0]},
            ).rowcount

        if not any(value is not None for value in fields.values()) and not headshot_url:
            continue  # ficha vacía en la API: no se toca nada

        conn.execute(
            text(
                # El campo ya guardado va PRIMERO en cada COALESCE: esto
                # rellena huecos, no pisa (ver el docstring del módulo). Vale
                # también para la foto: la del club gana a la de Euroliga.
                "UPDATE players SET"
                " height_cm = COALESCE(height_cm, :height_cm),"
                " weight_kg = COALESCE(weight_kg, :weight_kg),"
                " birth_date = COALESCE(birth_date, :birth_date),"
                " nationality = COALESCE(nationality, :nationality),"
                " photo_url = COALESCE(photo_url, :photo_url),"
                " photo_local_path = COALESCE(photo_local_path, :photo_local_path)"
                " WHERE id = :id"
            ),
            {**fields, "photo_url": headshot_url, "photo_local_path": local_path, "id": row[0]},
        )
        updated += 1
    return {"players": updated, "photos": photos, "positions": positions}


def run(
    engine: Engine,
    season: int,
    client: Optional[EuroleagueClient] = None,
    *,
    download_photos: bool = True,
    photos_dir: str = DEFAULT_PHOTOS_DIR,
) -> Dict[str, int]:
    """Recorre los clubes de la temporada y actualiza la ficha de sus jugadores.

    Un club que falle (API caída, código retirado a mitad de temporada) se
    registra y se salta: la ficha de los demás sí entra. Mismo principio que
    `pipeline.run` con los partidos.

    Args:
        download_photos: descargar los headshots a disco. `False` deja solo
            los campos de ficha (mismo interruptor que `--skip-photos` en la
            CLI de baskonia_web, y por el mismo motivo: la primera pasada son
            varios cientos de peticiones al CDN).

    Returns:
        `{"clubs": …, "players": fichas actualizadas, "photos": fotos nuevas,
        "positions": posiciones en blanco rellenadas}`.
    """
    client = client or EuroleagueClient()
    clubs = client.fetch_clubs(season)
    target_dir = Path(photos_dir) if download_photos else None
    # Una sola sesión para todo el CDN: son cientos de fotos contra el mismo
    # host y abrir una conexión por foto es tirar el keep-alive.
    session = requests.Session() if download_photos else None

    totals = {"clubs": 0, "players": 0, "photos": 0, "positions": 0}
    for club in clubs:
        code = club.get("code")
        if not code:
            continue
        try:
            people = client.fetch_club_people(season, code)
        except Exception as exc:  # noqa: BLE001 - un club roto no debe tumbar el resto
            logger.warning("euroleague: no se pudo leer la plantilla de %s (%s)", code, exc)
            continue
        totals["clubs"] += 1
        with engine.begin() as conn:
            result = update_player_profiles(conn, people, photos_dir=target_dir, session=session)
        totals["players"] += result["players"]
        totals["photos"] += result["photos"]
        totals["positions"] += result["positions"]

    logger.info(
        "euroleague: ficha actualizada en %d jugadores de %d clubes (%d fotos nuevas, "
        "%d posiciones en blanco rellenadas)",
        totals["players"], totals["clubs"], totals["photos"], totals["positions"]
    )
    return totals
