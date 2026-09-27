"""Fusiona clubes duplicados: el mismo club bajo dos o más `teams.id`.

Generaliza `tools/fix_barca_identity.py` (que fusionaba UN par fijado a mano)
a CUALQUIER grupo que detecte `ingest.common.identity.
find_team_identity_collisions`, igual que `tools/fix_player_identity.py` hizo
con las colisiones de jugador.

POR QUÉ HAY DUPLICADOS. ACB da un `id` de equipo NUEVO cada vez que cambia el
patrocinador del club ("BAXI Manresa" -> "Kids&Us Manresa" -> "Occident
Bàsquet Manresa"), así que `resolve_or_create_team` -que empareja por
`(source, external_id)` y, si no, por nombre normalizado- crea una fila de
`teams` por cada nombre. `_KNOWN_TEAM_ALIASES`
(`packages/baskonia_core/names.py`) es lo que enseña que todos esos nombres
son el mismo club: evita el duplicado SIGUIENTE, y es justo lo que hace que
estas colisiones se detecten hoy (sin el alias, "Casademont Zgz" y
"Casademont Zaragoza" normalizarían distinto y nadie diría nada). Lo que el
alias NO hace es fusionar las filas que se crearon ANTES de añadirlo: eso es
esto, una migración de datos de una vez.

Mientras están separadas, cualquier agregado por `team_id` de ese club
(récord, carga de minutos, descanso entre partidos, H2H...) ve solo una parte
de sus partidos, y el escudo/los jugadores pueden colgar de la fila
equivocada.

SUPERVIVIENTE de cada grupo, en este orden:

1. El equipo propio (`is_own_team = 1`) gana siempre, si está en el grupo.
   Fusionar el Baskonia DENTRO de otra fila rompería todo lo que cuelga de
   "mi equipo" (hoy no pasa: `bas` no tiene duplicados, pero la regla es
   barata y el fallo sería caro).
2. El que más PARTIDOS tiene (`games` como local o visitante) — el criterio
   que usó a mano `fix_barca_identity.py`, aquí calculado. Ojo: NO es el
   primero por orden alfabético ni el que no lleva sufijo `-2`/`-3`. Caso
   real: de `morabanc-and`, `morabanc-and-2` y `morabanc-and-3`, el que tiene
   los 34 partidos es `morabanc-and-2`.
3. A igualdad de partidos (dos filas con 0, p.ej. `cajasiete-fu` y
   `fundacion-ca`), el que más filas tiene apuntándole en total.

LOS JUGADORES SE MUEVEN, NO SE FUSIONAN, y esa es la diferencia importante
con `fix_barca_identity.py`. Allí se cruzaron las dos plantillas POR DORSAL
porque eran la misma temporada vista por dos fuentes (ACB y Euroliga), donde
el dorsal sí identifica a una persona. Aquí no: las filas duplicadas son
TEMPORADAS DISTINTAS del mismo club (cada patrocinador, un año), y el dorsal
se reutiliza de una temporada a la siguiente — cruzar por dorsal aquí
fundiría a dos personas distintas en una sola fila, que es exactamente la
corrupción silenciosa que `find_merged_players` existe para detectar y que ya
no se puede deshacer (ver su docstring y el de `resolve_or_create_player`).
Así que cada jugador conserva su fila y solo cambia de `team_id`. Si alguno
resultara ser de verdad la misma persona partida en dos filas, se detecta por
nombre (`find_player_identity_collisions`, que compara TODOS los jugadores
entre sí, no solo los del mismo equipo) y se fusiona aparte con
`tools/fix_player_identity.py`, que sabe elegir superviviente. El diagnóstico
de aquí avisa si el grupo trae alguno.

Tres cosas que un `DELETE` ingenuo de "la fila que tiene 0 partidos" rompería,
y que por eso esta herramienta mueve todo antes de borrar:

- **0 partidos no es 0 datos**: `stellantis-y` tiene 16 jugadores, `gc` 18,
  `bilbao` 26 — borrar la fila deja jugadores huérfanos (o revienta por FK,
  que el engine tiene `foreign_keys=ON`).
- **Calendario futuro**: `casademont-z-2` tiene 0 partidos jugados y 2
  `upcoming_matchups` de 2026-27 — borrarla rompe "Próximo rival".
- **Escudo**: la fila nueva (la del patrocinador de este año) suele ser la
  única con `logo_url`. El superviviente lo adopta si no tiene ninguno.

Y una que no se ve hasta que se intenta: `game_zone_stats` tiene la PK
`(game_id, team_id, zone_id)`, y hay partidos con tiros del club repartidos
entre sus dos `teams.id` (un jugador dado de alta bajo la fila vieja) — mover
esas filas a secas choca contra la PK. Son tiros del mismo equipo en el mismo
partido, así que se SUMAN en una sola fila (ver `_zone_stat_overlaps`). Lo que
sí para la fusión en seco es un choque que no se pueda resolver sumando: el
mismo partido cargado dos veces (`games`) o dos filas de
`game_advanced_stats`/`game_team_quarter_stats` para la misma clave.

Uso:
    .venv/Scripts/python.exe tools/fix_team_identity.py            # diagnóstico
    .venv/Scripts/python.exe tools/fix_team_identity.py --apply    # escribe

Haz antes una copia: `python tools/backup_db.py --label pre-fusion-clubes`.
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.identity import (  # noqa: E402
    find_player_identity_collisions,
    find_team_identity_collisions,
)
from packages.baskonia_core.db.scouting import create_scouting_engine  # noqa: E402
from packages.baskonia_core.names import normalize_name  # noqa: E402

#: Tablas con una columna que apunta a `teams.id`, y su columna. La lista
#: canónica: `fix_barca_identity.py` tiene su propia copia de cuando era el
#: único que fusionaba equipos. `players.team_id` va aparte (ver `_apply`:
#: se mueve primero, y su tratamiento es el que se explica en el docstring).
_TEAM_TABLES = [
    ("team_external_ids", "team_id"),
    ("games", "home_team_id"),
    ("games", "away_team_id"),
    ("game_advanced_stats", "team_id"),
    ("game_team_quarter_stats", "team_id"),
    ("lineups", "team_id"),
    ("game_zone_stats", "team_id"),
    ("key_events", "team_id"),
    ("upcoming_matchups", "opponent_team_id"),
    ("lineup_stints", "team_id"),
    ("play_events", "team_id"),
]


def _collision_groups(conn) -> List[List[str]]:
    """Grupos de `teams.id` que normalizan al mismo nombre, de 2 en adelante.

    `find_team_identity_collisions` devuelve PARES (uno por cada equipo
    adicional), pero un club puede estar partido en tres o cuatro filas
    (Lleida: `amara-lleida`, `cochesinternet`, `hiopos-lleid`,
    `ilerna-lleid`). Se agrupa por el mismo nombre normalizado que usa el
    detector, así que un grupo de N sale como un grupo, no como N-1 pares
    sueltos que habría que fusionar en cadena.
    """
    grouped: Dict[str, List[str]] = defaultdict(list)
    for row in conn.execute(text("SELECT id, name FROM teams ORDER BY id")).all():
        grouped[normalize_name(row.name)].append(row.id)
    return [ids for _key, ids in sorted(grouped.items()) if len(ids) > 1]


def _team_info(conn, team_id: str) -> dict:
    row = conn.execute(
        text("SELECT name, is_own_team, logo_url FROM teams WHERE id = :id"), {"id": team_id}
    ).one()
    games = conn.execute(
        text("SELECT COUNT(*) FROM games WHERE home_team_id = :id OR away_team_id = :id"),
        {"id": team_id},
    ).scalar_one()
    players = conn.execute(
        text("SELECT COUNT(*) FROM players WHERE team_id = :id"), {"id": team_id}
    ).scalar_one()
    upcoming = conn.execute(
        text("SELECT COUNT(*) FROM upcoming_matchups WHERE opponent_team_id = :id"), {"id": team_id}
    ).scalar_one()
    total_rows = players + sum(
        conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :id"), {"id": team_id}
        ).scalar_one()
        for table, col in _TEAM_TABLES
    )
    return {
        "id": team_id,
        "name": row.name,
        "is_own_team": bool(row.is_own_team),
        "logo_url": row.logo_url,
        "games": games,
        "players": players,
        "upcoming": upcoming,
        "total_rows": total_rows,
    }


def _pick_survivor(infos: List[dict]) -> dict:
    """Superviviente del grupo: equipo propio; si no, más partidos; si empatan, más filas."""
    own = [info for info in infos if info["is_own_team"]]
    if own:
        return own[0]
    return max(infos, key=lambda info: (info["games"], info["total_rows"]))


def _same_name_players(conn, team_ids: List[str]) -> List[Tuple[str, str]]:
    """Jugadores del grupo que normalizan al mismo nombre (posible misma persona).

    Solo AVISA: fusionar jugadores no es cosa de esta herramienta (ver el
    docstring del módulo), y `tools/fix_player_identity.py` ya sabe hacerlo
    eligiendo superviviente.
    """
    placeholders = ", ".join(f":t{i}" for i in range(len(team_ids)))
    rows = conn.execute(
        text(f"SELECT id, name FROM players WHERE team_id IN ({placeholders}) ORDER BY id"),
        {f"t{i}": team_id for i, team_id in enumerate(team_ids)},
    ).all()
    seen: Dict[str, str] = {}
    dupes: List[Tuple[str, str]] = []
    for row in rows:
        key = normalize_name(row.name)
        if key in seen:
            dupes.append((seen[key], row.id))
        else:
            seen[key] = row.id
    return dupes


#: Claves únicas que INCLUYEN la columna de equipo, aparte de `games` (que
#: tiene la suya propia, ver `_game_key_conflicts`) y de `game_zone_stats`
#: (que no se bloquea: se funde, ver `_zone_stat_overlaps`). Mover una fila al
#: superviviente choca si él ya tiene la suya para la misma clave. Hoy no pasa
#: en `data/baskonia.db`, pero un `IntegrityError` a mitad de fusión es una
#: forma pésima de enterarse.
_UNIQUE_KEYS_WITH_TEAM = [
    ("game_advanced_stats", ["game_id"]),
    ("game_team_quarter_stats", ["game_id", "quarter"]),
]


def _placeholders(team_ids: List[str]) -> Tuple[str, dict]:
    """`(":t0, :t1, ...", {"t0": ...})` para un `IN` con lista variable."""
    return (
        ", ".join(f":t{i}" for i in range(len(team_ids))),
        {f"t{i}": team_id for i, team_id in enumerate(team_ids)},
    )


def _key_conflicts(conn, table: str, keys: List[str], team_ids: List[str]) -> List[Tuple]:
    """Claves de `table` que quedarían duplicadas al mover todo al superviviente."""
    placeholders, params = _placeholders(team_ids)
    cols = ", ".join(keys)
    rows = conn.execute(
        text(
            f"SELECT {cols}, GROUP_CONCAT(team_id, '+') AS ids FROM {table}"
            f" WHERE team_id IN ({placeholders}) GROUP BY {cols} HAVING COUNT(*) > 1"
        ),
        params,
    ).all()
    return [tuple(row) for row in rows]


def _zone_stat_overlaps(conn, team_ids: List[str]) -> List[dict]:
    """Tiros del MISMO club y partido repartidos entre dos de sus `teams.id`.

    `game_zone_stats` es `shots` agregado por (equipo, zona) y su PK incluye el
    equipo (`ingest/common/loader.py::_replace_zone_stats_from_shots`). Si un
    jugador estaba dado de alta bajo la fila vieja del club, sus tiros de ese
    partido cuelgan del otro `team_id` y al fusionar chocarían contra la fila
    del superviviente. No es un choque de verdad: son tiros del mismo equipo en
    el mismo partido y la misma zona, así que la fusión correcta es SUMARLOS —
    `volume` se suma y `fg_pct` se recalcula sobre el total. Los aciertos se
    recuperan de `fg_pct * volume` (que es exactamente como los escribió el
    loader, redondeando a un decimal: con estos volúmenes el redondeo es
    reversible).

    Caso real en `data/baskonia.db`: 13 filas, en 3 clubes — p.ej. el partido
    `acb-104750` (Coviran Granada) tiene 3 mates de `coviran-gran` y 1 de
    `stellantis-y`, que son el mismo equipo.

    Returns:
        `[{game_id, zone_id, fg_pct, volume}]` con la fila YA fundida que debe
        quedar para el superviviente. Vacía si no hay solapes.
    """
    placeholders, params = _placeholders(team_ids)
    rows = conn.execute(
        text(
            "SELECT game_id, zone_id, fg_pct, volume FROM game_zone_stats"
            f" WHERE team_id IN ({placeholders})"
            " AND (game_id, zone_id) IN ("
            "   SELECT game_id, zone_id FROM game_zone_stats"
            f"  WHERE team_id IN ({placeholders})"
            "   GROUP BY game_id, zone_id HAVING COUNT(*) > 1)"
        ),
        params,
    ).all()

    totals: Dict[Tuple[str, int], List[int]] = {}
    for row in rows:
        made, volume = totals.setdefault((row.game_id, row.zone_id), [0, 0])
        # El loader escribe `fg_pct = round(100 * made / volume, 1)`.
        totals[(row.game_id, row.zone_id)] = [
            made + round(row.fg_pct * row.volume / 100.0),
            volume + row.volume,
        ]
    return [
        {
            "game_id": game_id,
            "zone_id": zone_id,
            "fg_pct": round(100.0 * made / volume, 1) if volume else 0.0,
            "volume": volume,
        }
        for (game_id, zone_id), (made, volume) in sorted(totals.items())
    ]


def _game_key_conflicts(conn, team_ids: List[str], survivor_id: str) -> List[Tuple[str, str]]:
    """Partidos que quedarían duplicados en la clave natural de `games` al fusionar.

    `games` tiene `UNIQUE (season_id, competition_id, game_date, home_team_id,
    away_team_id)`: si el MISMO partido estuviera cargado dos veces, una vez
    bajo cada `teams.id` del club, moverlos al superviviente chocaría contra
    esa clave. Hoy no pasa (en cada grupo solo una fila tiene partidos), pero
    parar con la lista de `game_id` delante es mucho mejor que un
    `IntegrityError` a mitad de la fusión.

    Returns:
        Lista de `(fecha, "g1+g2")` por cada choque. Vacía si no hay ninguno.
    """
    placeholders = ", ".join(f":t{i}" for i in range(len(team_ids)))
    params = {f"t{i}": team_id for i, team_id in enumerate(team_ids)}
    params["survivor"] = survivor_id
    rows = conn.execute(
        text(
            "SELECT game_date, GROUP_CONCAT(id, '+') AS ids, COUNT(*) AS n FROM ("
            "  SELECT id, game_date, season_id, competition_id,"
            f"   CASE WHEN home_team_id IN ({placeholders}) THEN :survivor ELSE home_team_id END AS h,"
            f"   CASE WHEN away_team_id IN ({placeholders}) THEN :survivor ELSE away_team_id END AS a"
            "  FROM games"
            ") GROUP BY season_id, competition_id, game_date, h, a HAVING n > 1 ORDER BY game_date"
        ),
        params,
    ).all()
    return [(str(row.game_date), row.ids) for row in rows]


def _plan(conn) -> List[dict]:
    """`[{survivor, losers, logo_from, conflicts}]`, uno por grupo de colisión."""
    plan = []
    for group in _collision_groups(conn):
        infos = [_team_info(conn, team_id) for team_id in group]
        survivor = _pick_survivor(infos)
        losers = [info for info in infos if info["id"] != survivor["id"]]
        # El escudo suele venir solo en la fila del patrocinador de este año
        # (la que tiene el calendario futuro), no en la que tiene el
        # histórico de partidos.
        logo_from = None
        if not survivor["logo_url"]:
            with_logo = [info for info in losers if info["logo_url"]]
            if with_logo:
                logo_from = max(with_logo, key=lambda info: (info["upcoming"], info["total_rows"]))
        conflicts = [
            (table, key)
            for table, keys in _UNIQUE_KEYS_WITH_TEAM
            for key in _key_conflicts(conn, table, keys, group)
        ]
        plan.append(
            {
                "survivor": survivor,
                "losers": losers,
                "logo_from": logo_from,
                "conflicts": _game_key_conflicts(conn, group, survivor["id"]),
                "key_conflicts": conflicts,
                "zone_merges": _zone_stat_overlaps(conn, group),
                "group": group,
            }
        )
    return plan


def _diagnose(conn, plan: List[dict]) -> None:
    print(f"{len(plan)} club(es) partidos en varias filas de `teams`:\n")
    for entry in plan:
        survivor = entry["survivor"]
        print(
            f"  {survivor['name']!r} -> se queda {survivor['id']!r} "
            f"({survivor['games']} pj, {survivor['players']} jug, {survivor['upcoming']} futuros)"
        )
        for loser in entry["losers"]:
            print(
                f"      <- {loser['id']:<15} {loser['name']!r} "
                f"({loser['games']} pj, {loser['players']} jug, {loser['upcoming']} futuros, "
                f"{loser['total_rows']} filas en total)"
            )
        if entry["logo_from"]:
            print(f"      escudo: lo adopta de {entry['logo_from']['id']!r} (el superviviente no tenía)")
        if entry["zone_merges"]:
            print(
                f"      tiros por zona repartidos entre dos ids del club: {len(entry['zone_merges'])} "
                "fila(s) de `game_zone_stats` se suman en una"
            )
        for date, ids in entry["conflicts"]:
            print(
                f"      ⛔ el mismo partido está cargado dos veces ({date}, {ids}): fusionar "
                "chocaría con la clave natural de `games`. Revísalo a mano antes."
            )
        for table, key in entry["key_conflicts"]:
            print(f"      ⛔ {table}: dos filas del club para la misma clave {key}. Revísalo a mano antes.")
        dupes = _same_name_players(conn, [survivor["id"]] + [loser["id"] for loser in entry["losers"]])
        if dupes:
            print(
                "      ⚠ jugadores con el mismo nombre en el grupo (NO se fusionan aquí, "
                f"míralos con tools/fix_player_identity.py): {dupes}"
            )
        print()


def _apply(conn, plan: List[dict]) -> None:
    for entry in plan:
        survivor_id = entry["survivor"]["id"]
        if entry["logo_from"]:
            conn.execute(
                text("UPDATE teams SET logo_url = :logo WHERE id = :id"),
                {"logo": entry["logo_from"]["logo_url"], "id": survivor_id},
            )
        # Antes de mover nada: las zonas que chocarían se sustituyen por la
        # fila ya sumada del superviviente (ver `_zone_stat_overlaps`).
        placeholders, params = _placeholders(entry["group"])
        for merged in entry["zone_merges"]:
            conn.execute(
                text(
                    f"DELETE FROM game_zone_stats WHERE team_id IN ({placeholders})"
                    " AND game_id = :game_id AND zone_id = :zone_id"
                ),
                {**params, "game_id": merged["game_id"], "zone_id": merged["zone_id"]},
            )
            conn.execute(
                text(
                    "INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume)"
                    " VALUES (:game_id, :team_id, :zone_id, :fg_pct, :volume)"
                ),
                {**merged, "team_id": survivor_id},
            )

        for loser in entry["losers"]:
            # Primero los hijos (el engine corre con `foreign_keys=ON`), la
            # fila de `teams` al final.
            conn.execute(
                text("UPDATE players SET team_id = :new WHERE team_id = :old"),
                {"new": survivor_id, "old": loser["id"]},
            )
            for table, col in _TEAM_TABLES:
                conn.execute(
                    text(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"),
                    {"new": survivor_id, "old": loser["id"]},
                )
            conn.execute(text("DELETE FROM teams WHERE id = :id"), {"id": loser["id"]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, solo diagnostica).")
    parser.add_argument(
        "--skip-id",
        action="append",
        default=[],
        metavar="TEAM_ID",
        help=(
            "Excluye el GRUPO que contenga este `teams.id` (repetible). Para cuando dos filas "
            "normalizan igual pero no son el mismo club — un alias de `_KNOWN_TEAM_ALIASES` "
            "demasiado laxo, p.ej.: eso se arregla en `names.py`, no fusionando."
        ),
    )
    args = parser.parse_args()
    skip_ids = set(args.skip_id)

    engine = create_scouting_engine(args.database_url)
    with engine.connect() as conn:
        plan = _plan(conn)
        if skip_ids:
            kept = []
            for entry in plan:
                ids = {entry["survivor"]["id"]} | {loser["id"] for loser in entry["losers"]}
                if ids & skip_ids:
                    print(f"Excluido por --skip-id: {sorted(ids)}")
                else:
                    kept.append(entry)
            plan = kept
            print()
        if not plan:
            print("Sin colisiones de identidad de club — nada que fusionar.")
            return
        _diagnose(conn, plan)

    if not args.apply:
        print("Ejecuta de nuevo con --apply para escribir los cambios.")
        return

    blocked = [entry for entry in plan if entry["conflicts"] or entry["key_conflicts"]]
    if blocked:
        print(
            "Abortado: "
            + ", ".join(entry["survivor"]["id"] for entry in blocked)
            + " tiene(n) filas que chocarían con una clave única al fusionar (⛔ arriba). "
            "Arréglalas a mano, o excluye el grupo con --skip-id."
        )
        sys.exit(1)

    with engine.begin() as conn:
        _apply(conn, plan)
        remaining_teams = find_team_identity_collisions(conn)
        remaining_players = find_player_identity_collisions(conn)

    merged = sum(len(entry["losers"]) for entry in plan)
    print(f"Fusión completa: {merged} fila(s) de `teams` absorbidas en {len(plan)} club(es).")
    if remaining_teams:
        print(f"⚠ Siguen quedando colisiones de club: {remaining_teams}")
    else:
        print("Sin colisiones de identidad de club restantes.")
    if remaining_players:
        print(f"Nota: quedan {len(remaining_players)} colisión(es) de JUGADOR: {remaining_players}")


if __name__ == "__main__":
    main()
