"""Avatar/escudo con degradación a iniciales cuando falta la imagen real.

Patrón compartido entre la foto de jugador (`players.photo_url`/
`photo_local_path`, con fuente real vía `ingest/baskonia_web/`) y el escudo
de equipo (`teams.logo_url`, columna nueva sin ninguna fuente que la puebla
todavía — ver `local/features/003-vista-plantilla/01_design.md` §9). En
ambos casos la regla es la misma: si no hay ninguna imagen disponible, un
badge con las iniciales sobre verde Baskonia, nunca un hueco vacío ni un
icono de imagen rota.

FOTO LOCAL VS. REMOTA: desde que `ingest/baskonia_web/scraper.py::
download_player_photos` descarga las fotos a disco, `players.photo_local_path`
es la fuente preferida — evita que pintar la plantilla dependa de que
baskonia.com esté arriba (ver `doc/features/ingestor/01_estado.md`,
sección baskonia_web). Streamlit no sirve `data/` como estático, así que la
foto local se lee del disco y se embebe en el propio `<img src="data:...">`
como `data:` URI en vez de una URL — `photo_url` (hotlink remoto) queda como
fallback si la descarga local no existe todavía (primer arranque, jugador
recién fichado sin ingesta reciente) o el fichero no se puede leer.

`photo_local_path` guarda la ruta tal como la escribió el proceso de
ingesta (que corre fuera de Docker, ver `deploy/systemd/`) — no es la ruta
real dentro del contenedor de la interfaz, que monta `data/` en `/data`
(ver `docker-compose.yml`). Por eso `_local_photo_data_uri` solo se fía del
NOMBRE de fichero y lo recompone contra `PLAYER_PHOTOS_DIR` (mismo patrón
que `DATABASE_URL`: relativo para desarrollo local, absoluto en el
contenedor).
"""
import base64
import functools
import html
import os
from pathlib import Path
from typing import Optional

_ACCENT = "#008300"  # verde Baskonia — mismo accent que el resto de la interfaz

# Ver docstring del módulo: por defecto relativo a la raíz del repo (igual
# que `BASKONIA_WEB_PHOTOS_DIR` en `ingest/baskonia_web/scraper.py`, para que
# desarrollo local sin Docker funcione sin configurar nada); en
# `docker-compose.yml` se sobreescribe a `/data/player_photos` (el volumen
# `data/` montado de solo lectura).
_PHOTOS_DIR = Path(os.getenv("PLAYER_PHOTOS_DIR", "data/player_photos"))

_MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}


