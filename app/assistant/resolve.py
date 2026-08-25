"""Resolución determinista de entidades (jugador/equipo) y de tiempo. Sin modelo.

Es la parte que más determina si el chat "funciona" y la que más barata sale
hacer bien (`local/features/005-chatbot/01_design.md` §5): aquí no hay LLM, y
por tanto todo esto se prueba con `pytest` y no cambia de una ejecución a la
siguiente. Que "Fenerbache" resuelva a `fenerbahce-b` no depende del modelo
del día — es lo que permite que un modelo pequeño acierte la respuesta aunque
razone regular (§8.4).

La normalización es **la misma** con la que se cargaron los datos
(`packages/baskonia_core/names.py::normalize_name`, antes en
`ingest/common/identity.py`). Escribir una segunda es cómo se resuelve mal
justo el caso raro.

Regla que no se negocia (§5.1): **con empate real no se elige**. Con
"Howard" hay varios jugadores distintos en la base de datos; devolver el
primero en silencio es el fallo más caro de todos, porque produce una
respuesta segura sobre la persona equivocada. Se devuelven todos los
candidatos y desambigua el modelo (preguntando, si hace falta).
"""
import datetime as dt
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.baskonia_core.names import normalize_name

# Umbral del match difuso. 0.75 cubre "Fenerbache" (el error del propio
# encargo) y "Markus"/"Marcus" Howard sin empezar a devolver equipos que no
# tienen nada que ver. Se usa `difflib` de la librería estándar en vez de
# añadir `rapidfuzz`: 968 jugadores y 64 equipos son un espacio diminuto y es
# una dependencia menos en la imagen de la Pi (§5.1).
_FUZZY_THRESHOLD = 0.75

# Longitud mínima de un token para compararlo suelto. Sin esto, "de"/"la"
# de un nombre compuesto casan con cualquier cosa corta.
_MIN_TOKEN_LEN = 4

# Dos candidatos separados por menos de esto son un empate: la herramienta lo
# marca como ambiguo y el modelo pregunta en vez de elegir.
_TIE_MARGIN = 0.05

# Alias propios (§5.1, paso 4): lo que escribe una persona -> el nombre
# normalizado que sí está en la base de datos. No son ids: se resuelven
# después por la misma cascada que cualquier otro nombre, así que siguen
# funcionando si el id del equipo cambia.
_ALIASES = {
    "fener": "fenerbahce",
    "el fener": "fenerbahce",
    "barsa": "barcelona",
    "barca": "barcelona",
    "madrid": "real madrid",
    "efes": "anadolu efes",
    "pana": "panathinaikos",
    "oly": "olympiacos",
    "olympiakos": "olympiacos",
    "zalgiris": "zalgiris kaunas",
    "maccabi": "maccabi tel aviv",
    "estrella roja": "crvena zvezda",
    "asvel": "ldlc asvel",
    "unicaja": "unicaja malaga",
    "canarias": "la laguna tenerife",
    "tenerife": "la laguna tenerife",
    "penya": "joventut badalona",
    "joventut": "joventut badalona",
    "obradoiro": "monbus obradoiro",
    "granada": "coviran granada",
}

# Cómo se refiere el usuario al equipo propio sin nombrarlo.
_OWN_TEAM_ALIASES = {
    "nosotros", "el equipo", "nuestro equipo", "mi equipo", "el baskonia",
    "los nuestros", "casa",
}

# Artículos que se quitan por delante antes de buscar alias ("el Fener").
_LEADING_ARTICLES = ("el ", "la ", "los ", "las ", "un ", "una ")


@dataclass(frozen=True)
class Candidate:
    """Un candidato de resolución, con de dónde salió y cuánto se parece.

    `matched_by` no es decorativo: es lo que permite leer la traza del chat y
    entender por qué el asistente creyó que "el Fener" era el Fenerbahçe.
    """

    id: str
    name: str
    kind: str  # 'player' | 'team'
    team: Optional[str]
    score: float
    matched_by: str

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical_query(raw: str) -> str:
    """Consulta del usuario -> nombre normalizado listo para comparar (con alias aplicados)."""
    normalized = normalize_name(raw)
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    for article in _LEADING_ARTICLES:
        if normalized.startswith(article):
            stripped = normalized[len(article):]
            return _ALIASES.get(stripped, stripped)
    return normalized


def _similarity(query: str, name: str) -> float:
    """Parecido entre la consulta y un nombre, mirando también token a token.

    El nombre completo suele ser más largo que lo que escribe una persona
    ("Fenerbahce Beko Istanbul" frente a "Fenerbache"), y comparar cadenas
    enteras castiga esa diferencia de longitud hasta hacer inútil el umbral.
    Comparar además contra cada token largo del nombre es lo que rescata el
    caso real.
    """
    best = SequenceMatcher(None, query, name).ratio()
    for token in name.split():
        if len(token) >= _MIN_TOKEN_LEN:
            best = max(best, SequenceMatcher(None, query, token).ratio())
    return best


