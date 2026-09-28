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
import logging
import re
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from packages.baskonia_core.names import normalize_name, normalize_name_without_aliases

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_name",
    "get_or_create_season",
    "get_competition_id",
    "resolve_or_create_team",
    "resolve_or_create_player",
    "find_team_identity_collisions",
    "find_team_identity_suggestions",
    "find_player_identity_collisions",
    "find_merged_players",
]


def _slugify(raw: str, *, max_len: int = 12) -> str:
    """Genera un id corto (slug ascii) a partir de un nombre, para filas nuevas."""
    normalized = normalize_name(raw).replace(" ", "-")
    slug = normalized[:max_len].strip("-") or "team"
    return slug


#: Longitud mínima de una palabra para contar como apellido. Descarta las
#: iniciales ("M." de "M. Howard", "A. J." de "A. J. Lawson"), que no
#: identifican a nadie: dos jugadores del mismo equipo pueden empezar por la
#: misma letra, y aceptarlas devolvería el emparejamiento por dorsal justo al
#: agujero que `_shares_a_surname` existe para tapar.
_MIN_SURNAME_LEN = 3


def _surname_words(name: str) -> set:
    """Palabras de `name` largas como para ser un apellido (ver `_MIN_SURNAME_LEN`)."""
    return {w for w in normalize_name(name).split() if len(w) >= _MIN_SURNAME_LEN}


def _shares_a_surname(name_a: str, name_b: str) -> bool:
    """`True` si los dos nombres comparten alguna palabra larga (apellido).

    Comparación deliberadamente laxa por un lado y estricta por otro: laxa en
    el ORDEN y en cuántas palabras trae cada fuente ("LARKIN, SHANE",
    "Shane Larkin" y "S. Larkin" comparten `larkin`), estricta en que tiene
    que haber una palabra de verdad en común — no vale "los dos empiezan por
    A". Ver `resolve_or_create_player` para por qué esa segunda mitad importa.
    """
    return bool(_surname_words(name_a) & _surname_words(name_b))


def _shares_a_prefix(name_a: str, name_b: str) -> bool:
    """Como `_shares_a_surname`, pero aceptando un apellido cortado por la mitad.

    Solo para comparar un `players.id` con un nombre (`find_merged_players`):
    el slug viene truncado a 12 caracteres, así que "Timothé Luwawu-Cabarrot"
    deja `timothe-luwa` y su último "apellido" es un trozo. Fuera de ahí no se
    usa — en la resolución de identidad, aceptar prefijos volvería a abrir la
    puerta a emparejar a dos personas distintas.
    """
    words_a, words_b = _surname_words(name_a), _surname_words(name_b)
    return any(a.startswith(b) or b.startswith(a) for a in words_a for b in words_b)


