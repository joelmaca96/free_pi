"""Tests de `ingest.euroleague.roster`: ficha física (altura/peso/nacimiento/nacionalidad).

Sin red: el payload de abajo es la forma REAL que devuelve
`/v2/competitions/E/seasons/E2025/clubs/{code}/people`, recortada a lo que
usa el módulo (verificada en vivo contra `BAS` el 2026-09-10).

Lo que se prueba es lo que duele si se tuerce: que el cruce de ids acierte
(el `person.code` de esta API lleva un `P` de más respecto al `Player_ID` del
boxscore, que es lo que hay guardado), que no invente jugadores y que rellene
huecos sin pisar lo que ya escribió otra fuente.
"""
import pytest
from sqlalchemy import text

from ingest.euroleague.roster import run, update_player_profiles

#: Un jugador con ficha completa, uno con la ficha a medias, y cuerpo técnico
#: (que no debe tocarse). `type` distingue jugador ("J") de lo demás.
PEOPLE = [
    {
        "type": "J",
        "person": {
            "code": "011948",
            "name": "HOWARD, MARKUS",
            "height": 178,
            "weight": 79,
            "birthDate": "1999-03-03T00:00:00",
            "country": {"code": "USA", "name": "United States of America"},
        },
    },
    {
        "type": "J",
        "person": {
            "code": "099999",
            "name": "SIN FICHA, JUGADOR",
            "height": None,
            "weight": 0,  # la API usa 0 para "sin rellenar"
            "birthDate": None,
            "country": {},
        },
    },
    {
        "type": "E",  # entrenador: no es un jugador
        "person": {"code": "000001", "name": "IVANOVIC, DUSKO", "height": 190, "weight": 90},
    },
]


@pytest.fixture()
def linked_player(engine):
    """El seed trae a `howard`; se le añade el alias de Euroliga que el boxscore crearía."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO player_external_ids (player_id, source, external_id)"
                " VALUES ('howard', 'euroleague', 'P011948')"
            )
        )
    return "howard"


def _player(engine, player_id):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT height_cm, weight_kg, birth_date, nationality FROM players WHERE id = :id"),
            {"id": player_id},
        ).first()


def test_height_and_weight_land_on_the_right_player(engine, linked_player):
    """El cruce va por `player_external_ids`, y el id de esta API lleva un `P` de más respecto
    al `Player_ID` del boxscore (`011948` aquí, `P011948` guardado)."""
    with engine.begin() as conn:
        result = update_player_profiles(conn, PEOPLE)

    assert result["players"] == 1  # solo el que tiene alias; el otro jugador y el entrenador, no
    row = _player(engine, linked_player)
    assert row.height_cm == 178
    assert row.weight_kg == 79
    assert row.birth_date == "1999-03-03"  # la API la da con hora, la columna es DATE


def test_it_never_creates_a_player(engine):
    """Sin alias no se toca nada: crear identidad desde aquí metería una cuarta vía de creación
    de jugadores, que es justo de donde vienen los problemas de identidad."""
    before = engine.connect().execute(text("SELECT COUNT(*) FROM players")).scalar()

    with engine.begin() as conn:
        result = update_player_profiles(conn, PEOPLE)

    assert result["players"] == 0
    assert engine.connect().execute(text("SELECT COUNT(*) FROM players")).scalar() == before


def test_it_fills_gaps_without_overwriting_another_source(engine, linked_player):
    """baskonia.com da la nacionalidad en español ('EE.UU.') y esta API en inglés ('United
    States of America'): dejar ganar a la última ingesta dejaba la columna en dos idiomas.
    Pasó de verdad la primera vez que se corrió esto."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE players SET nationality = 'EE.UU.' WHERE id = 'howard'"))
        update_player_profiles(conn, PEOPLE)

    row = _player(engine, linked_player)
    assert row.nationality == "EE.UU."   # lo que ya había, intacto
    assert row.height_cm == 178          # y el hueco, relleno


def test_an_empty_profile_is_left_alone(engine):
    """`height: null` y `weight: 0` son 'sin rellenar' en esta API, no un jugador de 0 kg."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO player_external_ids (player_id, source, external_id)"
                " VALUES ('howard', 'euroleague', 'P099999')"
            )
        )
        update_player_profiles(conn, PEOPLE)

    row = _player(engine, "howard")
    assert row.height_cm is None
    assert row.weight_kg is None


def test_a_broken_club_does_not_stop_the_others(engine, linked_player):
    """Mismo principio que el resto del pipeline: una fuente parcial es mejor que ninguna."""

    class FlakyClient:
        def fetch_clubs(self, season):
            return [{"code": "ROTO"}, {"code": "BAS"}]

        def fetch_club_people(self, season, club_code):
            if club_code == "ROTO":
                raise RuntimeError("503")
            return PEOPLE

    summary = run(engine, 2025, client=FlakyClient(), download_photos=False)

    assert summary == {"clubs": 1, "players": 1, "photos": 0}
    assert _player(engine, linked_player).height_cm == 178


# --- fotos ------------------------------------------------------------------
# La foto vive en `images` del MIEMBRO, no de `person` (que viene vacío). Hasta
# esta fuente, cualquier jugador que no fuera del Baskonia salía con el badge
# de iniciales, porque baskonia.com solo cubre la plantilla propia.

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

PEOPLE_WITH_PHOTO = [
    {
        "type": "J",
        "images": {"headshot": "https://media-cdn.example/abc.png", "action": "https://media-cdn.example/abc.png"},
        "person": {"code": "011948", "name": "HOWARD, MARKUS", "height": 178, "weight": 79, "country": {}},
    }
]


class _FakeResponse:
    def __init__(self, content=PNG, content_type="image/png"):
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass


class _FakeSession:
    """Sesión de mentira: cuenta descargas y no toca la red (la suite es offline)."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response or _FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._response


def test_the_headshot_is_downloaded_and_linked(engine, linked_player, tmp_path):
    session = _FakeSession()
    with engine.begin() as conn:
        result = update_player_profiles(conn, PEOPLE_WITH_PHOTO, photos_dir=tmp_path, session=session)

    assert result["photos"] == 1
    # `.jpg`, no `.png`: se guarda reducida (ver `shrink_photo` y el test de abajo).
    assert (tmp_path / "P011948.jpg").exists()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT photo_url, photo_local_path FROM players WHERE id = 'howard'")
        ).first()
    assert row.photo_url == "https://media-cdn.example/abc.png"
    assert row.photo_local_path.endswith("P011948.jpg")


def test_a_photo_already_on_disk_is_not_downloaded_again(engine, linked_player, tmp_path):
    """350 jugadores son 350 peticiones al CDN la primera vez y ninguna las siguientes.
    Vale con que exista con CUALQUIERA de las extensiones soportadas — las bajadas antes de
    que se redujeran siguen siendo `.png` y no hay que volver a pedirlas."""
    (tmp_path / "P011948.png").write_bytes(PNG)
    session = _FakeSession()

    with engine.begin() as conn:
        update_player_profiles(conn, PEOPLE_WITH_PHOTO, photos_dir=tmp_path, session=session)

    assert session.calls == []


def test_the_club_photo_wins_over_the_euroleague_headshot(engine, linked_player, tmp_path):
    """La del club es de cuerpo entero y con la equipación de la temporada; el headshot es de
    busto. Misma regla de 'rellena huecos' que el resto de campos."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE players SET photo_local_path = 'player_photos/145.jpg' WHERE id = 'howard'")
        )
        update_player_profiles(conn, PEOPLE_WITH_PHOTO, photos_dir=tmp_path, session=_FakeSession())

    with engine.connect() as conn:
        path = conn.execute(text("SELECT photo_local_path FROM players WHERE id = 'howard'")).scalar()
    assert path == "player_photos/145.jpg"


def test_something_that_is_not_an_image_is_discarded(engine, linked_player, tmp_path):
    """Un CDN que devuelve una página de error con 200 no debe dejar un .png que no lo es."""
    session = _FakeSession(_FakeResponse(b"<html>error</html>", "text/html"))

    with engine.begin() as conn:
        result = update_player_profiles(conn, PEOPLE_WITH_PHOTO, photos_dir=tmp_path, session=session)

    assert result["photos"] == 0
    assert list(tmp_path.iterdir()) == []


def test_without_photos_dir_nothing_is_written(engine, linked_player, tmp_path):
    """`download_photos=False`: la ficha entra igual y no se toca ni disco ni red."""
    with engine.begin() as conn:
        result = update_player_profiles(conn, PEOPLE_WITH_PHOTO, photos_dir=None)

    assert result["photos"] == 0
    assert list(tmp_path.iterdir()) == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT height_cm FROM players WHERE id = 'howard'")).scalar() == 178


# --- reducción de la foto -----------------------------------------------------
# `app/components/avatar.py` EMBEBE la foto en el HTML como `data:` URI (Streamlit
# no sirve `data/` como estático), así que el peso del fichero es peso de página,
# por cada jugador en pantalla. Medido con las fotos a tamaño original: 19
# jugadores de Olympiacos = 15,5 MB de HTML por carga, para pintarlas a 140 px.


def _png(width, height):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), (200, 30, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_big_photo_is_scaled_down():
    from ingest.euroleague.roster import _MAX_PHOTO_WIDTH, shrink_photo

    import io

    from PIL import Image

    shrunk = shrink_photo(_png(750, 1000))  # el tamaño real que sirve el CDN

    assert shrunk is not None
    image = Image.open(io.BytesIO(shrunk))
    assert image.width == _MAX_PHOTO_WIDTH
    assert image.height == round(1000 * _MAX_PHOTO_WIDTH / 750)  # mantiene la proporción
    assert image.format == "JPEG"


def test_a_small_photo_is_not_enlarged():
    """Reducir es el objetivo; agrandar solo añadiría peso sin añadir detalle."""
    import io

    from PIL import Image

    from ingest.euroleague.roster import shrink_photo

    shrunk = shrink_photo(_png(120, 160))

    assert Image.open(io.BytesIO(shrunk)).width == 120


def test_transparency_is_flattened_onto_white():
    """Los headshots vienen en RGBA con fondo transparente y JPEG no tiene alfa: sin
    componer, el fondo saldría negro sobre el blanco de la ficha."""
    import io

    from PIL import Image

    from ingest.euroleague.roster import shrink_photo

    transparent = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(transparent, format="PNG")

    image = Image.open(io.BytesIO(shrink_photo(transparent.getvalue())))

    assert image.mode == "RGB"
    assert image.getpixel((5, 5)) == (255, 255, 255)


def test_something_that_is_not_an_image_is_not_shrunk():
    """Devuelve `None` para que el llamante guarde el original en vez de reventar."""
    from ingest.euroleague.roster import shrink_photo

    assert shrink_photo(b"<html>no soy una imagen</html>") is None