def _score(query: str, raw_query: str, entity_id: str, name: str, external_ids: set) -> tuple:
    """`(score, matched_by)` de un candidato, o `(0.0, "")` si no llega al umbral.

    Cascada de §5.1, primer acierto gana: nombre normalizado, id externo de la
    fuente, id propio exacto, contención de tokens (apellido suelto) y difuso.

    Args:
        query: la consulta ya normalizada (sin acentos ni puntuación).
        raw_query: la consulta en crudo, solo en minúsculas. Los ids —propios
            y externos— llevan guiones (`fenerbahce-b`, `ACB-12345`) que la
            normalización se come, así que compararlos contra la versión
            normalizada nunca casaría.
    """
    normalized_name = normalize_name(name)
    if query == normalized_name:
        return 0.98, "nombre"
    if raw_query in external_ids or query in external_ids:
        return 0.96, "id externo"

    query_tokens = set(query.split())
    name_tokens = set(normalized_name.split())
    partial = bool(query_tokens) and query_tokens <= name_tokens

    # Id exacto y apellido suelto puntúan IGUAL, y no por descuido: los ids de
    # `players` son slugs derivados del propio nombre (`howard`), así que un
    # id exacto y un apellido suelto son, escritos por una persona, la misma
    # cosa. Puntuar el id por encima haría que "Howard" eligiera en silencio
    # al jugador cuyo slug coincide y descartara a los otros dos Howard
    # reales de la base de datos — justo el fallo que §5.1 prohíbe.
    if raw_query == entity_id.lower() or query == entity_id.lower():
        return 0.95, "id"
    if partial:
        return 0.95, "parcial"

    similarity = _similarity(query, normalized_name)
    if similarity >= _FUZZY_THRESHOLD:
        # Techo por debajo del escalón de arriba: un parecido altísimo sigue
        # siendo una conjetura y no debe adelantar a una coincidencia exacta.
        return round(min(similarity, 0.94), 3), "difuso"
    return 0.0, ""


def _load_teams(engine: Engine) -> List[tuple]:
    sql = text(
        """
        SELECT t.id, t.name,
               COALESCE(GROUP_CONCAT(LOWER(x.external_id), '|'), '') AS external_ids
        FROM teams t
        LEFT JOIN team_external_ids x ON x.team_id = t.id
        GROUP BY t.id, t.name
        """
    )
    with engine.connect() as conn:
        return [(row[0], row[1], set(filter(None, row[2].split("|")))) for row in conn.execute(sql)]


def _load_players(engine: Engine) -> List[tuple]:
    sql = text(
        """
        SELECT p.id, p.name, t.name AS team_name,
               COALESCE(GROUP_CONCAT(LOWER(x.external_id), '|'), '') AS external_ids
        FROM players p
        JOIN teams t ON t.id = p.team_id
        LEFT JOIN player_external_ids x ON x.player_id = p.id
        GROUP BY p.id, p.name, t.name
        """
    )
    with engine.connect() as conn:
        return [
            (row[0], row[1], row[2], set(filter(None, row[3].split("|"))))
            for row in conn.execute(sql)
        ]


def own_team_id(engine: Engine) -> Optional[str]:
    """Id del equipo propio (`teams.is_own_team = 1`), o `None` si no hay ninguno."""
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM teams WHERE is_own_team = 1 LIMIT 1")).fetchone()
    return row[0] if row else None


def resolve_entity(engine: Engine, query: str, kind: Optional[str] = None, limit: int = 5) -> List[Candidate]:
    """Candidatos para lo que ha escrito el usuario, mejor primero.

    Args:
        engine: engine de solo lectura sobre la BD de scouting.
        query: texto libre ("Markus Howard", "Fenerbache", "nosotros").
        kind: `'player'` o `'team'` para acotar; `None` busca en los dos.
        limit: cuántos candidatos devolver como mucho.

    Returns:
        Lista de `Candidate` ordenada por `score` descendente. Vacía si nada
        llega al umbral — que es una respuesta legítima y mucho mejor que
        devolver el equipo menos malo.
    """
    normalized = _canonical_query(query)
    raw_key = query.strip().lower()
    if not normalized:
        return []

    candidates: List[Candidate] = []

    if normalized in _OWN_TEAM_ALIASES and kind in (None, "team"):
        team_id = own_team_id(engine)
        if team_id is not None:
            with engine.connect() as conn:
                name = conn.execute(text("SELECT name FROM teams WHERE id = :id"), {"id": team_id}).scalar()
            return [Candidate(id=team_id, name=name, kind="team", team=None, score=1.0, matched_by="equipo propio")]

    if kind in (None, "team"):
        for team_id, name, external_ids in _load_teams(engine):
            score, matched_by = _score(normalized, raw_key, team_id, name, external_ids)
            if score:
                candidates.append(Candidate(team_id, name, "team", None, score, matched_by))

    if kind in (None, "player"):
        for player_id, name, team_name, external_ids in _load_players(engine):
            score, matched_by = _score(normalized, raw_key, player_id, name, external_ids)
            if score:
                candidates.append(Candidate(player_id, name, "player", team_name, score, matched_by))

    candidates.sort(key=lambda c: (-c.score, c.name))
    return candidates[:limit]


