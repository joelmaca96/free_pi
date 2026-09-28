"""Recalcula `lineup_stints.possessions_for`/`possessions_against` de una BD ya cargada.

Las posesiones por tramo se DERIVAN de lo que ya está guardado
(`play_events` + `lineup_stints` + `game_advanced_stats`, ver
`ingest/common/possessions.py`), así que rellenarlas para los partidos ya
ingeridos no necesita red: basta pasar por cada partido el mismo cálculo que
hace el loader en cada partido nuevo (`ingest/common/loader.py::
_update_stint_possessions`).

Sirve de algo cuando `play_events` YA tiene los tiros tipados (`fg2_made`,
`fg3_missed`, `ft_made`...): hasta que se reingiera con la ingesta de tiros,
cada partido sale como "sin tiros tipados" y sus tramos quedan en NULL — que es
lo correcto, no un fallo (ver "NULL, NO 0" en ese módulo).

Idempotente: sobrescribe siempre las dos columnas de cada tramo del partido.

Uso:
    .venv/Scripts/python.exe tools/backfill_stint_possessions.py              # diagnóstico (dry-run)
    .venv/Scripts/python.exe tools/backfill_stint_possessions.py --apply      # escribe
    .venv/Scripts/python.exe tools/backfill_stint_possessions.py --apply --no-rescale --ft-mode trips
"""
import argparse
import logging
import statistics
import sys
from pathlib import Path
from typing import List, Optional

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.possessions import (  # noqa: E402
    DISCREPANCY_WARN_PCT,
    FT_MODES,
    GamePossessions,
    update_stint_possessions,
)
from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db  # noqa: E402


def backfill(
    conn,
    apply: bool,
    rescale: bool = True,
    ft_mode: str = "factor",
    game_ids: Optional[List[str]] = None,
) -> dict:
    """Recalcula todos los partidos con tramos (o solo `game_ids`) y devuelve el resumen.

    Returns:
        `{"games", "with_shots", "without_shots", "stints_updated", "rescaled",
        "with_reference", "mean_abs_discrepancy_pct", "over_threshold", "applied"}`.
    """
    if game_ids is None:
        game_ids = [row[0] for row in conn.execute(text("SELECT DISTINCT game_id FROM lineup_stints ORDER BY game_id"))]

    results: List[GamePossessions] = [
        update_stint_possessions(conn, game_id, rescale=rescale, ft_mode=ft_mode, dry_run=not apply)
        for game_id in game_ids
    ]
    with_shots = [r for r in results if r.has_shot_events]
    discrepancies = [
        abs(d) for r in with_shots for team in r.estimated if (d := r.discrepancy_pct(team)) is not None
    ]
    return {
        "games": len(results),
        "with_shots": len(with_shots),
        "without_shots": len(results) - len(with_shots),
        "stints_updated": sum(len(r.stints) for r in with_shots),
        "rescaled": sum(1 for r in with_shots if any(abs(s - 1.0) > 1e-9 for s in r.scale.values())),
        "with_reference": sum(1 for r in with_shots if any(v for v in r.reference.values())),
        "mean_abs_discrepancy_pct": round(statistics.mean(discrepancies), 2) if discrepancies else None,
        "over_threshold": sum(1 for d in discrepancies if d > DISCREPANCY_WARN_PCT),
        "applied": apply,
    }


def _print_summary(summary: dict) -> None:
    print(
        f"{summary['games']} partidos con tramos · {summary['with_shots']} con tiros tipados en play_events · "
        f"{summary['without_shots']} sin ellos (sus tramos quedan en NULL)."
    )
    if summary["with_shots"]:
        print(
            f"{summary['stints_updated']} tramos con posesiones · {summary['with_reference']} partidos con "
            f"referencia en game_advanced_stats · {summary['rescaled']} reescalados."
        )
        if summary["mean_abs_discrepancy_pct"] is not None:
            print(
                f"Discrepancia media |estimado − referencia| por equipo y partido: "
                f"{summary['mean_abs_discrepancy_pct']:.1f}% · {summary['over_threshold']} por encima del "
                f"{DISCREPANCY_WARN_PCT:.0f}%."
            )
    if not summary["applied"]:
        print("Dry-run: no se ha escrito nada. Ejecuta de nuevo con --apply para escribir los cambios.")


def main(argv: Optional[List[str]] = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, dry-run).")
    parser.add_argument(
        "--no-rescale", action="store_true",
        help="No reescalar a la referencia de game_advanced_stats (deja la estimación cruda).",
    )
    parser.add_argument("--ft-mode", choices=FT_MODES, default="factor", help="0,44·FTA (factor) o viajes contados (trips).")
    parser.add_argument("--game", action="append", dest="games", help="Solo este partido (repetible).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Registra la comparación partido a partido.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    engine = create_scouting_engine(args.database_url)
    init_scouting_db(engine)  # asegura que existan las columnas nuevas antes de escribir en ellas
    with engine.begin() as conn:
        summary = backfill(
            conn, apply=args.apply, rescale=not args.no_rescale, ft_mode=args.ft_mode, game_ids=args.games
        )
    _print_summary(summary)
    return summary


if __name__ == "__main__":
    main()
