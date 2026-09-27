"""Separa las filas de `players` que son dos personas metidas en una.

Diagnóstico y contexto en `tools/report_merged_players.py`; la causa ya está
tapada en `ingest/common/identity.py` (el emparejamiento por dorsal exige
apellido en común). Esto arregla lo que quedó hecho de antes.

POR QUÉ HAY QUE REINGERIR Y NO BASTA UN `UPDATE`. Al fusionarse las dos
personas, `player_game_stats` fue escribiendo una encima de otra por su clave
natural `(game_id, player_id)`: en la base de datos ya no queda de quién era
cada línea. Lo sabe la fuente, y solo la fuente. Así que el arreglo es
volver a pedir los partidos afectados y dejar que la ingesta reconstruya la
identidad correcta, ahora que ya no fusiona.

LOS TRES PASOS, y por qué en ese orden:

1. **Soltar los alias sobrantes.** Mientras `player_external_ids` apunte a la
   fila fusionada, el paso 1 de `resolve_or_create_player` la resuelve otra
   vez a ella y la reingesta no cambia nada.

2. **Renombrar la fila a su primer ocupante.** El nombre que tiene ahora es
   el del ÚLTIMO que la pisó; si se deja así, al reingerir ese último vuelve
   a caer en ella por el paso 3 (mismo equipo, mismo nombre normalizado) y la
   fusión se rehace sola. El nombre original se recupera del `players.id`,
   que es un slug de cuando se creó la fila y nunca se reescribe.

3. **Borrar sus `player_game_stats` y reingerir.** Esa es la ÚNICA tabla por
   jugador que la ingesta actualiza en vez de reescribir por partido (ver
   `ingest/common/loader.py`: `lineups`, `shots`, `play_events`,
   `player_advanced_stats`... se borran y reinsertan por `game_id`, así que
   se curan solas al recargar el partido). Sin borrarla, la línea vieja
   —atribuida a quien no era— se quedaría ahí para siempre.

Uso:
    .venv/Scripts/python.exe tools/backup_db.py --label pre-separar-jugadores
    .venv/Scripts/python.exe tools/repair_merged_players.py --season 2025 --dry-run
    .venv/Scripts/python.exe tools/repair_merged_players.py --season 2025
"""
import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from ingest.common.identity import find_merged_players  # noqa: E402
from ingest.common.logging_utils import configure_logging  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine  # noqa: E402

logger = logging.getLogger(__name__)


def _original_name(player_id: str) -> str:
    """Nombre del primer ocupante, reconstruido desde el slug de `players.id`.

    Sale en minúsculas y puede venir cortado a 12 caracteres (`alberto-abal`
    de "Alberto Abalde"): no es el nombre bonito, es el que hace falta para
    que la reingesta NO vuelva a emparejar a la otra persona con esta fila.
    La ingesta lo sobreescribirá con el nombre bueno de la fuente en cuanto
    recargue un partido suyo.
    """
    return re.sub(r"-\d+$", "", player_id).replace("-", " ").title()


def affected_games(conn, player_ids):
    """`(game_id, fuente, id externo, temporada)` de los partidos a recargar."""
    if not player_ids:
        return []
    marks = ", ".join(f":p{i}" for i in range(len(player_ids)))
    params = {f"p{i}": pid for i, pid in enumerate(player_ids)}
    return conn.execute(
        text(f"""
            SELECT DISTINCT g.id, g.season_id
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            WHERE pgs.player_id IN ({marks})
            ORDER BY g.game_date
        """),
        params,
    ).all()


def _split_game_id(game_id: str):
    """`'acb-105371'` -> `('acb', '105371')`. Los ids llevan la fuente delante."""
    source, _, external = game_id.partition("-")
    return source, external


def detach(conn, merged) -> dict:
    """Pasos 1-3 (sin reingerir): suelta alias, renombra y borra el boxscore por jugador."""
    player_ids = [pid for pid, _ in merged]
    games = affected_games(conn, player_ids)

    marks = ", ".join(f":p{i}" for i in range(len(player_ids)))
    params = {f"p{i}": pid for i, pid in enumerate(player_ids)}

    aliases = conn.execute(
        text(f"SELECT COUNT(*) FROM player_external_ids WHERE player_id IN ({marks})"), params
    ).scalar()
    conn.execute(text(f"DELETE FROM player_external_ids WHERE player_id IN ({marks})"), params)

    for player_id, _ in merged:
        conn.execute(
            text("UPDATE players SET name = :name WHERE id = :id"),
            {"name": _original_name(player_id), "id": player_id},
        )

    stats = conn.execute(
        text(f"SELECT COUNT(*) FROM player_game_stats WHERE player_id IN ({marks})"), params
    ).scalar()
    conn.execute(text(f"DELETE FROM player_game_stats WHERE player_id IN ({marks})"), params)

    return {"aliases": aliases, "stats": stats, "games": games}


#: Tablas que referencian `players.id`. Se consultan para no borrar una fila
#: que todavía cuelga de algo (la FK lo impediría, pero fallar por
#: `IntegrityError` a mitad de una reparación es peor que comprobarlo antes).
_PLAYER_REFERENCES = [
    "player_external_ids",
    "player_game_stats",
    "player_game_quarter_stats",
    "player_advanced_stats",
    "lineup_players",
    "lineup_stint_players",
    "play_events",
    "shots",
]


