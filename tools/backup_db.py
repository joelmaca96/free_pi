"""Copia de seguridad de la base de datos de scouting, con rotación.

Existe porque la costumbre ya estaba —los nombres de los backups que había en
`data/` lo cuentan solos: `bak-fixcoords-...`, `bak-...-barca-fix`,
`bak-pre-player-merge-...`, todos hechos a mano antes de correr algo de
`tools/` que escribe— pero sin nada que la sostuviera: ni un solo `tools/*.py`
mencionaba el backup, así que se hacía de memoria, con `cp`, y no se borraba
nunca. Diecisiete copias, 493 MB, en una Raspberry Pi.

Dos cosas que un `cp` a mano no da:

1. **Copia consistente de una BD viva.** El esquema corre en modo WAL (ver
   `packages/baskonia_core/db/scouting/engine.py`), donde el fichero `.db` por
   sí solo NO es la base de datos entera: parte de los datos está en el `-wal`
   todavía sin integrar. Copiar solo el `.db` mientras la ingesta escribe da
   una copia a medias que parece válida. `sqlite3.Connection.backup()` usa la
   API de backup en línea de SQLite, que sí es consistente aunque haya un
   escritor a la vez — y de paso escribe un fichero único, sin `-wal` suelto.

2. **Rotación.** Se queda con las `--keep` más recientes y borra el resto, así
   que el directorio no crece sin freno.

Uso:
    # Antes de tocar nada: copia etiquetada + rotación
    .venv/Scripts/python.exe tools/backup_db.py --label pre-player-merge

    # Solo limpiar lo viejo, sin crear copia nueva
    .venv/Scripts/python.exe tools/backup_db.py --prune-only

    # Ver qué haría, sin tocar el disco
    .venv/Scripts/python.exe tools/backup_db.py --prune-only --dry-run
"""
import argparse
import datetime as dt
import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Añade la raíz del proyecto al sys.path para poder importar packages.*.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.baskonia_core import config  # noqa: E402

#: Sufijo de los backups, el mismo que ya usaban los hechos a mano — para que
#: la rotación reconozca también los antiguos y no empiece de cero.
_SUFFIX = ".bak-"

#: Cuántas copias se conservan por defecto. Tres cubre el caso real (deshacer
#: la última operación, y la anterior por si la última ya venía mal) sin
#: llenar el disco: cada copia pesa lo que la base de datos entera.
DEFAULT_KEEP = 3

#: Etiquetas: solo lo que puede ir en un nombre de fichero sin sorpresas.
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")

#: Ficheros satélite de SQLite. Los backups viejos, hechos con `cp` estando la
#: BD en modo WAL, arrastraron consigo su `-wal` y su `-shm` (que es justo la
#: copia a medias que este script existe para evitar). NO son copias: son
#: pedazos de una. Tratarlos como copias sueltas era un borrado catastrófico
#: — pesan unos KB y son recientes, así que al ordenar por fecha se quedaban
#: con las tres plazas de `--keep` y mandaban al borrado a los tres backups
#: de verdad. Se excluyen del recuento y se borran o se conservan CON su
#: copia (ver `_with_sidecars`).
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def database_path(database_url: Optional[str] = None) -> Path:
    """Ruta en disco de la BD a partir de una URL SQLAlchemy de SQLite.

    Raises:
        ValueError: si la URL no es de SQLite sobre fichero (`:memory:`
            incluido) — este script copia ficheros, no otra cosa.
    """
    url = database_url or config.DATABASE_URL
    if not url.startswith("sqlite:"):
        raise ValueError(f"Solo se sabe hacer copia de SQLite, y la URL es: {url}")
    # `sqlite://` a secas es la BD EN MEMORIA, no una ruta vacía: hay que
    # exigir el separador, no partir por él y quedarse con lo que salga (que
    # sin separador es la URL entera, y acababa devolviendo `Path('sqlite://')`).
    if "///" not in url:
        raise ValueError(f"La URL no apunta a un fichero: {url}")
    raw = url.split("///", 1)[1]
    if not raw or raw == ":memory:":
        raise ValueError(f"La URL no apunta a un fichero: {url}")
    return Path(raw)


