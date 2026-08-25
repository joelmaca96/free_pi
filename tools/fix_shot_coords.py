"""Recalcula in situ las coordenadas de los tiros ya cargados en la BD.

Hasta 2026-08-24 los dos adaptadores reescalaban mal las coordenadas de tiro,
cada uno a su manera, y el mapa de tiros salía deformado:

- Euroliga: `COORD_Y` es la DISTANCIA AL ARO, pero se normalizaba de extremo a
  extremo sin invertirla. `court_zones` usa la convención contraria ("y alto =
  cerca del aro"), así que el mapa salía DEL REVÉS: las bandejas caían en la
  zona "Triple exterior" y los triples junto al aro.
- ACB: misma idea, orientación correcta pero escala de profundidad estirada un
  76% (se normalizaba `posX` contra un máximo fijo de 7.500 mm). El aro
  quedaba en y=500 en vez de 455, así que el 35% de los tiros se pintaba por
  encima del aro -detrás del tablero- y el 77% no caía en ninguna zona.

Arreglados los adaptadores (la conversión vive ahora en
`ingest.common.zones.to_court_coords`), los tiros YA cargados siguen mal en la
BD. Esta herramienta los corrige sin volver a descargar los partidos: las
transformaciones antiguas son biyecciones lineales, así que se pueden
invertir para recuperar las coordenadas originales de cada fuente y volver a
aplicar la correcta. Recalcula también `shots.zone_id` y `game_zone_stats`
(derivada de las zonas), que dependían de las coordenadas.

La alternativa -reingestar con los pipelines de `ingest/`- también funciona y
es igual de válida (la carga es idempotente); esto es solo el atajo exacto y
sin red.

Rellena además `shots.located` (columna añadida el mismo día, ver
`schema.sql`), que en las filas ya cargadas viene a NULL: los mates de ACB
-los únicos tiros que la fuente no localiza- son reconocibles a posteriori
porque su centinela `posX=posY=0` los deja exactamente sobre el aro.

Uso:
    .venv/Scripts/python.exe tools/fix_shot_coords.py            # diagnóstico
    .venv/Scripts/python.exe tools/fix_shot_coords.py --apply    # escribe
"""
import argparse
import sys
from pathlib import Path
from typing import Callable, List, Tuple

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.zones import classify_zone, to_court_coords  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine, init_scouting_db  # noqa: E402


def _euroleague_original(pos_x: float, pos_y: float) -> Tuple[float, float]:
    """`(pos_x, pos_y)` con la transformación antigua de Euroliga -> `COORD_X`/`COORD_Y` en cm.

    Antigua: `x = (cx + 750) / 1500 * 500`, `y = (cy + 100) / 900 * 500`.
    """
    return pos_x / 500 * 1500 - 750, pos_y / 500 * 900 - 100


def _euroleague_is_old(rows: List[tuple]) -> bool:
    """La conversión nueva no puede sacar un tiro de `[25, 475]` en x salvo con
    un `COORD_X` de más de 8,4 m (media cancha son 7,5 m). Un lateral fuera de
    ese margen en una parte apreciable de los tiros solo lo produce la antigua,
    que estiraba +-750 cm a los 0-500 enteros."""
    return sum(1 for _, x, _ in rows if x < 25 or x > 475) / len(rows) > 0.01


def _acb_original(pos_x: float, pos_y: float) -> Tuple[float, float]:
    """`(pos_x, pos_y)` con la transformación antigua de ACB -> `posY`/`posX` en cm.

    Antigua: `x = (posY + 7500) / 15000 * 500`, `y = 500 * (1 - posX / 7500)`.
    """
    lateral_mm = pos_x / 500 * 15000 - 7500
    depth_mm = 7500 * (1 - pos_y / 500)
    return lateral_mm / 10, depth_mm / 10


def _acb_is_old(rows: List[tuple]) -> bool:
    """`posX` es una distancia al aro, nunca negativa, así que con la conversión
    nueva NINGÚN tiro puede quedar por encima del aro (y = 455). Con la antigua,
    el aro caía en y=500 y un tercio de los tiros quedaba por encima."""
    return sum(1 for _, _, y in rows if y > 456) / len(rows) > 0.01


# (fuente, prefijo de `games.id`, inversa de la transformación antigua, huella de "está antigua")
SOURCES: List[Tuple[str, str, Callable, Callable]] = [
    ("Euroliga", "euroleague-", _euroleague_original, _euroleague_is_old),
    ("ACB", "acb-", _acb_original, _acb_is_old),
]


