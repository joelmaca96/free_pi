"""Fusiona jugadores duplicados (mismo nombre, dos `players.id` distintos).

Generaliza `tools/fix_barca_identity.py` (que fusionaba UN par de equipos
conocido a mano) a CUALQUIER colisión que detecte
`ingest.common.identity.find_player_identity_collisions`: un jugador que
llegó a `resolve_or_create_player` con un `team_id` distinto en cada fuente
(fichaje reciente, o la fuente sin actualizar todavía) y por tanto acabó con
dos filas en `players` — señal real: un jugador que aparece DOS VECES en el
mismo timeline de rotaciones (`app/components/rotation_chart.py`), una fila
con sus tramos de quinteto y otra solo con las faltas, porque los tramos
(`lineup_stint_players`) llegaron bajo un `player_id` y las faltas
(`play_events`) bajo el otro.

Superviviente de cada par:

1. Si uno de los dos `player_id` vive bajo un equipo con `is_own_team = 1`
   (el Baskonia) Y ADEMÁS está `active = 1` (confirmado por
   `ingest/baskonia_web` contra la plantilla oficial), ESE gana siempre,
   aunque tenga menos filas — es la señal más fuerte de "esta es la
   identidad vigente". Caso real que SÍ debe ganar así: Chris Duarte tiene
   mucho más histórico bajo su club anterior (Unicaja) que bajo el Baskonia
   recién fichado — con solo "más filas" el fichaje se fusionaría DENTRO del
   club anterior, y "plantilla"/"estado del equipo" dejarían de encontrarlo
   como jugador propio.
   OJO — `is_own_team` SOLO no basta: `players.team_id` no se actualiza
   nunca tras crearse (ver `resolve_or_create_player`), así que una ficha
   vieja/placeholder con `team_id='bas'` de alguien que YA NO está en el
   club sigue teniendo `is_own_team=1` para siempre. Caso real que lo
   delató: 'moneke' (Chima Moneke) tenía `team_id='bas'` pero `active=0` —
   ya fuera de plantilla — y aun así "ganó" en una versión anterior de esta
   regla (solo `is_own_team`), colgándole sus 1756 filas reales del Crvena
   Zvezda a una ficha que ya no representaba dónde juega de verdad. De ahí
   el `active` en la condición.
2. Si no, el `player_id` con MÁS filas repartidas entre las tablas de
   `_PLAYER_TABLES` (más partidos/eventos registrados con ese id) — mismo
   criterio que usó a mano `fix_barca_identity.py` ("se queda con el que
   tiene más partidos"), aquí calculado en vez de fijado. Sin señal de
   equipo propio activo en ningún lado (colisión entre dos clubes rivales,
   o entre el Baskonia y un club rival cuando ninguno de los dos está
   `active`), es la mejor aproximación disponible a "la identidad más
   completa", aunque no garantiza que sea la del club MÁS RECIENTE del
   jugador.

Uso:
    .venv/Scripts/python.exe tools/fix_player_identity.py            # diagnóstico
    .venv/Scripts/python.exe tools/fix_player_identity.py --apply    # escribe

Revisa SIEMPRE la salida del diagnóstico antes de `--apply`: nombre
normalizado igual es una señal fuerte pero no una prueba (ver el docstring de
`find_player_identity_collisions`) — dos jugadores distintos con el mismo
nombre completo, aunque raro, no es imposible.
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.common.identity import find_player_identity_collisions  # noqa: E402
from packages.baskonia_core.db.scouting import create_scouting_engine  # noqa: E402

# Mismas tablas que `fix_barca_identity._PLAYER_TABLES`: todas referencian
# `players.id` vía `player_id`, ninguna lo tiene como única columna de su PK,
# así que un `UPDATE` simple no puede colisionar con una fila ya existente
# del jugador superviviente.
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


def _row_count(conn, player_id: str) -> int:
    return sum(
        conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE player_id = :id"), {"id": player_id}).scalar_one()
        for table in _PLAYER_TABLES
    )


def _player_info(conn, player_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT p.name, p.team_id, p.active, t.is_own_team FROM players p"
            " JOIN teams t ON t.id = p.team_id WHERE p.id = :id"
        ),
        {"id": player_id},
    ).one()
    return {
        "name": row.name,
        "team_id": row.team_id,
        "active": bool(row.active),
        "is_own_team": bool(row.is_own_team),
    }


def _pick_survivor(conn, a: str, b: str) -> tuple:
    """Decide superviviente entre `a`/`b`: equipo propio ACTIVO gana siempre; si no, más filas.

    `is_own_team` (equipo Baskonia) sin más NO basta — `players.team_id` no se
    actualiza nunca tras crearse (ver `resolve_or_create_player`), así que una
    ficha vieja/placeholder con `team_id='bas'` de alguien que ya no está en
    el club sigue teniendo `is_own_team=1` para siempre. Caso real que lo
    delató: 'moneke' (Chima Moneke) tenía `team_id='bas'` pero
    `active=0` — baskonia_web ya lo había marcado como fuera de plantilla — y
    aun así "ganó" la fusión por ser del equipo propio, colgándole sus 1756
    filas reales del Crvena Zvezda a una ficha que ya no representaba dónde
    juega de verdad. `active` es la señal que faltaba: la mantiene
    `ingest/baskonia_web` contra la plantilla oficial en cada ingesta, así que
    dice "vigente ahora mismo", no solo "alguna vez fue del equipo propio".
    """
    info = {pid: _player_info(conn, pid) for pid in (a, b)}
    own_active = [pid for pid in (a, b) if info[pid]["is_own_team"] and info[pid]["active"]]
    if len(own_active) == 1:
        survivor = own_active[0]
        reason = "equipo propio activo"
    else:
        counts = {pid: _row_count(conn, pid) for pid in (a, b)}
        survivor = max(counts, key=counts.get)
        reason = "más filas"
    loser = b if survivor == a else a
    return survivor, loser, reason, info


def _diagnose(conn, collisions) -> list:
    """Devuelve `(survivor_id, loser_id, survivor_name)` por cada par, superviviente ya decidido."""
    plan = []
    print(f"{len(collisions)} colisión(es) de identidad de jugador:\n")
    for a, b in collisions:
        survivor, loser, reason, info = _pick_survivor(conn, a, b)
        counts = {pid: _row_count(conn, pid) for pid in (a, b)}
        print(
            f"  {info[survivor]['name']!r} [{reason}]: "
            f"{survivor!r} ({counts[survivor]} filas, team_id={info[survivor]['team_id']}) "
            f"<- {loser!r} ({counts[loser]} filas, team_id={info[loser]['team_id']})"
        )
        plan.append((survivor, loser, info[survivor]["name"]))
    return plan


def _apply(conn, plan) -> None:
    for survivor, loser, _name in plan:
        for table in _PLAYER_TABLES:
            conn.execute(
                text(f"UPDATE {table} SET player_id = :new WHERE player_id = :old"),
                {"new": survivor, "old": loser},
            )
        conn.execute(text("DELETE FROM players WHERE id = :id"), {"id": loser})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="URL SQLAlchemy (por defecto config.DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (sin esto, solo diagnostica).")
    parser.add_argument(
        "--skip-id",
        action="append",
        default=[],
        metavar="PLAYER_ID",
        help=(
            "Excluye del diagnóstico/fusión cualquier par que contenga este `player_id` (repetible). "
            "Para un par donde NINGÚN lado slugifica del nombre actual (p.ej. 'alberto-abal' con "
            "nombre 'Gunars Grinvalds') — señal de que el id ya venía de OTRO jugador antes y el "
            "fallback team_id+dorsal de `resolve_or_create_player` lo pisó por error: eso no es un "
            "duplicado simple, es varios jugadores reales conflados bajo un id, y fusionar aquí "
            "encima solo lo empeora. Revísalo aparte."
        ),
    )
    args = parser.parse_args()
    skip_ids = set(args.skip_id)

    engine = create_scouting_engine(args.database_url)
    with engine.connect() as conn:
        collisions = find_player_identity_collisions(conn)
    if skip_ids:
        excluded = [pair for pair in collisions if skip_ids & set(pair)]
        collisions = [pair for pair in collisions if not skip_ids & set(pair)]
        for pair in excluded:
            print(f"Excluido por --skip-id: {pair}")
        if excluded:
            print()

    if not collisions:
        print("Sin colisiones de identidad de jugador — nada que fusionar.")
        return

    with engine.connect() as conn:
        plan = _diagnose(conn, collisions)

    if not args.apply:
        print("\nEjecuta de nuevo con --apply para escribir los cambios.")
        return

    with engine.begin() as conn:
        _apply(conn, plan)
        remaining = find_player_identity_collisions(conn)
    if remaining:
        # Sin acento ni símbolo no-ASCII: la consola de Windows (cp1252) no
        # los codifica y tira el traceback DESPUÉS de que el `UPDATE`/`DELETE`
        # de más arriba ya se ha confirmado (`engine.begin()` ya cerró la
        # transacción) — cosmético, pero confuso.
        print(f"\nOJO: siguen quedando colisiones tras la fusión: {remaining}")
    else:
        print(f"\nFusión completa: {len(plan)} par(es) fusionado(s). Sin colisiones de jugador restantes.")


if __name__ == "__main__":
    main()