#: Pares de `players.id` que normalizan al mismo nombre pero que se ha
#: comprobado A MANO, contra la fuente, que son DOS PERSONAS DISTINTAS.
#: `find_player_identity_collisions` los calla; sin esta lista, un homónimo
#: real se queda gritando en cada ingesta para siempre y el aviso se vuelve
#: ruido que se deja de mirar — que es justo cuando deja de servir para
#: detectar el duplicado de verdad.
#:
#: PARA AÑADIR UNO hay que haberlo verificado en la fuente y dejar escrito
#: aquí con qué evidencia, como abajo. Ante la duda, NO se añade: un
#: duplicado real callado no lo detecta ya nadie. Solo aplica a jugadores; en
#: clubes, dos filas que normalizan igual son el mismo club y lo que
#: corresponde es fusionarlas (`tools/fix_team_identity.py`) o corregir el
#: alias demasiado laxo en `_KNOWN_TEAM_ALIASES`.
_REVIEWED_DISTINCT_PLAYERS = {
    # Dos Jaime Fernández españoles en activo en ACB a la vez. Verificado en
    # acb.com (2026-09-12) por licencia: la 20204124 es el escolta de Madrid
    # nacido el 04/06/1993, 1,86 m, hoy en La Laguna Tenerife (dorsal 3); la
    # 20212348 es el ala-pívot de Zaragoza nacido el 15/07/2000, 2,06 m, en
    # Casademont Zaragoza (dorsal 10). Fecha de nacimiento, altura, posición,
    # licencia y equipo distintos, y los dos con temporada completa a la vez:
    # no puede ser la misma persona partida en dos filas.
    ("jaime-fernan", "jaime-fernan-2"),
}


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
    acb_club_id: Optional[int] = None,
) -> str:
    """Resuelve el `teams.id` para un equipo de una fuente externa.

    Orden de resolución:
    1. `team_external_ids` (source, external_id) ya vinculado.
    2. `teams.acb_club_id` igual a `acb_club_id` (solo si se pasa: hoy, ACB).
    3. Nombre normalizado igual al de un equipo ya existente (crea el alias),
       salvo que ese equipo ya lleve OTRO `acb_club_id` (ver abajo).
    4. Ninguno de los anteriores: crea el equipo y el alias.

    EL PASO 2 ES LO QUE HACE ROBUSTA LA IDENTIDAD DE CLUB EN ACB. ACB da un
    `id` de equipo nuevo en CADA edición (no solo al cambiar de patrocinador:
    el Real Madrid es 4239, 4345, 4407 y 4476 en cuatro temporadas seguidas),
    y el nombre cambia con el patrocinador, así que ni el paso 1 ni el 3
    reconocían a "Kids&Us Manresa" como el "BAXI Manresa" del año anterior
    hasta que alguien añadía el alias a mano en `_KNOWN_TEAM_ALIASES`. Pero
    el mismo payload trae `clubId`, que NO cambia (verificado en vivo
    2026-09-28 en las ediciones 89-91: Manresa es 10 con los dos nombres,
    Lleida 658 como Hiopos/Amara/iLERNA, Burgos 549, Granada 592...). Quien
    llama solo debe pasarlo para equipos del PRIMER EQUIPO: los equipos de
    cantera (Liga U, `competitionId` 134 en el propio equipo) comparten el
    `clubId` del club — "Fundacion CB Canarias" lleva el 28 de La Laguna
    Tenerife — y fundirlos con el primer equipo sería un error. Ver
    `ingest/acb/adapter.py::senior_acb_club_id`.

    El `acb_club_id` se apunta además en la fila resuelta si no lo tenía, así
    que las filas antiguas lo van ganando solas en cada ingesta. Si la fila
    ya lleva OTRO distinto, no se pisa: se avisa (dos clubes de ACB en una
    fila es un alias demasiado laxo, o un emparejamiento por nombre
    equivocado) y, en el paso 3, no se empareja — se crea una fila aparte,
    que `find_team_identity_collisions` delatará por nombre para revisarla.

    Deliberadamente NO hay aquí emparejamiento difuso por nombre ("Kids&Us
    Manresa" ~ "BAXI Manresa"). Se estudió y no es seguro en el momento de
    resolver: la señal que separa un cambio de patrocinador de dos clubes de
    la misma ciudad es que los segundos juegan la misma competición la misma
    temporada, y un equipo que aparece por primera vez todavía no ha jugado
    nada — el primer partido de un Hapoel Tel Aviv recién ascendido es
    indistinguible del primer partido de un Maccabi con patrocinador nuevo.
    El difuso vive aparte, sobre datos ya cargados y solo como SUGERENCIA
    (`find_team_identity_suggestions`).

    Args:
        logo_url: si se pasa (hoy solo `ingest/acb`, que trae `teams[].logo`
            real en el mismo payload de calendario — ver
            `AcbClient.fetch_season_scheduled_matches`), actualiza
            `teams.logo_url` tanto si el equipo ya existía como si se acaba
            de crear. `None` (por defecto) no toca la columna — así las
            demás fuentes (`baskonia_web`, `euroleague`), que no tienen
            escudo, no la pisan a `NULL` sin querer.
        acb_club_id: `clubId` estable de ACB del equipo (ver arriba). `None`
            (por defecto, y siempre desde otras fuentes) salta el paso 2 y no
            toca la columna.
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
        if acb_club_id is not None:
            match = _team_by_acb_club_id(conn, acb_club_id)

        if match is None:
            normalized = normalize_name(name)
            existing_teams = conn.execute(text("SELECT id, name, acb_club_id FROM teams")).all()
            by_name = next((t for t in existing_teams if normalize_name(t.name) == normalized), None)
            if by_name is not None and _conflicting_club_ids(by_name.acb_club_id, acb_club_id):
                logger.warning(
                    "equipo %r (%s %s, clubId ACB %s) se llama igual que %r pero esa fila es del "
                    "clubId ACB %s: NO se emparejan por nombre, se crea una fila aparte (revisa "
                    "_KNOWN_TEAM_ALIASES)",
                    name, source, external_id, acb_club_id, by_name.id, by_name.acb_club_id,
                )
                by_name = None
            match = by_name.id if by_name is not None else None

        if match is None:
            team_id = _unique_id(conn, "teams", _slugify(name))
            conn.execute(
                text(
                    "INSERT INTO teams (id, name, is_own_team, acb_club_id)"
                    " VALUES (:id, :name, 0, :acb_club_id)"
                ),
                {"id": team_id, "name": name, "acb_club_id": acb_club_id},
            )
            match = team_id

        conn.execute(
            text(
                "INSERT INTO team_external_ids (team_id, source, external_id)"
                " VALUES (:team_id, :source, :external_id)"
            ),
            {"team_id": match, "source": source, "external_id": external_id},
        )

    if acb_club_id is not None:
        _record_acb_club_id(conn, match, acb_club_id)

    if logo_url:
        conn.execute(text("UPDATE teams SET logo_url = :logo_url WHERE id = :id"), {"logo_url": logo_url, "id": match})

    return match


def _conflicting_club_ids(a: Optional[int], b: Optional[int]) -> bool:
    """`True` si los dos `acb_club_id` existen y son distintos (dos clubes de ACB)."""
    return a is not None and b is not None and int(a) != int(b)


def _team_by_acb_club_id(conn: Connection, acb_club_id: int) -> Optional[str]:
    """`teams.id` que lleva ese `clubId` de ACB, o `None`.

    Si hay varias (filas duplicadas de antes de que existiera la columna, a la
    espera de `tools/fix_team_identity.py`), la misma preferencia que el
    superviviente de esa herramienta: el equipo propio, luego el que más
    partidos tiene — para no seguir engordando la fila que va a desaparecer.
    """
    row = conn.execute(
        text(
            "SELECT t.id FROM teams t WHERE t.acb_club_id = :club"
            " ORDER BY t.is_own_team DESC,"
            " (SELECT COUNT(*) FROM games g WHERE g.home_team_id = t.id OR g.away_team_id = t.id) DESC,"
            " t.id"
        ),
        {"club": int(acb_club_id)},
    ).first()
    return row[0] if row is not None else None


def _record_acb_club_id(conn: Connection, team_id: str, acb_club_id: int) -> None:
    """Apunta `acb_club_id` en `team_id` si no tenía; avisa si tenía otro."""
    current = conn.execute(
        text("SELECT acb_club_id FROM teams WHERE id = :id"), {"id": team_id}
    ).scalar_one_or_none()
    if current is None:
        conn.execute(
            text("UPDATE teams SET acb_club_id = :club WHERE id = :id"),
            {"club": int(acb_club_id), "id": team_id},
        )
    elif int(current) != int(acb_club_id):
        logger.warning(
            "la fila de equipo %r es del clubId ACB %s pero acaba de resolverse para el clubId %s: "
            "dos clubes en una fila (¿alias demasiado laxo en _KNOWN_TEAM_ALIASES?). No se toca.",
            team_id, current, acb_club_id,
        )


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
    2. Mismo `team_id` + mismo dorsal (`number`) **y algún apellido en común**.
    3. Mismo `team_id` + nombre normalizado igual.
    4. Ninguno de los anteriores: crea el jugador y el alias.

    Si el jugador ya existía, actualiza dorsal/posición cuando cambian
    (idempotente: no falla, no duplica).

    EL APELLIDO EN COMÚN DEL PASO 2 NO ES UN ADORNO. El dorsal es lo único de
    esta lista que NO identifica a una persona: se reutiliza de una temporada
    a otra, y quien hereda el dorsal heredaba con él la FILA de su predecesor.
    Sin esa comprobación, el paso 2 fusionaba dos jugadores distintos en un
    solo `players.id` — se renombraba la fila y todos los `player_game_stats`,
    tramos, tiros y faltas del primero pasaban a colgar del segundo. Caso real
    en `data/baskonia.db`: la fila `alberto-abal` (creada para Alberto Abalde,
    Real Madrid #33) acabó llamándose "Gunars Grinvalds" con SIETE
    `external_id` de ACB y 79 partidos encima — siete personas distintas que
    fueron pasando por el 33. Y no era raro: 101 filas tenían dos o más
    `external_id` de la misma fuente.

    Es corrupción silenciosa, y peor que el fallo contrario: un jugador
    partido en dos filas se detecta (`find_player_identity_collisions`) y se
    arregla (`tools/fix_player_identity.py`); dos jugadores fundidos en una
    no se detectan y ya no se pueden separar, porque el dato de a quién
    pertenecía cada partido se ha perdido. Ante la duda, esta función prefiere
    duplicar antes que fundir.

    El paso 2 sigue existiendo porque resuelve un caso real que el 3 no pilla:
    la misma persona escrita distinto en cada fuente ("M. Howard" en Euroliga
    contra "Marcus Howard" en ACB) no tiene el nombre normalizado igual, pero
    sí comparte apellido y dorsal.
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
        candidates = conn.execute(
            text("SELECT id, name FROM players WHERE team_id = :team_id AND number = :number"),
            {"team_id": team_id, "number": number},
        ).all()
        player_id = next((c.id for c in candidates if _shares_a_surname(c.name, name)), None)

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


def find_team_identity_collisions(conn: Connection) -> List[Tuple[str, str]]:
    """Pares de `teams.id` que normalizan al mismo nombre pero viven bajo ids distintos.

    Comprobación de integridad post-ingesta ("ningún club con dos
    identidades", `doc/features/propuestas/04_fatiga_y_calendario.md` §5).
    Caso real que la motiva: el Barça llegó a existir como `barca` ("Barça",
    creado desde ACB) y `fcb` ("FC Barcelona", creado desde Euroliga) hasta
    que `_KNOWN_TEAM_ALIASES` (`packages/baskonia_core/names.py`) los unificó
    — mientras estuvieron separados, cualquier agregado por `team_id` de ese
    club (récord, carga de minutos, descanso entre partidos...) veía solo la
    mitad de sus partidos, sin que nada lo avisara. `resolve_or_create_team`
    ya evita crear un duplicado NUEVO una vez que el alias existe, pero no
    fusiona uno que ya se creó antes de que se añadiera el alias — esta
    función solo DETECTA esa situación (para loguearla al final de una
    ingesta, ver `ingest/run_all.py`); arreglar una detectada es una
    migración de datos de una vez (`tools/fix_barca_identity.py` es el
    ejemplo real), no algo que la ingesta pueda deshacer sola.

    Desde 2026-09-28 cuenta también como colisión EXACTA que dos filas lleven
    el mismo `teams.acb_club_id`: es el identificador estable del club en ACB
    (ver `resolve_or_create_team`), así que no hay duda posible — son el
    mismo club aunque se llamen distinto ("BAXI Manresa"/"Kids&Us Manresa"
    sin alias). Es lo que permite fusionarlas sin haber tocado
    `_KNOWN_TEAM_ALIASES`. Las parecidas-pero-no-seguras van aparte, como
    sugerencia (`find_team_identity_suggestions`).

    Returns:
        Lista de `(team_id_a, team_id_b)`, ordenado alfabéticamente dentro de
        cada par, uno por cada equipo adicional que comparte nombre
        normalizado (o `acb_club_id`) con uno visto antes. Vacía si no hay
        colisiones.
    """
    rows = conn.execute(text("SELECT id, name, acb_club_id FROM teams ORDER BY id")).all()
    collisions: List[Tuple[str, str]] = []
    seen_pairs: Set[frozenset] = set()
    for key_of in (lambda r: normalize_name(r.name), lambda r: r.acb_club_id):
        seen: dict = {}
        for row in rows:
            key = key_of(row)
            if key is None:
                continue
            if key in seen:
                pair = tuple(sorted((seen[key], row.id)))
                if frozenset(pair) not in seen_pairs:
                    seen_pairs.add(frozenset(pair))
                    collisions.append(pair)
            else:
                seen[key] = row.id
    return collisions


#: Palabras de nombre de club que no identifican a un club concreto aunque
#: sean largas: si dos equipos solo comparten una de estas, no es señal de
#: nada ("Real Madrid"/"Real Betis", "Gran Canaria"/"Gran ...", los equipos
#: de cantera "Fundación X" de clubes distintos). NO es una lista de
#: patrocinadores (esa sería otra vez una lista a mano que se queda vieja):
#: son palabras genéricas del idioma de los nombres de club, que no cambian.
_GENERIC_TEAM_WORDS = {
    "real", "gran", "fundacion", "union", "sporting", "atletico", "athletic",
    "basketball", "basketbol", "basquetbol", "koszykowka", "club", "team",
    "city", "grupo", "sociedad", "deportiva", "deportivo", "olimpia",
}

#: Longitud mínima de una palabra para contar como "núcleo" del nombre.
#: Deja fuera artículos, siglas y restos de patrocinador cortos ("us" de
#: "Kids&Us", "fc", "as", "tfe", "zgz").
_MIN_CORE_WORD_LEN = 4


def _team_core_words(name: str) -> Set[str]:
    """Palabras del nombre que pueden identificar a un club (ciudad/nombre propio).

    "Kids&Us Manresa" -> {kids, manresa}; "BAXI Manresa" -> {baxi, manresa}:
    comparten `manresa`. No intenta saber cuál es el patrocinador: basta con
    que dos nombres compartan ALGUNA palabra de este conjunto para que sean
    candidatos, y lo que separa a los candidatos buenos de los malos no es el
    nombre sino los datos (ver `find_team_identity_suggestions`).
    """
    return {
        w for w in normalize_name_without_aliases(name).split()
        if len(w) >= _MIN_CORE_WORD_LEN and w not in _GENERIC_TEAM_WORDS and not w.isdigit()
    }


def find_team_identity_suggestions(conn: Connection) -> List[dict]:
    """Pares de clubes que PARECEN el mismo pero sin prueba: solo para revisar a mano.

    Es la mitad difusa del problema de identidad de club, la que no cubre ni
    `acb_club_id` (solo existe en ACB, y solo en filas que una ingesta ya ha
    rellenado) ni `_KNOWN_TEAM_ALIASES` (solo lo que alguien ya apuntó):
    "Kids&Us Manresa" y "BAXI Manresa" comparten `manresa`; el nombre de un
    club en Euroliga y en ACB, cuando ninguno de los dos está en la lista.

    NUNCA fusiona ni vincula nada, y no lo hará `ingest/run_all.py
    --merge-team-duplicates` tampoco (esa solo toca colisiones exactas). La
    razón es que "comparten una palabra" es exactamente lo que pasa con dos
    clubes de la misma ciudad — Anadolu Efes y Fenerbahçe (`istanbul`),
    Maccabi y Hapoel (`aviv`), Crvena Zvezda y Partizan (`belgrade`), Real
    Madrid y un "Estudiantes Madrid" —, y fundir dos clubes en una fila es
    corrupción que no se deshace. Para aceptar una sugerencia: añadir el
    alias a `_KNOWN_TEAM_ALIASES` y fusionar con `tools/fix_team_identity.py`.

    Un par comparte alguna palabra de `_team_core_words` y NO es ya una
    colisión exacta, y se DESCARTA (no se sugiere) si hay prueba de que son
    dos clubes:

    - los dos jugaron (o tienen calendario, `upcoming_matchups`) en la misma
      competición la misma temporada: un club no juega dos veces la misma
      liga — es lo que separa a los dos de Estambul;
    - se han enfrentado entre sí;
    - los dos tienen `acb_club_id` y es distinto;
    - los dos tienen `code` de Euroliga y no comparten ninguno (el `code` de
      Euroliga es estable por club).

    `ambiguous=True` marca un par en el que alguno de los dos tiene OTRO
    candidato que es incompatible con el primero (p.ej. un equipo nuevo "X
    Istanbul" que podría ser tanto el Efes como el Fenerbahçe): ahí ni
    siquiera la sugerencia apunta a un club concreto.

    Returns:
        `[{"team_ids": (a, b), "names": (name_a, name_b), "shared_words":
        [...], "ambiguous": bool}]`, ordenada por `team_ids`. Vacía si no hay
        nada que sugerir.
    """
    teams = conn.execute(text("SELECT id, name, acb_club_id FROM teams ORDER BY id")).all()
    if len(teams) < 2:
        return []

    played: Dict[str, Set[Tuple[int, int]]] = defaultdict(set)
    opponents: Dict[str, Set[str]] = defaultdict(set)
    for g in conn.execute(
        text("SELECT season_id, competition_id, home_team_id, away_team_id FROM games")
    ).all():
        played[g.home_team_id].add((g.season_id, g.competition_id))
        played[g.away_team_id].add((g.season_id, g.competition_id))
        opponents[g.home_team_id].add(g.away_team_id)
        opponents[g.away_team_id].add(g.home_team_id)
    for u in conn.execute(
        text("SELECT opponent_team_id, season_id, competition_id FROM upcoming_matchups")
    ).all():
        played[u.opponent_team_id].add((u.season_id, u.competition_id))

    euroleague_codes: Dict[str, Set[str]] = defaultdict(set)
    for row in conn.execute(
        text("SELECT team_id, external_id FROM team_external_ids WHERE source = 'euroleague'")
    ).all():
        euroleague_codes[row.team_id].add(row.external_id)

    info = {
        t.id: {
            "name": t.name,
            "normalized": normalize_name(t.name),
            "club": t.acb_club_id,
            "core": _team_core_words(t.name),
        }
        for t in teams
    }

    def exact(a: str, b: str) -> bool:
        return info[a]["normalized"] == info[b]["normalized"] or (
            info[a]["club"] is not None and info[a]["club"] == info[b]["club"]
        )

    def provably_distinct(a: str, b: str) -> bool:
        if played[a] & played[b] or b in opponents[a]:
            return True
        if _conflicting_club_ids(info[a]["club"], info[b]["club"]):
            return True
        codes_a, codes_b = euroleague_codes.get(a), euroleague_codes.get(b)
        return bool(codes_a and codes_b and not codes_a & codes_b)

    candidates: Dict[str, Set[str]] = defaultdict(set)
    shared_by_pair: Dict[Tuple[str, str], Set[str]] = {}
    for a, b in combinations(sorted(info), 2):
        shared = info[a]["core"] & info[b]["core"]
        if not shared or exact(a, b) or provably_distinct(a, b):
            continue
        candidates[a].add(b)
        candidates[b].add(a)
        shared_by_pair[(a, b)] = shared

    suggestions = []
    for (a, b), shared in sorted(shared_by_pair.items()):
        ambiguous = any(provably_distinct(b, c) for c in candidates[a] - {b}) or any(
            provably_distinct(a, c) for c in candidates[b] - {a}
        )
        suggestions.append(
            {
                "team_ids": (a, b),
                "names": (info[a]["name"], info[b]["name"]),
                "shared_words": sorted(shared),
                "ambiguous": ambiguous,
            }
        )
    return suggestions


def find_player_identity_collisions(conn: Connection) -> List[Tuple[str, str]]:
    """Pares de `players.id` que normalizan al mismo nombre pero viven bajo ids distintos.

    Mismo defecto que `find_team_identity_collisions`, a nivel de jugador:
    `resolve_or_create_player` empareja por `(source, external_id)` primero
    y, si no hay alias todavía, por `team_id` + dorsal/nombre — así que un
    jugador que llega a dos fuentes (ACB/Euroliga) con un `team_id` distinto
    en cada una (fichaje reciente, o la fuente aún no tiene su ficha
    actualizada al equipo nuevo) puede acabar con dos `players.id`
    separados aunque el nombre sea idéntico. Caso real: Jabari Parker con
    `jabari-parke` (ACB, `external_id` 30002721) y `parker-jabar`
    (Euroliga, `external_id` P012745) — cada tramo de quinteto/falta que
    reconstruye el timeline de rotaciones
    (`app/components/rotation_chart.py`) sale bajo UNO de los dos ids, así
    que el jugador aparece dos veces en el mismo gráfico (una fila con
    tramos, otra solo con faltas) en vez de una.

    A diferencia del de equipos, aquí SÍ es posible un falso positivo (dos
    jugadores distintos con el mismo nombre completo) — nombre normalizado
    igual es una señal fuerte pero no una prueba; por eso esta función solo
    DETECTA (para loguearlo al final de una ingesta, igual que la de
    equipos), nunca fusiona sola. Arreglar una colisión detectada es una
    migración de datos revisada a mano, ver `tools/fix_player_identity.py`.

    Y el falso positivo no es hipotético: hay DOS Jaime Fernández españoles
    jugando en ACB a la vez (ver `_REVIEWED_DISTINCT_PLAYERS`). Un homónimo
    real no se puede "arreglar" — se queda ahí — así que, una vez comprobado
    contra la fuente que son dos personas, su par se apunta en esa lista y
    deja de contarse aquí. Si no, el aviso sale en cada ingesta para siempre
    y acaba siendo ruido que se deja de leer, que es cuando deja de servir
    para ver el duplicado de verdad.

    Returns:
        Lista de `(player_id_a, player_id_b)`, ordenado alfabéticamente
        dentro de cada par, sin los pares ya revisados. Vacía si no hay
        colisiones.
    """
    rows = conn.execute(text("SELECT id, name FROM players ORDER BY id")).all()
    seen: dict = {}
    collisions: List[Tuple[str, str]] = []
    for row in rows:
        key = normalize_name(row.name)
        if key in seen:
            pair = tuple(sorted((seen[key], row.id)))
            if pair not in _REVIEWED_DISTINCT_PLAYERS:
                collisions.append(pair)
        else:
            seen[key] = row.id
    return collisions


def find_merged_players(conn: Connection) -> List[Tuple[str, str]]:
    """Filas de `players` que son DOS personas distintas metidas en una sola.

    El fallo contrario a `find_player_identity_collisions`, y bastante peor:
    allí un jugador está partido en dos filas (molesto, reversible); aquí dos
    jugadores comparten fila, y con ella sus `player_game_stats`, tramos,
    tiros y faltas. Lo causaba el emparejamiento por dorsal de
    `resolve_or_create_player` antes de exigir apellido en común (ver su
    docstring): el dorsal se reutiliza entre temporadas, así que quien lo
    heredaba heredaba la fila de su predecesor, que se renombraba en el sitio.

    CÓMO SE DETECTA, y por qué se puede: `players.id` es un slug generado a
    partir del nombre **en el momento de crear la fila** (`_slugify`) y no se
    reescribe nunca. Es, de hecho, el único rastro que queda del primer
    ocupante. Si el slug no comparte ningún apellido con el nombre ACTUAL, la
    fila se renombró a otra persona. Caso real de `data/baskonia.db`:
    `alberto-abal` llamándose "Gunars Grinvalds", con ocho licencias de ACB y
    79 partidos encima — ocho personas que fueron pasando por el 33 del Real
    Madrid.

    Solo DETECTA, nunca arregla: separar una fila fusionada exige volver a
    ingerir los partidos afectados, porque el dato de a quién pertenecía cada
    línea ya no está en la base de datos. Ver `tools/report_merged_players.py`
    para el informe de qué habría que rehacer.

    Returns:
        Lista de `(player_id, nombre_actual)`, ordenada por id. Vacía si no
        hay ninguna. Los nombres demasiado cortos para tener apellido
        (iniciales sueltas como "A. De", que dejan un slug `a-de`) se omiten:
        ahí no hay nada que comparar, y contarlos sería ruido.
    """
    merged: List[Tuple[str, str]] = []
    for row in conn.execute(text("SELECT id, name FROM players ORDER BY id")).all():
        # `-2`, `-3`... son el desempate de `_unique_id`, no parte del nombre.
        slug = re.sub(r"-\d+$", "", row.id).replace("-", " ")
        if not _surname_words(slug) or not _surname_words(row.name):
            continue  # indecidible, no sospechoso
        # `_slugify` trunca a 12 caracteres, así que el último apellido del
        # slug puede venir cortado ("timothe-luwa"): se acepta el prefijo.
        if not _shares_a_surname(slug, row.name) and not _shares_a_prefix(slug, row.name):
            merged.append((row.id, row.name))
    return merged


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
