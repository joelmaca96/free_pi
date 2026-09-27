"""Reintenta SOLO los partidos que faltan en `games` para una temporada — sin
repetir la temporada entera (`ingest.run_all`), que releería y reintentaría
también los cientos de partidos que ya cargaron bien la primera vez.

Usa `discover_missing_games()` de cada pipeline (barato: solo relee el
calendario, no vuelve a descargar boxscore/tiros de lo que ya está) para
encontrar qué falta, y `run_single_game()` para cargar cada uno — mismo
cliente reutilizado dentro de cada fuente, así que el calendario que ambos
pasos necesitan se pide una sola vez (cacheado en el cliente).

OJO: un partido que falta puede ser (a) un fallo real (rate-limit, timeout) —
este script SÍ lo arregla si la fuente ya no está devolviendo 429 — o (b) un
partido fuera de alcance a propósito (ver `ValueError` de
`ingest/acb/adapter.py::_COMPETITION_BY_ID`) — este script lo va a reintentar
igual y va a volver a fallar exactamente igual, es esperado, no es un bug.

Uso:
    .venv/bin/python tools/retry_missing_games.py --season 2025
    .venv/bin/python tools/retry_missing_games.py --season 2025 --skip euroleague
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.acb.client import AcbClient  # noqa: E402
from ingest.acb.pipeline import discover_missing_games as acb_missing  # noqa: E402
from ingest.acb.pipeline import run_single_game as acb_run_single  # noqa: E402
from ingest.common.db import get_engine  # noqa: E402
from ingest.common.logging_utils import configure_logging  # noqa: E402
from ingest.euroleague.client import EuroleagueClient  # noqa: E402
from ingest.euroleague.pipeline import discover_missing_games as euroleague_missing  # noqa: E402
from ingest.euroleague.pipeline import run_single_game as euroleague_run_single  # noqa: E402

logger = logging.getLogger(__name__)


def _retry_source(name, missing_ids, run_single, engine, season) -> dict:
    summary = {"loaded": [], "failed": []}
    logger.info("%s %s: %d partido(s) pendiente(s) por reintentar", name, season, len(missing_ids))
    for game_id in missing_ids:
        result = run_single(engine, season, game_id)
        # `run_single_game` ya devuelve su propio {"loaded"/"failed": [...]}
        # de un solo elemento (mismo contrato que `run()`, ver su docstring).
        summary["loaded"] += result.get("loaded", [])
        summary["failed"] += result.get("failed", [])
    logger.info(
        "%s %s: %d recuperados, %d siguen fallando", name, season, len(summary["loaded"]), len(summary["failed"])
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Año de inicio de temporada (p.ej. 2025).")
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument(
        "--skip", action="append", default=[], choices=["acb", "euroleague"],
        help="Omite una fuente (repetible).",
    )
    args = parser.parse_args()
    configure_logging()

    engine = get_engine(args.database_url)
    results = {}

    if "acb" not in args.skip:
        client = AcbClient()
        missing = acb_missing(engine, args.season, client=client)
        results["acb"] = _retry_source(
            "ACB", missing, lambda e, s, gid: acb_run_single(e, s, gid, client=client), engine, args.season
        )

    if "euroleague" not in args.skip:
        client = EuroleagueClient()
        missing = euroleague_missing(engine, args.season, client=client)
        results["euroleague"] = _retry_source(
            "Euroliga", missing, lambda e, s, gid: euroleague_run_single(e, s, gid, client=client),
            engine, args.season,
        )

    print(f"\nResumen del reintento (temporada {args.season}):")
    for name, result in results.items():
        print(f"  {name}: {len(result['loaded'])} recuperados, {len(result['failed'])} siguen fallando")
        if result["failed"]:
            print(f"    fallidos: {result['failed']}")


if __name__ == "__main__":
    main()
