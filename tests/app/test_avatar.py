"""Tests de `app/components/avatar.py`: resolución foto local -> URL remota -> iniciales.

`app/` no tiene tests hasta ahora (es la interfaz Streamlit, sin lógica de red);
`avatar.py` es la única pieza con lógica pura (sin `st.*`) que merece cobertura
directa — construye HTML a partir de datos ya cargados, no dibuja nada en pantalla.
"""
import base64

import pytest

import app.components.avatar as avatar_module
from app.components.avatar import avatar_html, player_avatar_html, team_crest_html


@pytest.fixture(autouse=True)
def _isolate_photos_dir(tmp_path, monkeypatch):
    """Aísla `_PHOTOS_DIR` (normalmente `PLAYER_PHOTOS_DIR`/`data/player_photos`) a un
    directorio temporal por test, y limpia el `lru_cache` de `_local_photo_data_uri`
    entre tests — la caché vive por proceso y sin esto un test podría ver el resultado
    cacheado de otro test que usara el mismo nombre de fichero."""
    monkeypatch.setattr(avatar_module, "_PHOTOS_DIR", tmp_path)
    avatar_module._local_photo_data_uri.cache_clear()
    yield
    avatar_module._local_photo_data_uri.cache_clear()


def test_avatar_html_prefers_local_photo_over_remote_url(tmp_path):
    (tmp_path / "145.png").write_bytes(b"fake-png-bytes")

    html = avatar_html("Markus Howard", "https://www.baskonia.com/uploads/howard.png", local_path="145.png")

    expected_b64 = base64.b64encode(b"fake-png-bytes").decode("ascii")
    assert f"data:image/png;base64,{expected_b64}" in html
    assert "baskonia.com" not in html  # no debe quedar el hotlink si hay copia local


def test_avatar_html_falls_back_to_remote_url_when_local_missing():
    html = avatar_html("Markus Howard", "https://www.baskonia.com/uploads/howard.png", local_path="no-existe.png")

    assert 'src="https://www.baskonia.com/uploads/howard.png"' in html
    assert "data:image" not in html


def test_avatar_html_shows_initials_badge_when_nothing_available():
    html = avatar_html("Markus Howard", None, local_path=None)

    assert "<img" not in html
    assert "MH" in html  # iniciales


def test_avatar_html_tolerates_windows_style_local_path(tmp_path):
    """`photo_local_path` puede venir con separadores `\\` si la ingesta corrió en
    Windows (dev) — solo debe importar el nombre de fichero, no la ruta completa."""
    (tmp_path / "150.jpg").write_bytes(b"fake-jpg-bytes")

    html = avatar_html("Tadas Sedekerskis", None, local_path="data\\player_photos\\150.jpg")

    expected_b64 = base64.b64encode(b"fake-jpg-bytes").decode("ascii")
    assert f"data:image/jpeg;base64,{expected_b64}" in html


def test_player_avatar_html_is_portrait_2x3_and_forwards_local_path(tmp_path):
    """Retrato de cuerpo entero (ratio 2:3, ver nota "portrait" en `avatar_html`
    — las fotos descargadas por `ingest/baskonia_web` son 1280x1920), no un
    círculo — a diferencia de `team_crest_html`, que sí es redondo/rounded."""
    (tmp_path / "923.webp").write_bytes(b"fake-webp-bytes")

    html = player_avatar_html("Damion Baugh", None, local_path="923.webp")

    assert "border-radius:12px" in html
    assert "data:image/webp;base64," in html


def test_team_crest_html_has_no_local_path_and_uses_rounded_shape():
    html = team_crest_html("Baskonia", None)

    assert "border-radius:18%" in html
    assert "<img" not in html  # sin logo_url ni copia local -> badge de iniciales


def test_missing_local_file_does_not_raise_and_logs_nothing_fatal(tmp_path):
    """Un `local_path` que apunta a un fichero corrupto/no legible no debe tumbar el
    render — degrada al fallback remoto o al badge, nunca lanza excepción."""
    unreadable_dir = tmp_path / "150.jpg"
    unreadable_dir.mkdir()  # existe como ruta pero no es un fichero legible

    html = avatar_html("Tadas Sedekerskis", "https://example.com/x.jpg", local_path="150.jpg")

    assert 'src="https://example.com/x.jpg"' in html
