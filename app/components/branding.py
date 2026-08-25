"""Escudo del Baskonia empaquetado con la interfaz (marca fija de la app).

A diferencia de `teams.logo_url` (poblado por `ingest/acb/pipeline.py` desde
la API de ACB, que hoy por hoy apunta al lockup CON el wordmark del
patrocinador del dorsal encima del escudo — ver de dónde sale
`app/assets/baskonia_crest.png`, recortado a mano de esa misma imagen para
quedarnos solo con el escudo) la marca de la propia interfaz no depende de
qué fila tenga la tabla `teams` ni de que `static.acb.com` esté arriba: esta
app ES la del Baskonia (título fijo "Baskonia · Scouting", verde de acento
fijo en todas las páginas), así que el escudo de cabecera/favicon/sidebar es
un asset local versionado en el repo, no un hotlink.

Dos formatos porque los consumidores son distintos:
- `CREST_PATH`: ruta de fichero, para `st.set_page_config(page_icon=...)` y
  `st.logo(...)` — ambos aceptan una ruta local y Streamlit se encarga de
  servirla.
- `crest_data_uri()`: `data:` URI en base64, para incrustar en HTML crudo
  (`st.markdown(..., unsafe_allow_html=True)`, como en `components.header`)
  — Streamlit no sirve rutas de fichero arbitrarias dentro de un `<img src>`
  de markdown (mismo motivo que `avatar._local_photo_data_uri` para las
  fotos de jugador).
"""
import base64
import functools
from pathlib import Path

CREST_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "baskonia_crest.png")


@functools.lru_cache(maxsize=1)
def crest_data_uri() -> str:
    """El escudo del Baskonia como `data:` URI, listo para un `<img src=...>`."""
    raw = Path(CREST_PATH).read_bytes()
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
