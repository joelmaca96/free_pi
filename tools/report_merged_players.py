"""Informe de filas de `players` que son dos personas metidas en una (solo lectura).

Detección en `ingest/common/identity.py::find_merged_players` — ahí está el
porqué y cómo. Este script pone números encima: cuántas filas, cuántos
partidos cuelgan de ellas y, sobre todo, **qué habría que rehacer** para
separarlas.

POR QUÉ NO ARREGLA NADA. Separar una fila fusionada no es un `UPDATE`: el
dato de a quién pertenecía cada línea de boxscore ya no está en la base de
datos, se perdió al escribirse una encima de otra (`player_game_stats` tiene
clave natural `(game_id, player_id)` y hace `ON CONFLICT DO UPDATE`). Lo
único que lo sabe es la fuente. Y reingerir tal cual TAMPOCO basta: los
`player_external_ids` ya apuntan a la fila equivocada, así que el paso 1 de
`resolve_or_create_player` los resuelve otra vez a ella. Hay que desvincular
esos alias primero (`--unlink-sql` escupe el SQL, para revisarlo antes de
ejecutar nada) y volver a ingerir los partidos que este informe lista.

Uso:
    .venv/Scripts/python.exe tools/report_merged_players.py
    .venv/Scripts/python.exe tools/report_merged_players.py --unlink-sql > separar.sql
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from ingest.common.identity import find_merged_players  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine  # noqa: E402


def _evidence(conn, player_id: str) -> dict:
    """Alias por fuente, partidos y equipo de una fila sospechosa."""
    aliases = conn.execute(
        text("SELECT source, external_id FROM player_external_ids WHERE player_id = :id ORDER BY source"),
        {"id": player_id},
    ).all()
    by_source = defaultdict(list)
    for source, external_id in aliases:
        by_source[source].append(external_id)
    row = conn.execute(
        text("SELECT team_id, number FROM players WHERE id = :id"), {"id": player_id}
    ).first()
    games = conn.execute(
        text("SELECT COUNT(*) FROM player_game_stats WHERE player_id = :id"), {"id": player_id}
    ).scalar()
    return {"aliases": dict(by_source), "team": row.team_id, "number": row.number, "games": games}


def _affected_games(conn, player_ids):
    """Partidos que habría que volver a ingerir, agrupados por (temporada, competición)."""
    if not player_ids:
        return []
    marks = ", ".join(f":p{i}" for i in range(len(player_ids)))
    params = {f"p{i}": pid for i, pid in enumerate(player_ids)}
    return conn.execute(
        text(f"""
            SELECT s.label AS season, c.name AS competition,
                   COUNT(DISTINCT g.id) AS games, MIN(g.game_date) AS first, MAX(g.game_date) AS last
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            JOIN seasons s ON s.id = g.season_id
            JOIN competitions c ON c.id = g.competition_id
            WHERE pgs.player_id IN ({marks})
            GROUP BY s.label, c.name
            ORDER BY s.label DESC, games DESC
        """),
        params,
    ).all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument(
        "--unlink-sql", action="store_true",
        help="Escribe el SQL que desvincula los alias sobrantes, para revisarlo antes de ejecutarlo.",
    )
    args = parser.parse_args()

    engine = create_scouting_engine(args.database_url)
    with engine.connect() as conn:
        merged = find_merged_players(conn)
        evidence = {pid: _evidence(conn, pid) for pid, _ in merged}
        affected = _affected_games(conn, [pid for pid, _ in merged])
        total_stats = conn.execute(text("SELECT COUNT(*) FROM player_game_stats")).scalar()

    if args.unlink_sql:
        _print_unlink_sql(merged, evidence)
        return

    if not merged:
        print("Sin filas fusionadas. Nada que rehacer.")
        return

    print(f"FILAS FUSIONADAS: {len(merged)}\n")
    print(f"  {'equipo':<16} {'#':<4} {'se llama ahora':<26} {'id (primer ocupante)':<22} alias  partidos")
    dirty = 0
    for player_id, name in sorted(merged, key=lambda r: -evidence[r[0]]["games"]):
        info = evidence[player_id]
        dirty += info["games"]
        alias_text = " ".join(f"{src}×{len(ids)}" for src, ids in info["aliases"].items()) or "—"
        print(
            f"  {info['team']:<16} {info['number']:<4} {name:<26} {player_id:<22}"
            f" {alias_text:<12} {info['games']:>4}"
        )

    share = 100 * dirty / total_stats if total_stats else 0
    print(f"\n  Registros de player_game_stats afectados: {dirty:,} de {total_stats:,} ({share:.1f}%)")

    print("\nQUÉ HABRÍA QUE VOLVER A INGERIR")
    for row in affected:
        print(f"  {row.season}  {row.competition:<12} {row.games:>4} partidos  ({row.first} … {row.last})")
    print(f"  {'':<14} {'TOTAL':<12} {sum(r.games for r in affected):>4} partidos distintos")

    print(
        "\nCÓMO, en este orden (ninguno de estos pasos lo hace este script):\n"
        "  1. python tools/backup_db.py --label pre-separar-jugadores\n"
        "  2. python tools/report_merged_players.py --unlink-sql > separar.sql   (y revisarlo)\n"
        "  3. ejecutar separar.sql  — deja un alias por fila y suelta los demás\n"
        "  4. reingerir las temporadas de arriba: python -m ingest.run_all --season <año>\n"
        "     Al no encontrar el alias, cada jugador se resuelve de cero y el emparejamiento\n"
        "     por dorsal ya exige apellido en común, así que se crean las filas que faltan.\n"
        "  5. volver a pasar este informe: debería quedar en cero."
    )


def _print_unlink_sql(merged, evidence) -> None:
    """SQL para soltar los alias sobrantes de cada fila fusionada.

    Se conserva el PRIMER alias de cada fuente (arbitrario pero estable) y se
    sueltan los demás: son los que pertenecen a las otras personas metidas en
    la misma fila. Sin soltarlos, reingerir vuelve a resolverlos a esta fila.
    """
    print("-- Generado por tools/report_merged_players.py — REVISAR antes de ejecutar.")
    print("-- Suelta los alias que no son del primer ocupante de cada fila fusionada.")
    print("-- Después hay que reingerir (ver el informe sin --unlink-sql).")
    print("BEGIN;")
    total = 0
    for player_id, name in sorted(merged):
        for source, external_ids in evidence[player_id]["aliases"].items():
            for external_id in external_ids[1:]:  # el primero se queda
                total += 1
                print(
                    f"DELETE FROM player_external_ids WHERE player_id = '{player_id}'"
                    f" AND source = '{source}' AND external_id = '{external_id}';"
                    f"  -- {name}"
                )
    print("COMMIT;")
    print(f"-- {total} alias sueltos, de {len(merged)} filas.")


if __name__ == "__main__":
    main()
