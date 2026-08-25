"""Orquestador de los tres módulos de ingesta.

Orden: primero la plantilla (`baskonia_web`, para que los jugadores del
Baskonia ya existan al insertar boxscores), luego ACB y Euroliga. Un módulo
que falla no detiene a los demás; se reporta al final qué se cargó y qué no.

Uso:
    .venv/Scripts/python.exe -m ingest.run_all --season 2025
    .venv/Scripts/python.exe -m ingest.run_all --season 2025 --skip euroleague
"""
import argparse
import logging

from ingest.common.db import get_engine
from ingest.common.logging_utils import configure_logging

logger = logging.getLogger(__name__)


def run_all(season: int, database_url: str = None, skip: tuple = ()) -> dict:
    """Ejecuta baskonia_web -> acb -> euroleague, en ese orden, y devuelve un resumen."""
    engine = get_engine(database_url)
    results = {}

    if "baskonia_web" not in skip:
        try:
            from ingest.baskonia_web.pipeline import run as run_baskonia_web

            results["baskonia_web"] = {"ok": True, "summary": run_baskonia_web(engine)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("baskonia_web falló")
            results["baskonia_web"] = {"ok": False, "error": str(exc)}
    else:
        results["baskonia_web"] = {"ok": None, "summary": "omitido"}

    if "acb" not in skip:
        try:
            from ingest.acb.pipeline import run as run_acb

            results["acb"] = {"ok": True, "summary": run_acb(engine, season)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("ACB falló")
            results["acb"] = {"ok": False, "error": str(exc)}

        # Calendario futuro (no partidos jugados) — aparte del backfill de arriba a
        # propósito: un fallo aquí (p.ej. ACB no ha publicado aún el calendario de
        # `season`) no debe tumbar los partidos ya finalizados que sí se acaban de
        # cargar. Ver `ingest/acb/pipeline.py::run_upcoming`.
        try:
            from ingest.acb.pipeline import run_upcoming as run_acb_upcoming

            results["acb_upcoming"] = {"ok": True, "summary": run_acb_upcoming(engine, season)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("ACB (calendario futuro) falló")
            results["acb_upcoming"] = {"ok": False, "error": str(exc)}
    else:
        results["acb"] = {"ok": None, "summary": "omitido"}
        results["acb_upcoming"] = {"ok": None, "summary": "omitido"}

    if "euroleague" not in skip:
        try:
            from ingest.euroleague.pipeline import run as run_euroleague

            results["euroleague"] = {"ok": True, "summary": run_euroleague(engine, season)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Euroliga falló")
            results["euroleague"] = {"ok": False, "error": str(exc)}

        # Igual que `acb_upcoming` arriba: calendario futuro aparte del backfill de
        # partidos jugados, para que un fallo aquí no tumbe lo que sí se acaba de
        # cargar. Ver `ingest/euroleague/pipeline.py::run_upcoming`.
        try:
            from ingest.euroleague.pipeline import run_upcoming as run_euroleague_upcoming

            results["euroleague_upcoming"] = {"ok": True, "summary": run_euroleague_upcoming(engine, season)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Euroliga (calendario futuro) falló")
            results["euroleague_upcoming"] = {"ok": False, "error": str(exc)}
    else:
        results["euroleague"] = {"ok": None, "summary": "omitido"}
        results["euroleague_upcoming"] = {"ok": None, "summary": "omitido"}

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Año de inicio de temporada (p.ej. 2025).")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--skip", action="append", default=[], choices=["acb", "euroleague", "baskonia_web"],
        help="Omite un módulo (se puede repetir).",
    )
    args = parser.parse_args()

    configure_logging()
    results = run_all(args.season, args.database_url, tuple(args.skip))

    print(f"\nResumen de ingesta (temporada {args.season}):")
    for name, result in results.items():
        if result["ok"] is None:
            print(f"  {name}: omitido")
        elif result["ok"]:
            print(f"  {name}: OK -> {result['summary']}")
        else:
            print(f"  {name}: FALLÓ -> {result['error']}")


if __name__ == "__main__":
    main()
