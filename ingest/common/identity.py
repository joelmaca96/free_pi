"""Resolución de identidad entre fuentes (equipos y jugadores) y temporadas.

Cada fuente (`acb`, `euroleague`, `baskonia_web`) nombra/identifica a un mismo
equipo o jugador de forma distinta. Estas funciones son el único punto donde
se decide "esto ya existe" vs "esto es nuevo", vía las tablas puente
`team_external_ids`/`player_external_ids` (y, en su ausencia, un match por
nombre normalizado). Se usan desde los tres módulos de ingesta para que la
resolución de identidad sea idéntica en los tres.

`normalize_name` ya no se define aquí: vive en
`packages/baskonia_core/names.py` desde que el asistente de scouting
(`app/assistant/resolve.py`) necesita normalizar exactamente igual que la
ingesta, y `ingest/` no viaja en la imagen de la interfaz (ver el docstring
de ese módulo para el razonamiento completo). Se reexporta desde aquí para
que todo lo que ya la importaba de `ingest.common.identity` siga funcionando.
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from packages.baskonia_core.names import normalize_name

__all__ = [
    "normalize_name",
    "get_or_create_season",
    "get_competition_id",
    "resolve_or_create_team",
    "resolve_or_create_player",
]


def _slugify(raw: str, *, max_len: int = 12) -> str:
    """Genera un id corto (slug ascii) a partir de un nombre, para filas nuevas."""
    normalized = normalize_name(raw).replace(" ", "-")
    slug = normalized[:max_len].strip("-") or "team"
    return slug


def get_or_create_season(conn: Connection, start_year: int) -> int:
    """Devuelve el id de la temporada `start_year`-`start_year+1`, creándola si falta."""
    label = f"{start_year}-{start_year + 1}"
    row = conn.execute(text("SELECT id FROM seasons WHERE label = :label"), {"label": label}).first()
    if row is not None:
        return row[0]
    result = conn.execute(text("INSERT INTO seasons (label) VALUES (:label)"), {"label": label})
    return result.lastrowid


def get_competition_id(conn: Connection, name: str) -> int:
    """Devuelve el id de una competición por nombre (`'ACB'`, `'Euroliga'`...), creándola si falta."""
    row = conn.execute(text("SELECT id FROM competitions WHERE name = :name"), {"name": name}).first()
    if row is not None:
        return row[0]
    result = conn.execute(text("INSERT INTO competitions (name) VALUES (:name)"), {"name": name})
    return result.lastrowid


def resolve_or_create_team(
    conn: Connection,
    source: str,
    external_id: str,
    name: str,
    logo_url: Optional[str] = None,
) -> str:
    """Resuelve el `teams.id` para un equipo de una fuente externa.

    Orden de resolución:
    1. `team_external_ids` (source, external_id) ya vinculado.
    2. Nombre normalizado igual al de un equipo ya existente (crea el alias).
    3. Ninguno de los anteriores: crea el equipo y el alias.

    Args:
        logo_url: si se pasa (hoy solo `ingest/acb`, que trae `teams[].logo`
            real en el mismo payload de calendario — ver
            `AcbClient.fetch_season_scheduled_matches`), actualiza
            `teams.logo_url` tanto si el equipo ya existía como si se acaba
            de crear. `None` (por defecto) no toca la columna — así las
            demás fuentes (`baskonia_web`, `euroleague`), que no tienen
            escudo, no la pisan a `NULL` sin querer.
    """
    row = conn.execute(
        text(
            "SELECT team_id FROM team_external_ids"
            " WHERE source = :source AND external_id = :external_id"
        ),
        {"source": source, "external_id": external_id},
    ).first()
    match = row[0] if row is not None else None

    if match is None:
        normalized = normalize_name(name)
        existing_teams = conn.execute(text("SELECT id, name FROM teams")).all()
        match = next((t.id for t in existing_teams if normalize_name(t.name) == normalized), None)

        if match is None:
            team_id = _unique_id(conn, "teams", _slugify(name))
            conn.execute(
                text("INSERT INTO teams (id, name, is_own_team) VALUES (:id, :name, 0)"),
                {"id": team_id, "name": name},
            )
            match = team_id

        conn.execute(
            text(
                "INSERT INTO team_external_ids (team_id, source, external_id)"
                " VALUES (:team_id, :source, :external_id)"
            ),
            {"team_id": match, "source": source, "external_id": external_id},
        )

    if logo_url:
        conn.execute(text("UPDATE teams SET logo_url = :logo_url WHERE id = :id"), {"logo_url": logo_url, "id": match})

    return match


def resolve_or_create_player(
    conn: Connection,
    source: str,
    external_id: str,
    name: str,
    team_id: str,
    number: Optional[int] = None,
    position: Optional[str] = None,
) -> str:
    """Resuelve el `players.id` para un jugador de una fuente externa.

    Orden de resolución (igual para las tres fuentes):
    1. `player_external_ids` (source, external_id) ya vinculado.
    2. Mismo `team_id` + mismo dorsal (`number`), si se conoce.
    3. Mismo `team_id` + nombre normalizado igual.
    4. Ninguno de los anteriores: crea el jugador y el alias.

    Si el jugador ya existía, actualiza dorsal/posición cuando cambian
    (idempotente: no falla, no duplica).
    """
    row = conn.execute(
        text(
            "SELECT player_id FROM player_external_ids"
            " WHERE source = :source AND external_id = :external_id"
        ),
        {"source": source, "external_id": external_id},
    ).first()
    player_id = row[0] if row is not None else None

    if player_id is None and number is not None:
        row = conn.execute(
            text("SELECT id FROM players WHERE team_id = :team_id AND number = :number"),
            {"team_id": team_id, "number": number},
        ).first()
        player_id = row[0] if row is not None else None

    if player_id is None:
        normalized = normalize_name(name)
        candidates = conn.execute(
            text("SELECT id, name FROM players WHERE team_id = :team_id"), {"team_id": team_id}
        ).all()
        match = next((p.id for p in candidates if normalize_name(p.name) == normalized), None)
        player_id = match

    if player_id is None:
        player_id = _unique_id(conn, "players", _slugify(name))
        conn.execute(
            text(
                "INSERT INTO players (id, team_id, name, number, position)"
                " VALUES (:id, :team_id, :name, :number, :position)"
            ),
            {
                "id": player_id,
                "team_id": team_id,
                "name": name,
                "number": number if number is not None else 0,
                "position": position or "",
            },
        )
    else:
        set_clauses = ["name = :name"]
        if number is not None:
            set_clauses.append("number = :number")
        if position:
            set_clauses.append("position = :position")
        conn.execute(
            text(f"UPDATE players SET {', '.join(set_clauses)} WHERE id = :id"),
            {
                "id": player_id,
                "name": name,
                **({"number": number} if number is not None else {}),
                **({"position": position} if position else {}),
            },
        )

    conn.execute(
        text(
            "INSERT OR IGNORE INTO player_external_ids (player_id, source, external_id)"
            " VALUES (:player_id, :source, :external_id)"
        ),
        {"player_id": player_id, "source": source, "external_id": external_id},
    )
    return player_id


def _unique_id(conn: Connection, table: str, base_slug: str) -> str:
    """Devuelve `base_slug`, o `base_slug-2`/`-3`... si ya existe en `table`."""
    if table not in ("teams", "players"):
        raise ValueError(f"tabla no soportada: {table}")
    candidate = base_slug
    suffix = 2
    while conn.execute(text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": candidate}).first() is not None:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate
