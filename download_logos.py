"""Descarga los escudos de los equipos de la ACB (Endesa) y la EuroLeague.

Usa la API de Wikipedia para listar las imágenes de cada página de equipo,
filtra las que parecen escudos/logos, y las descarga a `assets/logos/<slug>.<ext>`.

Uso:
    python download_logos.py
"""
import re
import time
from pathlib import Path

import requests

# --- Configuración ---
LOGOS_DIR = Path(__file__).parent / "assets" / "logos"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
WIKI_API = "https://en.wikipedia.org/w/api.php"
REQUEST_DELAY = 3.0  # segundos entre peticiones (evitar rate-limit)
MAX_RETRIES = 5

# Mapa slug -> nombre de la página de Wikipedia en inglés
# (slug es el usado por BBR / config.TEAMS)
TEAM_WIKI = {
    # --- ACB / Endesa ---
    "vitoria": "Saski Baskonia",
    "bilbao": "Bilbao Basket",
    "canarias": "CB Canarias",
    "zaragoza": "Basket Zaragoza",
    "fundacion-granada": "Fundación CB Granada",
    "murcia": "UCAM Murcia CB",
    "forca-lleida": "Força Lleida CE",
    "gran-canaria": "CB Gran Canaria",
    "unicaja-malaga": "Baloncesto Málaga",
    "miraflores": "CB Miraflores",
    "breogan": "CB Breogán",
    "andorra": "BC Andorra",
    "basquet-girona": "Bàsquet Girona",
    "manresa": "Bàsquet Manresa",
    "joventut": "Joventut Badalona",
    "valencia": "Valencia Basket",
    "barcelona": "FC Barcelona Basket",
    "real-madrid": "Real Madrid Baloncesto",
    # --- EuroLeague ---
    "olympiakos": "Olympiacos B.C.",
    "villeurbanne": "ASVEL Basket",
    "panathinaikos": "Panathinaikos B.C.",
    "paris-basket": "Paris Basketball",
    "partizan": "KK Partizan",
    "red-star": "KK Crvena zvezda",
    "dubai": "Dubai Basketball Club",
    "anadolu-efes": "Anadolu Efes S.K.",
    "virtus-bologna": "Virtus Bologna",
    "hapoel-tel-aviv": "Hapoel Tel Aviv B.C.",
    "maccabi-tel-aviv": "Maccabi Tel Aviv B.C.",
    "bayern-muenchen": "FC Bayern Munich (basketball)",
    "zalgiris": "BC Žalgiris",
    "milano": "Olimpia Milano",
    "monaco": "AS Monaco Basket",
    "ulker-fenerbahce": "Fenerbahçe S.K. (basketball)",
}

# Palabras clave para identificar un escudo/logo
LOGO_KEYWORDS = ("logo", "crest", "escudo", "shield", "badge", "emblem")
# Palabras que descartan una imagen (no son escudos)
EXCLUDE_KEYWORDS = (
    "flag", "kit", "map", "commons-logo", "cruz", "cross", "icon",
    "wikimedia", "stadium", "arena", "pabellon", "pabellón", "photo",
    "jpg", "jpeg",  # las fotos suelen ser .jpg/.jpeg
    "basketball current event", "basketball pictogram", "football pictogram",
    "question book", "sports icon", "symbol category", "oojs",
)