@functools.lru_cache(maxsize=128)
def _local_photo_data_uri(local_path: str) -> Optional[str]:
    """Codifica como `data:` URI el fichero de `local_path` (columna
    `photo_local_path`), o `None` si no se puede leer (ver docstring del
    módulo para por qué no se usa la ruta guardada tal cual).

    Cacheada por proceso (`lru_cache`): la interfaz es de solo lectura y la
    ingesta corre aparte (ver `_TTL` de `app/data/queries.py`), así que no
    hace falta invalidar dentro de la vida de un mismo proceso de Streamlit.
    """
    # `Path` en Linux no separa por `\`; se normaliza a mano para tolerar
    # una ruta guardada por una ingesta corrida en Windows en desarrollo.
    filename = Path(local_path.replace("\\", "/")).name
    if not filename:
        return None
    try:
        raw = (_PHOTOS_DIR / filename).read_bytes()
    except OSError:
        return None
    mime = _MIME_BY_EXTENSION.get(Path(filename).suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _is_missing(value) -> bool:
    """`True` para `None`, cadena vacía y `NaN` (pandas representa un `NULL`
    de SQL como `NaN` en columnas de texto cuando la columna tiene huecos,
    no como `None` — `bool(nan)` es `True`, así que un simple `if value`
    no basta para detectar el hueco)."""
    if value is None or value == "":
        return True
    return isinstance(value, float) and value != value  # NaN != NaN


def _initials(name: str) -> str:
    """Iniciales para el badge: primera letra de hasta dos palabras del nombre."""
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar_html(
    name: str, photo_url: str | None, *, local_path: str | None = None, size: int = 96, shape: str = "circle"
) -> str:
    """HTML de una imagen (o un badge con iniciales si no hay ninguna foto).

    Args:
        name: nombre completo, usado para las iniciales del badge de respaldo
            y como `alt` de la imagen real.
        photo_url: URL remota de la foto/escudo (hotlink), o `None`/cadena vacía.
        local_path: `players.photo_local_path` — copia descargada a disco por
            `ingest/baskonia_web`, preferida sobre `photo_url` cuando se puede leer (ver
            docstring del módulo). `None` para el escudo de equipo, que no tiene copia
            local todavía.
        size: para `"circle"`/`"rounded"`, lado en píxeles del cuadro (foto y
            badge miden lo mismo, para que no salte el layout al degradar).
            Para `"portrait"`, ANCHO en píxeles — el alto se deriva del ratio
            2:3 de las fotos de `ingest/baskonia_web` (ver más abajo).
        shape: `"circle"` (escudo o iniciales en redondo), `"rounded"`
            (escudo de equipo, esquinas suaves) o `"portrait"` (foto de
            jugador de cuerpo entero — ver nota).

            NOTA `"portrait"`: `download_player_photos` guarda fotos de
            1280×1920, exactamente ratio 2:3 (verificado en vivo, ver
            `ingest/baskonia_web/scraper.py`). Un `"circle"` de 96px con
            `object-fit:cover` escala por el ancho y centra verticalmente,
            así que de una foto tan alta recorta la cabeza y dejaba solo el
            torso — de ahí la queja de fotos "demasiado pequeñas y
            recortadas". Al fijar el cuadro también a 2:3, `cover` no tiene
            que recortar nada (encaja perfecto): se ve la foto completa,
            cabeza incluida.

    Returns:
        Fragmento HTML listo para `st.markdown(..., unsafe_allow_html=True)`.
    """
    if shape == "portrait":
        width, height, radius, object_position = size, round(size * 1.5), "12px", "top center"
    else:
        width = height = size
        radius = "50%" if shape == "circle" else "18%"
        object_position = "center"
    safe_name = html.escape(name)

    src = None
    if not _is_missing(local_path):
        src = _local_photo_data_uri(local_path)  # None si el fichero no existe/no se puede leer
    if src is None and not _is_missing(photo_url):
        src = photo_url  # fallback: hotlink remoto (foto aún no descargada, o descarga fallida)

    if src is not None:
        return (
            f'<img src="{html.escape(src)}" alt="{safe_name}" '
            f'style="width:{width}px;height:{height}px;border-radius:{radius};object-fit:cover;'
            f'object-position:{object_position};display:block" />'
        )
    initials = html.escape(_initials(name))
    font_size = max(12, min(width, height) // 2)
    return (
        f'<div title="{safe_name}" style="width:{width}px;height:{height}px;border-radius:{radius};'
        f'background:{_ACCENT};color:#fff;display:flex;align-items:center;justify-content:center;'
        f'font-weight:700;font-size:{font_size}px;font-family:sans-serif">{initials}</div>'
    )


def player_avatar_html(name: str, photo_url: str | None, *, local_path: str | None = None, size: int = 140) -> str:
    """Atajo de `avatar_html` para foto de jugador (retrato de cuerpo entero, ratio 2:3).

    `size` es el ANCHO en píxeles (ver nota `"portrait"` en `avatar_html`).
    """
    return avatar_html(name, photo_url, local_path=local_path, size=size, shape="portrait")


def team_crest_html(name: str, logo_url: str | None, *, size: int = 32) -> str:
    """Atajo de `avatar_html` para escudo de equipo (esquinas redondeadas, no círculo)."""
    return avatar_html(name, logo_url, size=size, shape="rounded")