def existing_backups(db_path: Path) -> List[Path]:
    """Backups de `db_path` que ya hay en su directorio, del más reciente al más antiguo.

    Sin sus ficheros satélite (ver `_SIDECAR_SUFFIXES`): aquí se cuentan
    COPIAS, no ficheros.

    Ordenados por fecha de modificación y NO por nombre: los backups hechos a
    mano no siguen un solo formato de nombre (`bak-20260828-220108` frente a
    `bak-fixcoords-20260824150631`), así que ordenar por nombre mezclaría
    etiquetas con fechas y "el más reciente" saldría mal.
    """
    candidates = [
        path
        for path in db_path.parent.glob(f"{db_path.name}{_SUFFIX}*")
        if not path.name.endswith(_SIDECAR_SUFFIXES)
    ]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _with_sidecars(backup: Path) -> List[Path]:
    """Una copia y los ficheros satélite que la acompañen, si los hay."""
    found = [backup.parent / f"{backup.name}{suffix}" for suffix in _SIDECAR_SUFFIXES]
    return [backup] + [path for path in found if path.exists()]


def create_backup(db_path: Path, label: Optional[str] = None) -> Path:
    """Copia consistente de `db_path`, devuelta como ruta del fichero creado.

    Usa la API de backup en línea de SQLite (ver docstring del módulo): la BD
    puede estar en uso, y el `-wal` se integra en la copia.
    """
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    clean = _SAFE_LABEL.sub("-", label).strip("-") if label else ""
    name = f"{db_path.name}{_SUFFIX}{stamp}" + (f"-{clean}" if clean else "")
    target = db_path.parent / name

    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target.as_posix())
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def prune(db_path: Path, keep: int, *, dry_run: bool = False) -> Tuple[List[Path], List[Path]]:
    """Borra los backups sobrantes y devuelve `(conservados, borrados)`.

    `keep` es cuántos se conservan de los MÁS RECIENTES. Con `keep <= 0` no se
    borra nada: quedarse sin ninguna copia nunca es lo que se quería pedir, y
    un `--keep 0` es más probable que sea un dedazo que una intención.
    """
    backups = existing_backups(db_path)
    if keep <= 0:
        return backups, []
    kept, doomed = backups[:keep], backups[keep:]
    if not dry_run:
        for backup in doomed:
            for path in _with_sidecars(backup):
                path.unlink()
    return kept, doomed


def _mb(path: Path) -> float:
    """Tamaño de una copia en MB, satélites incluidos (son parte de ella)."""
    return sum(p.stat().st_size for p in _with_sidecars(path)) / 1e6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--label", default=None, help="Etiqueta para el nombre, p.ej. 'pre-player-merge'.")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"Copias a conservar (por defecto {DEFAULT_KEEP}).")
    parser.add_argument("--prune-only", action="store_true", help="No crea copia nueva: solo rota las que hay.")
    parser.add_argument("--dry-run", action="store_true", help="Enseña qué haría, sin tocar el disco.")
    args = parser.parse_args()

    db_path = database_path(args.database_url)
    if not db_path.exists():
        raise SystemExit(f"No existe la base de datos: {db_path}")

    if not args.prune_only:
        if args.dry_run:
            print(f"[dry-run] copiaría {db_path} ({_mb(db_path):.0f} MB)")
        else:
            created = create_backup(db_path, args.label)
            print(f"Copia creada: {created.name} ({_mb(created):.0f} MB)")

    # El tamaño se mide ANTES de borrar: después, `stat()` sobre un fichero ya
    # borrado revienta.
    freed = sum(_mb(p) for p in existing_backups(db_path)[args.keep:]) if args.keep > 0 else 0.0
    kept, removed = prune(db_path, args.keep, dry_run=args.dry_run)

    verb = "se borrarían" if args.dry_run else "borradas"
    if removed:
        print(f"{len(removed)} copias {verb} ({freed:.0f} MB):")
        for path in removed:
            print(f"  - {path.name}")
    print(f"{len(kept)} copias conservadas:")
    for path in kept:
        print(f"  · {path.name} ({_mb(path):.0f} MB)")


if __name__ == "__main__":
    main()