def _session() -> requests.Session:
    """Crea una sesión HTTP con cabeceras de usuario."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _api_get(session: requests.Session, params: dict) -> dict:
    """Petición GET a la API de Wikipedia con reintentos ante rate-limit."""
    for attempt in range(MAX_RETRIES):
        resp = session.get(WIKI_API, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            wait = 10 * (attempt + 1)
            print(f"    (rate-limit, esperando {wait}s...)")
            time.sleep(wait)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"API de Wikipedia no disponible tras {MAX_RETRIES} intentos")


def _get_page_images(session: requests.Session, page_title: str) -> list:
    """Devuelve la lista de nombres de archivo de imágenes de una página."""
    params = {
        "action": "query",
        "titles": page_title,
        "prop": "images",
        "format": "json",
        "redirects": 1,
        "imlimit": 100,
    }
    data = _api_get(session, params)
    images = []
    for page in data.get("query", {}).get("pages", {}).values():
        for img in page.get("images", []):
            images.append(img["title"])
    return images


def _pick_logo_filename(images: list, page_title: str) -> "str | None":
    """Elige el archivo que parece el escudo del equipo entre las imágenes."""
    # Normalizar el nombre de la página para comparar (sin sufijos como B.C.)
    page_norm = re.sub(r"[^a-z0-9]", "", page_title.lower())
    # Quitar sufijos comunes de nombres de página
    for suffix in ("bc", "sk", "kk", "club", "basketball"):
        page_norm = page_norm.replace(suffix, "")

    # Filtrar imágenes que claramente no son escudos
    candidates = []
    for img in images:
        name = img.lower()
        if any(ex in name for ex in EXCLUDE_KEYWORDS):
            continue
        candidates.append(img)

    # 1. Preferir archivos con "logo"/"crest"/"escudo" en el nombre
    for img in candidates:
        name = img.lower()
        if any(kw in name for kw in LOGO_KEYWORDS):
            return img

    # 2. Buscar archivos cuyo nombre coincida con el de la página
    for img in candidates:
        name = re.sub(r"[^a-z0-9]", "", img.lower())
        if page_norm and page_norm in name:
            return img

    # 3. Preferir archivos SVG (los escudos suelen ser SVG)
    for img in candidates:
        if img.lower().endswith(".svg"):
            return img

    # 4. Cualquier imagen restante que sea un archivo de imagen
    for img in candidates:
        name = img.lower()
        if name.endswith((".svg", ".png")):
            return img

    return None


def _resolve_image_url(session: requests.Session, filename: str) -> "str | None":
    """Resuelve la URL directa de un archivo (en Wikipedia o Commons)."""
    params = {
        "action": "query",
        "titles": filename,
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    data = _api_get(session, params)
    for page in data.get("query", {}).get("pages", {}).values():
        imageinfo = page.get("imageinfo", [])
        if imageinfo and imageinfo[0].get("url"):
            return imageinfo[0]["url"]
    return None


def _download(session: requests.Session, url: str, dest: Path) -> bool:
    """Descarga una imagen a `dest`. Devuelve True si tuvo éxito."""
    clean_url = url.split("?")[0]
    for attempt in range(MAX_RETRIES):
        resp = session.get(clean_url, timeout=60)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            if content_type.startswith("image/"):
                dest.write_bytes(resp.content)
                return True
            return False
        if resp.status_code == 429:
            wait = 5 * (attempt + 1)
            print(f"    (rate-limit en descarga, esperando {wait}s...)")
            time.sleep(wait)
            continue
        return False
    return False


def _ext_from_filename(filename: str) -> str:
    """Extrae la extensión de un nombre de archivo."""
    m = re.search(r"\.(svg|png|jpe?g|gif|webp)$", filename, re.IGNORECASE)
    return m.group(1).lower() if m else "png"


def download_team_logo(session: requests.Session, slug: str, page_title: str) -> "Path | None":
    """Descarga el escudo de un equipo. Devuelve la ruta guardada o None."""
    # 1. Listar imágenes de la página
    images = _get_page_images(session, page_title)
    if not images:
        print(f"  [SKIP] {slug}: sin imágenes en la página")
        return None

    # 2. Elegir el archivo que parece el escudo
    filename = _pick_logo_filename(images, page_title)
    if not filename:
        print(f"  [SKIP] {slug}: no se identificó un escudo entre {len(images)} imágenes")
        return None

    # 3. Resolver la URL directa
    url = _resolve_image_url(session, filename)
    if not url:
        print(f"  [SKIP] {slug}: no se pudo resolver URL de {filename}")
        return None

    # 4. Descargar
    ext = _ext_from_filename(filename)
    dest = LOGOS_DIR / f"{slug}.{ext}"
    if _download(session, url, dest):
        print(f"  [OK]   {slug} -> {dest.name}  ({filename})")
        return dest

    print(f"  [FAIL] {slug}: no se pudo descargar {url}")
    return None


def main() -> None:
    """Descarga todos los escudos."""
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    session = _session()

    print(f"Descargando {len(TEAM_WIKI)} escudos a {LOGOS_DIR}...")
    ok = 0
    for slug, page in TEAM_WIKI.items():
        # Comprobar si ya existe un logo para este equipo
        existing = [p for p in LOGOS_DIR.glob(f"{slug}.*") if p.suffix.lower() in (".svg", ".png", ".jpg", ".jpeg")]
        if existing:
            print(f"  [EXIST] {slug} -> {existing[0].name}")
            ok += 1
            continue
        try:
            if download_team_logo(session, slug, page):
                ok += 1
        except Exception as exc:  # noqa: BLE001 - continuar con el siguiente equipo
            print(f"  [ERROR] {slug}: {exc}")
        time.sleep(REQUEST_DELAY)

    print(f"\nCompletado: {ok}/{len(TEAM_WIKI)} escudos disponibles.")


if __name__ == "__main__":
    main()
