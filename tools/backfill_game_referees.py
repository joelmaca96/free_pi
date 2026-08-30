"""Rellena `game_referees` a partir de `games.referees` ya cargada.

`game_referees` es una tabla ADITIVA (Fase 5, perfil arbitral —
doc/features/propuestas/05_perfil_arbitral.md): `init_scouting_db()` la crea
sola en cualquier `data/baskonia.db` ya inicializada
(`packages/baskonia_core/db/scouting/engine.py::_ADDITIVE_TABLES`), pero
vacía — igual que `lineup_stints`/`play_events` cuando se añadieron. Poblarla
para los partidos YA cargados no necesita red ni volver a descargar nada
(`games.referees` ya tiene la terna cruda, 736 de 737 partidos verificado en
vivo): basta trocear esa cadena con el mismo troceador que usa el loader en
cada partido nuevo (`ingest/common/loader.py::_replace_game_referees`) y
reutilizarlo aquí sobre lo que ya hay. Reingerir toda la temporada para
conseguir lo mismo sería tirar tiempo y llamadas HTTP a la basura.

Idempotente: `_replace_game_referees` borra por `game_id` antes de reinsertar,
así que ejecutar esta herramienta dos veces (o después de una reingesta
normal, que ya llama a `_replace_game_referees` por su cuenta) no duplica
ninguna fila.

Uso:
    .venv/Scripts/python.exe tools/backfill_game_referees.py            # diagnóstico
    .venv/Scripts/python.exe tools/backfill_game_referees.py --apply    # escribe
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.loader import _replace_game_referees  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db  # noqa: E402


def _backfill(conn, apply: bool) -> None:
    rows = conn.execute(text("SELECT id, referees FROM games WHERE referees IS NOT NULL")).all()
    already = conn.execute(text("SELECT COUNT(DISTINCT game_id) FROM game_referees")).scalar_one()
    print(f"{len(rows)} partidos con terna cruda en `games.referees` · {already} ya tienen `game_referees` cargada.")
    if not rows:
        print("Nada que hacer.")
        return
    if not apply:
        print("Ejecuta de nuevo con --apply para escribir los cambios.")
        return

    for game_id, referees in rows:
        _replace_game_referees(conn, game_id, referees)

    distinct_referees = conn.execute(text("SELECT COUNT(DISTINCT referee_name) FROM game_referees")).scalar_one()
    total_rows = conn.execute(text("SELECT COUNT(*) FROM game_referees")).scalar_one()
    print(f"Cargadas {total_rows} filas de {len(rows)} partidos · {distinct_referees} árbitros distintos (canonicalizados).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, solo diagnostica).")
    args = parser.parse_args()

    engine = create_scouting_engine(args.database_url)
    init_scouting_db(engine)  # asegura que `game_referees` exista antes de escribir en ella
    with engine.begin() as conn:
        _backfill(conn, apply=args.apply)


if __name__ == "__main__":
    main()
