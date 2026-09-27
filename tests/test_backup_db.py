"""Tests de `tools/backup_db.py`: copia consistente + rotación.

La rotación borra ficheros, así que lo que se prueba aquí es sobre todo lo
que NO se puede permitir que se tuerza: qué cuenta como copia, qué se
conserva y qué se lleva por delante cada borrado. El caso del `-wal` no es
hipotético — con los backups reales de `data/`, tratarlo como una copia más
mandaba al borrado a los tres backups de verdad.

Todo sobre `tmp_path`: nunca se toca `data/baskonia.db`.
"""
import os
import sqlite3
import time

import pytest

from tools import backup_db


def _make_db(path, rows=3):
    conn = sqlite3.connect(path.as_posix())
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO games (id) VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def _touch(path, *, age_seconds):
    """Fichero vacío con una antigüedad concreta — la rotación ordena por mtime."""
    path.write_bytes(b"")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


# --- ruta de la base de datos ---------------------------------------------


def test_database_path_reads_a_sqlite_url(tmp_path):
    assert backup_db.database_path(f"sqlite:///{(tmp_path / 'x.db').as_posix()}").name == "x.db"


@pytest.mark.parametrize("url", ["postgresql://host/db", "sqlite:///:memory:", "sqlite://"])
def test_database_path_refuses_what_it_cannot_copy(url):
    """Este script copia FICHEROS: una BD en memoria o en otro motor no es un error que
    convenga descubrir a mitad del borrado."""
    with pytest.raises(ValueError):
        backup_db.database_path(url)


# --- copia ------------------------------------------------------------------


def test_the_backup_is_a_real_and_readable_database(tmp_path):
    db = _make_db(tmp_path / "baskonia.db", rows=5)

    copy = backup_db.create_backup(db)

    assert copy.exists() and copy != db
    conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 5
    conn.close()


def test_a_wal_database_is_copied_whole(tmp_path):
    """El motivo de existir del script: en modo WAL, parte de los datos vive en el `-wal` y
    un `cp` del `.db` a secas se deja lo último escrito. La API de backup de SQLite no."""
    db = _make_db(tmp_path / "baskonia.db", rows=2)
    live = sqlite3.connect(db.as_posix())
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO games (id) VALUES (99)")
    live.commit()

    copy = backup_db.create_backup(db)  # con el escritor todavía abierto
    live.close()

    conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM games WHERE id = 99").fetchone()[0] == 1
    conn.close()


def test_the_label_ends_up_in_the_name_without_breaking_it(tmp_path):
    db = _make_db(tmp_path / "baskonia.db")

    copy = backup_db.create_backup(db, "pre player/merge")

    assert "pre-player-merge" in copy.name
    assert "/" not in copy.name


# --- rotación ---------------------------------------------------------------


def test_prune_keeps_the_newest_and_deletes_the_rest(tmp_path):
    db = _make_db(tmp_path / "baskonia.db")
    old = [_touch(tmp_path / f"baskonia.db.bak-{i}", age_seconds=1000 - i) for i in range(5)]

    kept, removed = backup_db.prune(db, keep=2)

    assert [p.name for p in kept] == [old[4].name, old[3].name]
    assert len(removed) == 3
    assert all(not p.exists() for p in removed)
    assert all(p.exists() for p in kept)


def test_prune_never_touches_the_database_itself(tmp_path):
    db = _make_db(tmp_path / "baskonia.db")
    _touch(tmp_path / "baskonia.db.bak-1", age_seconds=10)

    backup_db.prune(db, keep=0)

    assert db.exists()


def test_keep_zero_deletes_nothing(tmp_path):
    """Quedarse sin ninguna copia no es algo que se pida a propósito: es un dedazo."""
    db = _make_db(tmp_path / "baskonia.db")
    backups = [_touch(tmp_path / f"baskonia.db.bak-{i}", age_seconds=100 - i) for i in range(3)]

    kept, removed = backup_db.prune(db, keep=0)

    assert removed == []
    assert len(kept) == 3
    assert all(p.exists() for p in backups)


def test_dry_run_reports_without_deleting(tmp_path):
    db = _make_db(tmp_path / "baskonia.db")
    backups = [_touch(tmp_path / f"baskonia.db.bak-{i}", age_seconds=100 - i) for i in range(4)]

    kept, removed = backup_db.prune(db, keep=1, dry_run=True)

    assert len(kept) == 1 and len(removed) == 3
    assert all(p.exists() for p in backups)  # nada borrado


# --- ficheros satélite ------------------------------------------------------


def test_wal_and_shm_files_are_not_counted_as_backups(tmp_path):
    """Con los backups reales de `data/`, contar el `-wal` como copia era catastrófico: pesa
    unos KB y es reciente, así que se quedaba con las plazas de `--keep` y mandaba al borrado
    a los backups de verdad."""
    db = _make_db(tmp_path / "baskonia.db")
    real = _touch(tmp_path / "baskonia.db.bak-20260828", age_seconds=500)
    _touch(tmp_path / "baskonia.db.bak-20260828-wal", age_seconds=1)   # más reciente
    _touch(tmp_path / "baskonia.db.bak-20260828-shm", age_seconds=1)

    assert [p.name for p in backup_db.existing_backups(db)] == [real.name]


def test_deleting_a_backup_takes_its_sidecars_with_it(tmp_path):
    """Un `-wal` huérfano no vale para nada y confunde al siguiente que mire el directorio."""
    db = _make_db(tmp_path / "baskonia.db")
    _touch(tmp_path / "baskonia.db.bak-nueva", age_seconds=1)
    doomed = _touch(tmp_path / "baskonia.db.bak-vieja", age_seconds=900)
    doomed_wal = _touch(tmp_path / "baskonia.db.bak-vieja-wal", age_seconds=900)

    backup_db.prune(db, keep=1)

    assert not doomed.exists()
    assert not doomed_wal.exists()


def test_a_kept_backup_keeps_its_sidecars(tmp_path):
    db = _make_db(tmp_path / "baskonia.db")
    kept = _touch(tmp_path / "baskonia.db.bak-nueva", age_seconds=1)
    kept_wal = _touch(tmp_path / "baskonia.db.bak-nueva-wal", age_seconds=1)
    _touch(tmp_path / "baskonia.db.bak-vieja", age_seconds=900)

    backup_db.prune(db, keep=1)

    assert kept.exists() and kept_wal.exists()
