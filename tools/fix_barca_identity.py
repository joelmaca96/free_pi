"""Fusiona el Barça duplicado (`barca` + `fcb`) en un único `team_id`.

Requisito previo de la propuesta 04 (`doc/features/propuestas/
04_fatiga_y_calendario.md` §5): `barca` ("Barça", creado desde ACB — 42
partidos de Liga Endesa + 2 de Copa del Rey) y `fcb` ("FC Barcelona", creado
desde Euroliga — 40 partidos) son el mismo club, pero se crearon como dos
`teams.id` distintos ANTES de que `_KNOWN_TEAM_ALIASES`
(`packages/baskonia_core/names.py`) aprendiera que "Barça" y "FC Barcelona"
normalizan al mismo club. Ese alias ya evita que la PRÓXIMA ingesta cree un
tercer duplicado (`ingest.common.identity.resolve_or_create_team` resuelve
"FC Barcelona" contra el nombre ya existente), pero no fusiona con
retroactividad los dos que ya estaban cargados — eso es justo lo que hace
este script, de una vez.

Se queda con `barca` como id superviviente (más partidos: 44 contra 40) y
mueve todo lo de `fcb` a `barca`:

- **Equipo**: todas las tablas con `team_id`/`home_team_id`/`away_team_id`/
  `opponent_team_id` = `'fcb'` pasan a `'barca'`; `team_external_ids` mueve el
  alias `(euroleague, BAR)` a `barca`; se borra la fila `fcb` de `teams`.
- **Jugadores**: el roster de `fcb` (16 jugadores, la plantilla de Euroliga)
  se cruza con el de `barca` (26, la de ACB) por DORSAL — es más fiable que el
  nombre aquí porque una fuente escribe "Dario Brizuela" y la otra "Darío
  Brizuela", y "Nico Laprovittola" (ACB) es "Nicolas Laprovittola" (Euroliga):
  normalizar nombres no los iguala, pero el dorsal de un jugador no cambia
  entre competiciones en la misma temporada. Los que comparten dorsal en las
  dos plantillas son la MISMA persona partida en dos `player_id`: se fusionan
  (sus filas en `player_game_stats`/`shots`/`lineup_players`/... se
  reatribuyen al `player_id` de `barca`, y la fila de `fcb` se borra). Los que
  solo están en `fcb` (2: dorsales 41 y 43, jugadores extracomunitarios que
  no estaban inscritos en ACB) simplemente cambian de `team_id`, sin fusión.

No hay colisión de clave posible al reatribuir: los partidos de `fcb` son
todos de Euroliga y los de `barca` todos de ACB/Copa (`game_id` con prefijos
distintos), así que ninguna tabla con PK `(game_id, player_id, ...)` puede
tener ya una fila de `barca` para un `game_id` que solo existe del lado `fcb`.

Uso:
    .venv/Scripts/python.exe tools/fix_barca_identity.py            # diagnóstico
    .venv/Scripts/python.exe tools/fix_barca_identity.py --apply    # escribe
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.identity import find_team_identity_collisions  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine  # noqa: E402

_SURVIVOR = "barca"
_MERGED = "fcb"

# Tablas con una columna de EQUIPO que apunta a `teams.id`, y su columna.
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

# Tablas con una columna `player_id` que hay que reatribuir al fusionar dos
# jugadores (todas referencian `players.id`, ninguna tiene `player_id` como
# única columna de su PK, así que un `UPDATE` simple no puede colisionar con
# una fila ya existente del jugador superviviente — ver docstring del módulo).
_PLAYER_TABLES = [
    "player_external_ids",
    "player_game_stats",
    "shots",
    "lineup_players",
    "lineup_stint_players",
    "play_events",
    "player_game_quarter_stats",
    "player_advanced_stats",
]


def _matched_players(conn) -> list:
    """`(fcb_player_id, barca_player_id, number, fcb_name, barca_name)` por dorsal compartido."""
    barca_by_number = {
        row.number: (row.id, row.name)
        for row in conn.execute(
            text("SELECT id, name, number FROM players WHERE team_id = :t"), {"t": _SURVIVOR}
        ).all()
    }
    matches = []
    for row in conn.execute(
        text("SELECT id, name, number FROM players WHERE team_id = :t"), {"t": _MERGED}
    ).all():
        if row.number in barca_by_number:
            survivor_id, survivor_name = barca_by_number[row.number]
            matches.append((row.id, survivor_id, row.number, row.name, survivor_name))
    return matches


def _diagnose(conn) -> None:
    counts = {
        table: conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :t"), {"t": _MERGED}).scalar_one()
        for table, col in _TEAM_TABLES
    }
    print(f"Filas de equipo que apuntan a '{_MERGED}' (se moverán a '{_SURVIVOR}'):")
    for (table, col), n in zip(_TEAM_TABLES, counts.values()):
        if n:
            print(f"  {table}.{col}: {n}")

    matches = _matched_players(conn)
    unmatched = conn.execute(
        text("SELECT id, name, number FROM players WHERE team_id = :t"), {"t": _MERGED}
    ).all()
    unmatched = [row for row in unmatched if row.id not in {m[0] for m in matches}]

    print(f"\nJugadores de '{_MERGED}' que fusionan con '{_SURVIVOR}' por dorsal ({len(matches)}):")
    for fcb_id, barca_id, number, fcb_name, barca_name in matches:
        note = "" if fcb_name == barca_name else f"  [nombres distintos: {fcb_name!r} vs {barca_name!r}]"
        print(f"  #{number:<3} {fcb_id} -> {barca_id}{note}")

    print(f"\nJugadores de '{_MERGED}' SIN dorsal compartido (solo cambian de equipo, {len(unmatched)}):")
    for row in unmatched:
        print(f"  #{row.number:<3} {row.id} ({row.name})")


def _apply(conn) -> None:
    for fcb_id, barca_id, _number, _fcb_name, _barca_name in _matched_players(conn):
        for table in _PLAYER_TABLES:
            conn.execute(
                text(f"UPDATE {table} SET player_id = :new WHERE player_id = :old"),
                {"new": barca_id, "old": fcb_id},
            )
        conn.execute(text("DELETE FROM players WHERE id = :id"), {"id": fcb_id})

    # Los jugadores de `fcb` que no fusionaron (sin dorsal compartido) se
    # quedan como jugadores propios, solo que ahora de `barca`.
    conn.execute(
        text("UPDATE players SET team_id = :new WHERE team_id = :old"), {"new": _SURVIVOR, "old": _MERGED}
    )

    for table, col in _TEAM_TABLES:
        conn.execute(text(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"), {"new": _SURVIVOR, "old": _MERGED})

    conn.execute(text("DELETE FROM teams WHERE id = :id"), {"id": _MERGED})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, solo diagnostica).")
    args = parser.parse_args()

    engine = create_scouting_engine(args.database_url)
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT id FROM teams WHERE id IN (:a, :b)"), {"a": _SURVIVOR, "b": _MERGED}
        ).all()
    ids_present = {row[0] for row in exists}
    if _MERGED not in ids_present:
        print(f"'{_MERGED}' ya no existe en esta base de datos — nada que fusionar (¿ya se aplicó este script?).")
        return
    if _SURVIVOR not in ids_present:
        print(f"'{_SURVIVOR}' no existe en esta base de datos — no se puede fusionar hacia él. Abortando.")
        return

    with engine.connect() as conn:
        _diagnose(conn)

    if not args.apply:
        print("\nEjecuta de nuevo con --apply para escribir los cambios.")
        return

    with engine.begin() as conn:
        _apply(conn)
        remaining = find_team_identity_collisions(conn)
    if remaining:
        print(f"\n⚠ Sigue habiendo colisiones de identidad tras la fusión: {remaining}")
    else:
        print(f"\nFusión completa: '{_MERGED}' -> '{_SURVIVOR}'. Sin colisiones de identidad restantes.")


if __name__ == "__main__":
    main()
