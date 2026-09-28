"""Orquestador de los tres módulos de ingesta.

Orden: primero la plantilla (`baskonia_web`, para que los jugadores del
Baskonia ya existan al insertar boxscores), luego ACB y Euroliga. Un módulo
que falla no detiene a los demás; se reporta al final qué se cargó y qué no.

Uso:
    .venv/Scripts/python.exe -m ingest.run_all --season 2025
    .venv/Scripts/python.exe -m ingest.run_all --season 2025 --skip euroleague
    .venv/Scripts/python.exe -m ingest.run_all --season 2025 --merge-team-duplicates
"""
import argparse
import logging

from ingest.common.db import get_engine
from ingest.common.identity import (
    find_merged_players,
    find_player_identity_collisions,
    find_team_identity_collisions,
    find_team_identity_suggestions,
)
from ingest.common.logging_utils import configure_logging

logger = logging.getLogger(__name__)


def _merge_team_duplicates(engine, database_url: str = None) -> dict:
    """Copia de seguridad + fusión de los clubes duplicados EXACTOS (`--merge-team-duplicates`).

    Solo colisiones exactas (mismo nombre normalizado o mismo
    `teams.acb_club_id`, ver `find_team_identity_collisions`) — las
    sugerencias difusas (`find_team_identity_suggestions`) NUNCA se fusionan
    solas, ni con esta opción. La fusión es la de `tools/fix_team_identity.py`
    (`merge_exact_duplicates`), y va SIEMPRE detrás de una copia hecha con
    `tools/backup_db.py`: borra filas de `teams`, y si la copia falla (BD en
    memoria, disco lleno...) no se fusiona nada. La copia no rota las
    antiguas: borrar copias no es algo que deba pasar sin pedirlo.
    """
    from tools import backup_db, fix_team_identity

    with engine.connect() as conn:
        pending = find_team_identity_collisions(conn)
    if not pending:
        return {"ok": True, "summary": "sin clubes duplicados exactos, nada que fusionar"}

    try:
        backup = backup_db.create_backup(backup_db.database_path(database_url), label="pre-fusion-clubes")
    except Exception as exc:  # noqa: BLE001
        logger.exception("copia de seguridad previa a la fusión de clubes falló")
        return {"ok": False, "error": f"sin copia de seguridad no se fusiona nada: {exc}"}

    outcome = fix_team_identity.merge_exact_duplicates(engine)
    for survivor, absorbed in outcome["merged"]:
        logger.info("club fusionado: %s <- %s", survivor, absorbed)
    for group in outcome["blocked"]:
        logger.warning(
            "grupo de clubes NO fusionado (revísalo con python tools/fix_team_identity.py): %s", group
        )
    summary = (
        f"{outcome['absorbed_rows']} fila(s) de `teams` absorbidas en {outcome['merged_clubs']} club(es)"
        f" (copia: {backup.name})"
    )
    if outcome["blocked"]:
        summary += f"; {len(outcome['blocked'])} grupo(s) bloqueados para revisión a mano: {outcome['blocked']}"
    return {"ok": True, "summary": summary}


