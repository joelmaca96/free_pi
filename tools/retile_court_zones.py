"""Reclasifica in situ los tiros ya cargados contra la geometría nueva de `court_zones`.

Hasta el reteselado del 2026-08-27 (ver el comentario sobre `court_zones` en
`schema.sql`), las 6 zonas originales dejaban sin clasificar entre el 21%
(Euroliga) y el 48% (Supercopa) de los tiros de la BD — huecos grandes entre
rectángulos, verificados en vivo (ver `app/pages/proximo_rival.py`, aviso de
cobertura que leía la interfaz). El reteselado añade zonas nuevas y ensancha
las de media distancia para cubrir casi toda la cancha ofensiva, más una
zona dedicada a los mates (antes contados dentro de "Pintura").

`packages/baskonia_core/db/scouting/engine.py::_sync_court_zones` ya deja la
TABLA `court_zones` al día en cualquier `init_scouting_db()` (mismo mecanismo
que las columnas/tablas aditivas). Lo que NO hace, porque no es su sitio, es
tocar los tiros ya cargados: `shots.zone_id` se fija una vez, al ingerir
(`ingest/common/raw_game.py`), así que sigue apuntando a la clasificación
VIEJA hasta que algo la recalcula — y `game_zone_stats`, derivada de
`shots.zone_id`, arrastra el mismo dato viejo. Esta herramienta es ese "algo":
recorre todos los tiros ya cargados, poniéndoles el `zone_id` que les tocaría
con la geometría de HOY, y reconstruye `game_zone_stats` entera a partir del
resultado.

Mismo criterio que aplica `ingest/common/raw_game.py` en la carga: un tiro
`located=0` (mate sin coordenadas medidas) recibe `MATE_ZONE_ID` DIRECTAMENTE,
nunca por geometría — su centinela cae exactamente dentro de "Pintura", y sin
`ORDER BY` en `classify_zone` qué zona "gana" el solape no está garantizado.

También recoge el split de ala por triple (2026-08-27, ver el comentario
sobre `court_zones` en `schema.sql`): `classify_zone` ahora resuelve "Ala
izq."/"Ala der." un paso más, contra la elipse real de
`packages.baskonia_core.court_geometry`, hacia sus sub-zonas "(2)"/"(3)"
(filas 14-17) — así que en una BD con tiros cargados ANTES de ese cambio,
`shots.zone_id` sigue apuntando a la fila mezclada vieja hasta que se
ejecuta esta herramienta con `--apply`.

No hace falta red ni volver a descargar ningún partido: opera solo sobre lo
que ya hay en la BD, igual que `tools/fix_shot_coords.py` (incluso se puede
ejecutar después de él, o en cualquier orden — no comparten filas).

Uso:
    .venv/Scripts/python.exe tools/retile_court_zones.py            # diagnóstico
    .venv/Scripts/python.exe tools/retile_court_zones.py --apply    # escribe
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.zones import MATE_ZONE_ID, classify_zone  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db  # noqa: E402


def _reclassify(conn, apply: bool) -> None:
    rows = conn.execute(text("SELECT id, pos_x, pos_y, located, zone_id FROM shots")).all()
    if not rows:
        print("Sin tiros cargados — nada que hacer.")
        return

    before_covered = sum(1 for r in rows if r.zone_id is not None)
    changes = []
    after_covered = before_covered
    for row in rows:
        # `located` puede venir NULL en filas anteriores a esa columna (ver
        # `schema.sql`) — la interfaz las trata como localizadas, aquí igual.
        new_zone = MATE_ZONE_ID if row.located == 0 else classify_zone(conn, row.pos_x, row.pos_y)
        if new_zone != row.zone_id:
            changes.append((row.id, new_zone))
            if row.zone_id is None and new_zone is not None:
                after_covered += 1
            elif row.zone_id is not None and new_zone is None:
                after_covered -= 1
    print(
        f"{len(rows)} tiros · cobertura actual {100 * before_covered / len(rows):.0f}% "
        f"({before_covered} con zona) · {len(changes)} cambiarían de zona."
    )
    if not apply:
        print("Ejecuta de nuevo con --apply para escribir los cambios.")
        return
    if not changes:
        print("Nada que reclasificar — la BD ya está al día con la geometría actual.")
        return

    for shot_id, new_zone in changes:
        conn.execute(text("UPDATE shots SET zone_id = :z WHERE id = :id"), {"z": new_zone, "id": shot_id})

    # `game_zone_stats` se reconstruye ENTERA (no solo los partidos tocados,
    # a diferencia de `fix_shot_coords.py`: el reteselado afecta a TODAS las
    # fuentes, no a un prefijo de `game_id` concreto) — mismo criterio de
    # agregación que `ingest/common/loader.py::_replace_zone_stats_from_shots`.
    conn.execute(text("DELETE FROM game_zone_stats"))
    result = conn.execute(
        text(
            """
            INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume)
            SELECT s.game_id, p.team_id, s.zone_id,
                   ROUND(100.0 * SUM(s.made) / COUNT(*), 1), COUNT(*)
            FROM shots s
            JOIN players p ON p.id = s.player_id
            WHERE s.zone_id IS NOT NULL
            GROUP BY s.game_id, p.team_id, s.zone_id
            """
        )
    )
    print(
        f"Reclasificados {len(changes)} tiros · cobertura {100 * after_covered / len(rows):.0f}% "
        f"({after_covered} con zona) · regeneradas {result.rowcount} filas de game_zone_stats."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, solo diagnostica).")
    args = parser.parse_args()

    engine = create_scouting_engine(args.database_url)
    # Deja `court_zones` al día (filas nuevas/reajustadas del reteselado) ANTES
    # de reclasificar contra ella — también en diagnóstico: sin esto, una BD
    # que aún no ha pasado por `init_scouting_db()` (la app no lo llama sola en
    # cada arranque, ver `app/data/db.py`) se reclasifica contra la geometría
    # VIEJA y el diagnóstico infraestima cuánto cambiaría de verdad. Es un
    # no-op en una BD ya al día, y solo toca `court_zones` (nunca `shots`/
    # `game_zone_stats`, que siguen exigiendo `--apply` — ver `_reclassify`).
    init_scouting_db(engine)
    with engine.begin() as conn:
        _reclassify(conn, apply=args.apply)


if __name__ == "__main__":
    main()
