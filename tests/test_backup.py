"""Tests para la copia de seguridad de `apps/ingest/pipeline.py`.

`backfill_league`, `backfill_season` y `scout_team` copian la base de datos
antes de escribir nada. Se cubren los dos fallos que tenía esa copia: reventar
si la base de datos aún no existe, y copiar el `.db` sin consolidar el WAL
(perdiendo en silencio las escrituras recientes).
"""
import sqlite3

import pytest

from apps.ingest import pipeline


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Apunta `config.DATABASE_URL` a una BD sqlite temporal (aún inexistente)."""
    path = tmp_path / "baskonia.db"
    monkeypatch.setattr(pipeline.config, "DATABASE_URL", f"sqlite:///{path}")
    return path


def test_backup_returns_none_when_db_missing(db_path):
    """Sin base de datos previa no hay nada que copiar: devuelve None, no falla.

    Regresión: `shutil.copy2` lanzaba `FileNotFoundError` fuera del `try` de
    `backfill_season`/`scout_team`, así que la primera ejecución de esos
    comandos moría con traceback.
    """
    assert not db_path.exists()
    assert pipeline._backup_database() is None


def test_backup_copies_existing_db(db_path):
    """Con base de datos existente, crea una copia con los mismos datos."""
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE t (v TEXT)")
    connection.execute("INSERT INTO t VALUES ('antes')")
    connection.commit()
    connection.close()

    backup_path = pipeline._backup_database()

    assert backup_path is not None
    copy = sqlite3.connect(backup_path)
    assert copy.execute("SELECT v FROM t").fetchall() == [("antes",)]
    copy.close()


def test_backup_includes_wal_writes(db_path):
    """La copia incluye las escrituras que aún viven en el WAL.

    Regresión: la BD se abre en modo WAL, así que `shutil.copy2` del `.db` a
    secas dejaba fuera todo lo que no se hubiera consolidado todavía.
    """
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t (v TEXT)")
    connection.execute("INSERT INTO t VALUES ('en-wal')")
    connection.commit()
    # No se cierra: el commit vive en el -wal, no en el .db.

    backup_path = pipeline._backup_database()
    connection.close()

    assert backup_path is not None
    copy = sqlite3.connect(backup_path)
    assert copy.execute("SELECT v FROM t").fetchall() == [("en-wal",)]
    copy.close()


def test_backup_never_overwrites_source(db_path):
    """La copia es un fichero nuevo, distinto del original."""
    sqlite3.connect(db_path).close()
    backup_path = pipeline._backup_database()
    assert backup_path != str(db_path)
    assert db_path.exists()


def test_backup_rejects_non_sqlite_url(monkeypatch):
    """Solo se soporta sqlite: cualquier otro backend es un error explícito."""
    monkeypatch.setattr(pipeline.config, "DATABASE_URL", "postgresql://localhost/x")
    with pytest.raises(RuntimeError, match="solo sqlite"):
        pipeline._backup_database()


def test_log_backup_handles_none(caplog):
    """`_log_backup(None)` informa de que no hubo copia, sin romper."""
    with caplog.at_level("INFO"):
        pipeline._log_backup(None)
    assert "Sin base de datos previa" in caplog.text