def run_all(
    season: int, database_url: str = None, skip: tuple = (), merge_team_duplicates: bool = False
) -> dict:
    """Ejecuta baskonia_web -> acb -> euroleague, en ese orden, y devuelve un resumen.

    Con `merge_team_duplicates=True` fusiona además, antes de la comprobación
    de identidad, los clubes duplicados exactos (ver `_merge_team_duplicates`).
    """
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

        # Ficha física (altura/peso/nacimiento/nacionalidad). Va DESPUÉS del
        # backfill de partidos a propósito: solo actualiza jugadores que ya
        # existen (ver `roster.py`), así que cuanto más tarde corra, más
        # altas del boxscore alcanza. Aparte del resto por el mismo motivo
        # que los calendarios: es la única fuente de esos campos, pero
        # ninguna estadística depende de ella.
        try:
            from ingest.euroleague.roster import run as run_euroleague_roster

            results["euroleague_roster"] = {"ok": True, "summary": run_euroleague_roster(engine, season)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Euroliga (ficha física) falló")
            results["euroleague_roster"] = {"ok": False, "error": str(exc)}
    else:
        results["euroleague"] = {"ok": None, "summary": "omitido"}
        results["euroleague_upcoming"] = {"ok": None, "summary": "omitido"}
        results["euroleague_roster"] = {"ok": None, "summary": "omitido"}

    if merge_team_duplicates:
        results["team_merge"] = _merge_team_duplicates(engine, database_url)

    # Comprobación de integridad de identidad (club y jugador, §5 de
    # doc/features/propuestas/04_fatiga_y_calendario.md), SIEMPRE al final —
    # incluso si algún módulo se omitió: un duplicado ("ningún club/jugador
    # con dos identidades") es más barato de detectar aquí, en cada ingesta,
    # que de descubrir a ojo meses después mirando un agregado que sale por
    # la mitad (o, en el caso de jugador, un timeline de rotaciones con la
    # misma persona en dos filas — ver `find_player_identity_collisions`).
    # Solo avisa, no arregla nada (ver el docstring de cada función).
    with engine.connect() as conn:
        team_collisions = find_team_identity_collisions(conn)
        team_suggestions = find_team_identity_suggestions(conn)
        player_collisions = find_player_identity_collisions(conn)
        merged_players = find_merged_players(conn)
    if team_collisions:
        logger.warning(
            "colisión de identidad de club detectada (fusionable con --merge-team-duplicates): %s",
            team_collisions,
        )
    # Sugerencias: parecidos de nombre SIN prueba de que sean el mismo club.
    # No cuentan como problema (no ponen `identity_check` en AVISO) y nunca se
    # fusionan solas; ver `find_team_identity_suggestions`.
    for suggestion in team_suggestions:
        logger.info(
            "posible club duplicado (sugerencia%s, revisar a mano): %s %s ~ %s %s, comparten %s",
            ", AMBIGUA" if suggestion["ambiguous"] else "",
            suggestion["team_ids"][0], suggestion["names"][0],
            suggestion["team_ids"][1], suggestion["names"][1],
            suggestion["shared_words"],
        )
    if player_collisions:
        logger.warning("colisión de identidad de jugador detectada: %s", player_collisions)
    # El fallo contrario y peor: dos personas en una sola fila. Se avisa aparte
    # porque la reparación no se parece en nada — una colisión se fusiona a
    # mano, una fila fusionada hay que reingerirla (ver
    # `tools/report_merged_players.py`).
    if merged_players:
        logger.warning(
            "%d filas de jugador parecen dos personas fusionadas (informe: "
            "python tools/report_merged_players.py): %s",
            len(merged_players), merged_players[:5],
        )
    collisions = team_collisions + player_collisions
    problems = collisions + merged_players
    summary = (
        f"{len(team_collisions)} colisiones de club, {len(player_collisions)} de jugador, "
        f"{len(merged_players)} filas fusionadas"
        if problems
        else "sin colisiones"
    )
    if team_suggestions:
        summary += f" ({len(team_suggestions)} sugerencia(s) de club por revisar)"
    results["identity_check"] = {
        "ok": not problems,
        "summary": summary,
        "team_collisions": team_collisions,
        "team_suggestions": team_suggestions,
    }

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Año de inicio de temporada (p.ej. 2025).")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--skip", action="append", default=[], choices=["acb", "euroleague", "baskonia_web"],
        help="Omite un módulo (se puede repetir).",
    )
    parser.add_argument(
        "--merge-team-duplicates", action="store_true",
        help=(
            "Al final, fusiona los clubes duplicados EXACTOS (mismo nombre normalizado o mismo "
            "clubId de ACB) con tools/fix_team_identity.py, tras una copia con tools/backup_db.py. "
            "Las sugerencias difusas nunca se fusionan."
        ),
    )
    args = parser.parse_args()

    configure_logging()
    results = run_all(
        args.season, args.database_url, tuple(args.skip),
        merge_team_duplicates=args.merge_team_duplicates,
    )

    print(f"\nResumen de ingesta (temporada {args.season}):")
    failed = []
    for name, result in results.items():
        if result["ok"] is None:
            print(f"  {name}: omitido")
        elif result["ok"]:
            print(f"  {name}: OK -> {result['summary']}")
        elif "error" in result:
            print(f"  {name}: FALLÓ -> {result['error']}")
            failed.append(name)
        else:
            # `identity_check`: no falla ejecutando nada, solo avisa (ver su
            # docstring) - no tiene "error" que imprimir, solo "summary".
            # Por eso NO cuenta como fallo: un duplicado de identidad es algo
            # que revisar, no una ingesta que no se ha hecho.
            print(f"  {name}: AVISO -> {result['summary']}")

    identity = results.get("identity_check", {})
    for pair in identity.get("team_collisions", []):
        print(f"  club duplicado: {pair[0]} = {pair[1]}  (--merge-team-duplicates lo fusiona)")
    for suggestion in identity.get("team_suggestions", []):
        (id_a, id_b), (name_a, name_b) = suggestion["team_ids"], suggestion["names"]
        print(
            f"  ¿mismo club? {id_a} ({name_a}) ~ {id_b} ({name_b})"
            f"{'  [AMBIGUA]' if suggestion['ambiguous'] else ''}"
            " - solo sugerencia: si lo es, añade el alias en _KNOWN_TEAM_ALIASES y fusiona"
        )

    # Código de salida distinto de 0 si algún módulo reventó. `run_all` está
    # pensado para no detenerse ante el fallo de una fuente (esa es su
    # gracia), pero salir siempre con 0 hacía que `baskonia-ingest.service`
    # (`Type=oneshot`, ver deploy/systemd/) se marcase como correcto aunque no
    # hubiese entrado un solo partido: la ingesta se quedaba muerta en
    # silencio y `systemctl --failed` seguía limpio. El caso no es
    # hipotético — el propio README avisa de que la `x-apikey` de acb.com
    # puede rotar.
    if failed:
        raise SystemExit(f"\nIngesta incompleta: falló {', '.join(failed)}.")


if __name__ == "__main__":
    main()