def drop_orphans(conn, player_ids) -> list:
    """Borra las filas que quedaron vacías tras la reingesta; devuelve cuáles.

    Una fila fusionada de la que ya se han ido TODOS sus ocupantes (cada uno
    a la suya, al recargar los partidos) se queda sin alias, sin boxscore y
    sin nada que la referencie, con el nombre truncado que se le puso en el
    paso 2 ("Alberto Abal"). No hace daño, pero es un jugador fantasma en
    `players` que aparecería en cualquier listado por equipo. Se borra solo
    si de verdad no queda nada colgando de ella.
    """
    dropped = []
    for player_id in player_ids:
        referenced = any(
            conn.execute(
                text(f"SELECT 1 FROM {table} WHERE player_id = :id LIMIT 1"), {"id": player_id}
            ).first()
            for table in _PLAYER_REFERENCES
        )
        if referenced:
            continue
        conn.execute(text("DELETE FROM players WHERE id = :id"), {"id": player_id})
        dropped.append(player_id)
    return dropped


def reingest(engine, games, season: int) -> dict:
    """Recarga los partidos afectados, agrupados por fuente y reutilizando cliente."""
    from ingest.acb.client import AcbClient
    from ingest.acb.pipeline import run_single_game as acb_single
    from ingest.euroleague.client import EuroleagueClient
    from ingest.euroleague.pipeline import run_single_game as euroleague_single

    by_source = {"acb": [], "euroleague": []}
    for game_id, _season_id in games:
        source, external = _split_game_id(game_id)
        if source in by_source:
            by_source[source].append(external)

    done = {"acb": 0, "euroleague": 0}
    failed = []

    if by_source["acb"]:
        client = AcbClient()
        for index, external in enumerate(by_source["acb"], 1):
            try:
                result = acb_single(engine, season, external, client=client)
                done["acb"] += len(result.get("loaded", []))
                failed.extend(result.get("failed", []))
            except Exception as exc:  # noqa: BLE001 - un partido roto no para el resto
                logger.warning("acb-%s falló: %s", external, exc)
                failed.append(f"acb-{external}")
            if index % 25 == 0:
                logger.info("acb: %d/%d partidos recargados", index, len(by_source["acb"]))

    if by_source["euroleague"]:
        client = EuroleagueClient()
        for index, external in enumerate(by_source["euroleague"], 1):
            try:
                result = euroleague_single(engine, season, int(external), client=client)
                done["euroleague"] += len(result.get("loaded", []))
                failed.extend(result.get("failed", []))
            except Exception as exc:  # noqa: BLE001
                logger.warning("euroleague-%s falló: %s", external, exc)
                failed.append(f"euroleague-{external}")
            if index % 25 == 0:
                logger.info("euroleague: %d/%d partidos recargados", index, len(by_source["euroleague"]))

    return {"loaded": done, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True, help="Temporada a recargar (2025 = 2025-2026).")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Enseña qué haría, sin tocar nada.")
    parser.add_argument("--skip-reingest", action="store_true", help="Solo suelta y limpia; no recarga partidos.")
    args = parser.parse_args()

    configure_logging()
    engine = create_scouting_engine(args.database_url)

    with engine.connect() as conn:
        merged = find_merged_players(conn)
        games = affected_games(conn, [pid for pid, _ in merged])

    if not merged:
        print("No hay filas fusionadas. Nada que hacer.")
        return

    print(f"Filas fusionadas: {len(merged)}")
    print(f"Partidos a recargar: {len(games)}")
    if args.dry_run:
        print("\n[dry-run] se soltarían los alias, se renombrarían estas filas y se")
        print("[dry-run] borraría su boxscore por jugador antes de recargar:")
        for player_id, name in merged[:10]:
            print(f"  {player_id:<20} '{name}' -> '{_original_name(player_id)}'")
        if len(merged) > 10:
            print(f"  ... y {len(merged) - 10} más")
        return

    with engine.begin() as conn:
        detached = detach(conn, merged)
    print(
        f"Soltados {detached['aliases']} alias, renombradas {len(merged)} filas, "
        f"borradas {detached['stats']} líneas de boxscore."
    )

    if args.skip_reingest:
        print("--skip-reingest: no se recarga nada. La base de datos queda INCOMPLETA hasta que se recargue.")
        return

    print(f"Recargando {len(games)} partidos...")
    result = reingest(engine, games, args.season)
    print(f"Recargados: {result['loaded']}")
    if result["failed"]:
        print(f"Fallaron {len(result['failed'])}: {result['failed'][:10]}")

    with engine.begin() as conn:
        dropped = drop_orphans(conn, [pid for pid, _ in merged])
    print(f"Filas fantasma borradas (sin alias, sin boxscore, sin referencias): {len(dropped)}")

    with engine.connect() as conn:
        remaining = find_merged_players(conn)
    print(f"\nFilas fusionadas restantes: {len(remaining)}")
    if remaining:
        print("Vuelve a pasar el informe para ver cuáles:")
        print("  python tools/report_merged_players.py")


if __name__ == "__main__":
    main()