def _fix_source(conn, label: str, prefix: str, original: Callable, is_old: Callable, apply: bool) -> None:
    rows = [
        (r[0], float(r[1]), float(r[2]))
        for r in conn.execute(
            text("SELECT id, pos_x, pos_y FROM shots WHERE game_id LIKE :prefix"), {"prefix": f"{prefix}%"}
        ).all()
    ]
    if not rows:
        print(f"[{label}] sin tiros cargados — nada que hacer.")
        return
    if not is_old(rows):
        print(
            f"[{label}] {len(rows)} tiros, sin la huella de las coordenadas antiguas — ya están "
            "corregidos (o recargados con el adaptador nuevo). No se toca nada."
        )
        return

    print(f"[{label}] {len(rows)} tiros con coordenadas antiguas.")
    if not apply:
        shot_id, pos_x, pos_y = rows[0]
        new_x, new_y = to_court_coords(*original(pos_x, pos_y))
        print(f"          ejemplo: ({pos_x:.0f}, {pos_y:.0f}) -> ({new_x:.0f}, {new_y:.0f})")
        return

    for shot_id, pos_x, pos_y in rows:
        new_x, new_y = to_court_coords(*original(pos_x, pos_y))
        conn.execute(
            text("UPDATE shots SET pos_x = :x, pos_y = :y, zone_id = :z WHERE id = :id"),
            {"x": new_x, "y": new_y, "z": classify_zone(conn, new_x, new_y), "id": shot_id},
        )

    # `game_zone_stats` es una agregación de `shots` por (partido, equipo,
    # zona): se rehace entera para los partidos tocados, con el mismo criterio
    # que `ingest/common/loader.py::_replace_zone_stats_from_shots` (el equipo
    # del tiro sale de `players.team_id`, igual que en las consultas de la
    # interfaz).
    conn.execute(text("DELETE FROM game_zone_stats WHERE game_id LIKE :prefix"), {"prefix": f"{prefix}%"})
    result = conn.execute(
        text(
            """
            INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume)
            SELECT s.game_id, p.team_id, s.zone_id,
                   ROUND(100.0 * SUM(s.made) / COUNT(*), 1), COUNT(*)
            FROM shots s
            JOIN players p ON p.id = s.player_id
            WHERE s.game_id LIKE :prefix AND s.zone_id IS NOT NULL
            GROUP BY s.game_id, p.team_id, s.zone_id
            """
        ),
        {"prefix": f"{prefix}%"},
    )
    print(f"          corregidos {len(rows)} tiros y regeneradas {result.rowcount} filas de game_zone_stats.")


def _backfill_located(conn, apply: bool) -> None:
    """`shots.located` para las filas anteriores a esa columna (a NULL).

    Los tiros sin coordenadas medidas son hoy solo los mates de ACB, y tras la
    corrección de coordenadas caen todos en el punto exacto del aro (250, 455)
    — que es donde los pone `to_court_coords` a partir de su centinela
    `posX=posY=0`. Ningún tiro medido puede caer ahí por casualidad: haría
    falta un 0,0 exacto en las dos coordenadas de la fuente, que es
    precisamente el centinela. El resto de filas pasan a `located = 1`.
    """
    if "located" not in {col["name"] for col in inspect(conn).get_columns("shots")}:
        # La columna la añade `init_scouting_db` (ver `_ADDITIVE_COLUMN_MIGRATIONS`),
        # que solo se llama al escribir — en diagnóstico no se toca la BD.
        print("[located] la columna aún no existe en esta BD; se añade y se rellena con --apply.")
        return

    pending = conn.execute(text("SELECT COUNT(*) FROM shots WHERE located IS NULL")).scalar_one()
    if not pending:
        print("[located] ninguna fila pendiente de marcar.")
        return

    unlocated_sql = (
        "ROUND(pos_x, 3) = 250 AND ROUND(pos_y, 3) = 455 AND game_id LIKE 'acb-%'"
    )
    if not apply:
        n = conn.execute(text(f"SELECT COUNT(*) FROM shots WHERE located IS NULL AND {unlocated_sql}")).scalar_one()
        print(f"[located] {pending} tiros sin marcar; {n} quedarían como 'sin ubicación exacta' (mates ACB).")
        return

    conn.execute(text(f"UPDATE shots SET located = 0 WHERE located IS NULL AND {unlocated_sql}"))
    result = conn.execute(text("UPDATE shots SET located = 1 WHERE located IS NULL"))
    print(f"[located] marcados {pending} tiros ({pending - result.rowcount} sin ubicación exacta).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, solo diagnostica).")
    args = parser.parse_args()

    engine = create_scouting_engine(args.database_url)
    if args.apply:
        # Añade `shots.located` si esta BD es anterior a esa columna; en una BD
        # ya al día es un no-op (ver `engine.py::_apply_additive_migrations`).
        init_scouting_db(engine)
    with engine.begin() as conn:
        for source in SOURCES:
            _fix_source(conn, *source, apply=args.apply)
        _backfill_located(conn, apply=args.apply)
    if not args.apply:
        print("\nEjecuta de nuevo con --apply para escribir los cambios.")


if __name__ == "__main__":
    main()