def is_ambiguous(candidates: List[Candidate]) -> bool:
    """`True` si los dos mejores candidatos están tan cerca que elegir sería inventar."""
    return len(candidates) >= 2 and (candidates[0].score - candidates[1].score) < _TIE_MARGIN


# ---------------------------------------------------------------------------
# Tiempo (§5.2). Todo lo relativo se resuelve contra un `today` INYECTADO, no
# contra `date.today()` dentro de la consulta — mismo motivo que ya documenta
# `app/data/queries.py`: determinismo y cacheabilidad.
# ---------------------------------------------------------------------------


def previous_season_id(engine: Engine, season_id: int) -> Optional[int]:
    """Temporada inmediatamente anterior a `season_id` ("la temporada pasada")."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM seasons WHERE id < :season_id ORDER BY id DESC LIMIT 1"),
            {"season_id": season_id},
        ).fetchone()
    return row[0] if row else None


def resolve_player_game(
    engine: Engine,
    player_id: str,
    *,
    season_id: Optional[int] = None,
    when: str = "last",
) -> Optional[str]:
    """`game_id` del partido de un JUGADOR, por defecto el último que jugó.

    "Su último partido" es el último con fila en `player_game_stats` **para
    ese jugador**, no el último del equipo: no siempre jugó (§5.2). Confundir
    las dos cosas produce una respuesta con las cifras de otro partido, que
    es exactamente el tipo de error que nadie detecta leyendo.

    Args:
        when: `'last'` (el último), `'first'` (el primero de la temporada) o
            una fecha ISO `YYYY-MM-DD` (el partido de ese día).
    """
    clauses = ["pgs.player_id = :player_id"]
    params = {"player_id": player_id}
    if season_id is not None:
        clauses.append("g.season_id = :season_id")
        params["season_id"] = season_id

    order = "DESC"
    if when == "first":
        order = "ASC"
    elif when != "last":
        clauses.append("g.game_date = :game_date")
        params["game_date"] = when

    sql = text(
        f"""
        SELECT g.id
        FROM player_game_stats pgs
        JOIN games g ON g.id = pgs.game_id
        WHERE {' AND '.join(clauses)}
        ORDER BY g.game_date {order}
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def resolve_team_game(
    engine: Engine,
    team_id: str,
    *,
    when: str = "last",
    opponent_id: Optional[str] = None,
    competition_id: Optional[int] = None,
    season_id: Optional[int] = None,
    today: Optional[dt.date] = None,
) -> Optional[str]:
    """`game_id` de un partido de EQUIPO ya disputado.

    Args:
        when: `'last'` (el más reciente hasta `today`), `'first'`, o una fecha
            ISO `YYYY-MM-DD`.
        opponent_id: acota a los cruces contra ese rival ("contra el Madrid").
        today: fecha de referencia, inyectada (ver cabecera de sección). Por
            defecto no acota: útil en pruebas con datos futuros.

    El próximo partido NO sale de aquí: los partidos futuros viven en
    `upcoming_matchups`, no en `games`, y los sirve la herramienta
    `next_opponent`.
    """
    clauses = ["(g.home_team_id = :team_id OR g.away_team_id = :team_id)"]
    params = {"team_id": team_id}
    if opponent_id:
        clauses.append("(g.home_team_id = :opponent_id OR g.away_team_id = :opponent_id)")
        params["opponent_id"] = opponent_id
    if competition_id is not None:
        clauses.append("g.competition_id = :competition_id")
        params["competition_id"] = competition_id
    if season_id is not None:
        clauses.append("g.season_id = :season_id")
        params["season_id"] = season_id
    if today is not None:
        clauses.append("g.game_date <= :today")
        params["today"] = today.isoformat()

    order = "DESC"
    if when == "first":
        order = "ASC"
    elif when != "last":
        clauses.append("g.game_date = :game_date")
        params["game_date"] = when

    sql = text(
        f"""
        SELECT g.id FROM games g
        WHERE {' AND '.join(clauses)}
        ORDER BY g.game_date {order}
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None
