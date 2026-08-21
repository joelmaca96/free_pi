"""Resolución de identidad entre fuentes (equipos y jugadores) y temporadas.

Cada fuente (`acb`, `euroleague`, `baskonia_web`) nombra/identifica a un mismo
equipo o jugador de forma distinta. Estas funciones son el único punto donde
se decide "esto ya existe" vs "esto es nuevo", vía las tablas puente
`team_external_ids`/`player_external_ids` (y, en su ausencia, un match por
nombre normalizado). Se usan desde los tres módulos de ingesta para que la
resolución de identidad sea idéntica en los tres.
"""
import re
import unicodedata
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Palabras/sufijos habituales en nombres de clubes que sobran para comparar
# (p.ej. "Valencia Basket Club" vs "Valencia" deben normalizar igual).
_CLUB_NOISE_WORDS = {
    "club", "baloncesto", "basket", "basquet", "cb", "bc", "sad", "s.a.d",
    "saski", "kirolak", "kk", "bk",
}


# Alias conocidos que la normalización genérica no resuelve (nombres de
# patrocinador, que cambian de temporada en temporada: "Kosner Baskonia",
# "Saski Baskonia"... todos son el mismo equipo "Baskonia").
_KNOWN_TEAM_ALIASES = {
    "kosner baskonia": "baskonia",
    "saski baskonia": "baskonia",
    "baskonia vitoria gasteiz": "baskonia",
}


def normalize_name(raw: str) -> str:
    """Normaliza un nombre (jugador o equipo) para comparar entre fuentes.

    Quita acentos, pasa a minúsculas, elimina puntuación y palabras de ruido
    de club, y colapsa espacios. P.ej. "Valencia Basket Club" y "Valencia"
    normalizan ambos a "valencia". También resuelve alias conocidos de
    patrocinador (`_KNOWN_TEAM_ALIASES`).
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_only = ascii_only.lower()
    ascii_only = re.sub(r"[^a-z0-9\s]", " ", ascii_only)
    words = [w for w in ascii_only.split() if w not in _CLUB_NOISE_WORDS]
    normalized = " ".join(words).strip()
    return _KNOWN_TEAM_ALIASES.get(normalized, normalized)


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
) -> str:
    """Resuelve el `teams.id` para un equipo de una fuente externa.

    Orden de resolución:
    1. `team_external_ids` (source, external_id) ya vinculado.
    2. Nombre normalizado igual al de un equipo ya existente (crea el alias).
    3. Ninguno de los anteriores: crea el equipo y el alias.
    """
    row = conn.execute(
        text(
            "SELECT team_id FROM team_external_ids"
            " WHERE source = :source AND external_id = :external_id"
        ),
        {"source": source, "external_id": external_id},
    ).first()
    if row is not None:
        return row[0]

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
