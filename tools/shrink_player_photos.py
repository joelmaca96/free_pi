"""Reduce las fotos de jugador ya descargadas y actualiza `players.photo_local_path`.

`ingest/euroleague/roster.py` ya guarda los headshots reducidos desde que
existe `shrink_photo`, pero las que se bajaron antes siguen en disco a tamaño
original. Este script las pone al día sin volver a pedirlas al CDN.

POR QUÉ IMPORTA EL TAMAÑO DEL FICHERO. `app/components/avatar.py` no sirve la
foto por URL: Streamlit no publica `data/` como estático, así que la lee del
disco y la EMBEBE en el HTML como `data:` URI. El peso del fichero es peso de
página, multiplicado por cada jugador en pantalla. Medido sobre la plantilla
de Olympiacos en "Próximo rival": 19 jugadores × ~600 KB = **15,5 MB de HTML
por carga**, para pintarlas a 140 px de ancho. En la Raspberry Pi, detrás de
un túnel, eso es inservible.

Cambia la extensión (`.png` -> `.jpg`), así que hay que tocar también
`players.photo_local_path` o la interfaz se queda buscando un fichero que ya
no existe. Las dos cosas van en la misma pasada.

NO toca las fotos de baskonia.com: son de cuerpo entero, ya vienen a un
tamaño razonable y son la plantilla propia (doce jugadores, no trescientos).
Se reconocen porque su nombre de fichero NO empieza por el prefijo de
Euroliga.

Uso:
    .venv/Scripts/python.exe tools/shrink_player_photos.py --dry-run
    .venv/Scripts/python.exe tools/shrink_player_photos.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from ingest.euroleague.roster import DEFAULT_PHOTOS_DIR, shrink_photo  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine  # noqa: E402

#: Solo las de Euroliga (`P014207.png`): las de baskonia.com se llaman con el
#: id numérico corto del club y se dejan como están.
_EUROLEAGUE_PREFIX = "P"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--photos-dir", default=DEFAULT_PHOTOS_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Enseña el ahorro sin tocar nada.")
    args = parser.parse_args()

    photos_dir = Path(args.photos_dir)
    candidates = sorted(
        p for p in photos_dir.glob(f"{_EUROLEAGUE_PREFIX}*")
        if p.is_file() and p.suffix.lower() != ".jpg"
    )
    if not candidates:
        print("Nada que reducir.")
        return

    engine = create_scouting_engine(args.database_url)
    before = after = 0
    converted = []

    for source in candidates:
        raw = source.read_bytes()
        shrunk = shrink_photo(raw)
        if shrunk is None:
            print(f"  no se pudo reducir {source.name}, se deja igual")
            continue
        before += len(raw)
        after += len(shrunk)
        converted.append((source, shrunk))

    print(f"{len(converted)} fotos: {before/1e6:.0f} MB -> {after/1e6:.1f} MB "
          f"({100 * (1 - after / before):.0f}% menos)" if before else "sin datos")

    if args.dry_run:
        print("[dry-run] no se ha tocado nada.")
        return

    for source, payload in converted:
        target = source.with_suffix(".jpg")
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        # `photo_local_path` guarda la ruta tal cual la escribió la ingesta;
        # `avatar.py` solo usa el NOMBRE, pero tiene que coincidir la extensión.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE players SET photo_local_path = :new"
                    " WHERE photo_local_path LIKE :old"
                ),
                {"new": str(target), "old": f"%{source.name}"},
            )
        source.unlink()

    print(f"Hechas. Ahorro: {(before - after)/1e6:.0f} MB en disco y en cada carga de página.")


if __name__ == "__main__":
    main()
