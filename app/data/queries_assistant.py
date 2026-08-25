"""Consultas que solo necesita el asistente de scouting (`app/assistant/`).

Mismo estilo y mismas reglas que `app/data/queries.py` (`_engine` sin hashear,
`@st.cache_data`, SQL parametrizado, solo `SELECT`): esto es un módulo
hermano, no una capa nueva. Vive aparte para que quede claro de un vistazo
qué consulta existe porque la pinta una pantalla y cuál existe porque la pide
una herramienta del chat — y para no engordar un fichero que ya tiene 800
líneas.

Lo que NO va aquí: nada que `queries.py` ya resuelva. Las herramientas llaman
a `player_game_log`, `team_advanced_profile`, `head_to_head`, `game_boxscore`,
`team_zone_profile`... tal cual (`local/features/005-chatbot/01_design.md`
§3.5).

Varias de estas consultas leen vistas añadidas en la misma entrega
(`team_style_percentiles`, `player_percentiles`, `lineup_team`) o tablas de
la fase 3 (`lineup_stints`). Una base de datos que todavía no las tenga es un
caso ESPERADO, no un error: `app/assistant/capabilities.py` lo sondea y apaga
la herramienta correspondiente antes de que el modelo la pueda llamar.
"""
import datetime as dt
from typing import List, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_TTL = 3600  # mismo criterio que `queries.py`: la ingesta corre por su cuenta.


@st.cache_data(ttl=_TTL, show_spinner=False)
def _table_columns(_engine: Engine, table: str) -> frozenset:
    """Columnas que ESTA base de datos tiene en `table` (no las que promete `schema.sql`).

    La base de datos desplegada va por detrás de las migraciones aditivas:
    `player_game_stats.ftm`/`fta` existen en el esquema versionado desde el
    2026-08-24 y **no** en `data/baskonia.db` hasta que se reingiere. Una
    consulta que las nombre sin más falla con `no such column` en producción
    y funciona en desarrollo, que es la peor combinación posible.

    `app/assistant/capabilities.py` sondea lo mismo para decidir qué se puede
    PROMETER; esto es la otra mitad: qué se puede CONSULTAR.
    """
    return frozenset(col["name"] for col in inspect(_engine).get_columns(table))


@st.cache_data(ttl=_TTL, show_spinner=False)
def _has_view(_engine: Engine, view: str) -> bool:
    """`True` si la vista existe en esta base de datos (mismo motivo que `_table_columns`)."""
    return view in set(inspect(_engine).get_view_names())

# Métricas que se pueden pedir en un ranking de liga, con la columna y la
# vista de la que salen. Lista blanca a propósito: es lo que permite que
# `league_leaders` reciba un nombre de métrica del modelo sin que eso sea
# jamás una inyección (el nombre no llega a interpolarse si no está aquí).
LEADER_METRICS = {
    "pts": ("player", "pts_avg", "puntos por partido"),
    "reb": ("player", "reb_avg", "rebotes por partido"),
    "ast": ("player", "ast_avg", "asistencias por partido"),
    "min": ("player", "min_avg", "minutos por partido"),
    "efg": ("player", "efg_pct", "eFG%"),
    "pace": ("team", "pace", "posesiones por partido"),
    "ortg": ("team", "ortg", "rating ofensivo"),
    "drtg": ("team", "drtg", "rating defensivo"),
    "net_rating": ("team", "net_rating", "net rating"),
    "team_efg": ("team", "efg_pct", "eFG% de equipo"),
    "team_ts": ("team", "ts_pct", "TS% de equipo"),
}

# Métricas donde MENOS es mejor: ordenar todas descendente pondría al peor
# defensor del campeonato en lo alto de "mejor defensa".
_LOWER_IS_BETTER = {"drtg"}


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_header(_engine: Engine, game_id: str) -> Optional[dict]:
    """Cabecera de un partido: fecha, competición, equipos y marcador.

    Es la procedencia que acompaña a cualquier cifra de partido (§1): sin
    ella, "21 puntos" no dice de qué partido sale y la respuesta deja de ser
    comprobable.
    """
    sql = text("""
        SELECT g.id, g.game_date, c.name AS competition, s.label AS season_label, g.season_id,
               g.competition_id, g.home_team_id, g.away_team_id,
               th.name AS home_team, ta.name AS away_team,
               g.home_score, g.away_score, g.pace
        FROM games g
        JOIN competitions c ON c.id = g.competition_id
        JOIN seasons s ON s.id = g.season_id
        JOIN teams th ON th.id = g.home_team_id
        JOIN teams ta ON ta.id = g.away_team_id
        WHERE g.id = :game_id
    """)
    df = pd.read_sql(sql, _engine, params={"game_id": game_id})
    return None if df.empty else df.iloc[0].to_dict()


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_game_line(_engine: Engine, game_id: str, player_id: str) -> Optional[dict]:
    """Línea de boxscore de un jugador en un partido concreto.

    Incluye `ftm`/`fta` tal cual salen de la base de datos: `NULL` significa
    "partido ingerido antes de que existieran esas columnas", no "cero tiros
    libres" (ver `schema.sql`). Quien pinte esto tiene que distinguirlo — de
    ahí que se devuelvan crudos y no rellenados a 0.
    """
    # Los tiros libres se piden solo si la columna existe aquí (ver
    # `_table_columns`); si no, se devuelven como NULL, que es exactamente lo
    # que significan: "este dato no está", no "cero tiros libres".
    has_ft = {"ftm", "fta"} <= _table_columns(_engine, "player_game_stats")
    ft_select = "pgs.ftm, pgs.fta" if has_ft else "NULL AS ftm, NULL AS fta"
    sql = text(f"""
        SELECT pgs.player_id, p.name AS player_name, p.team_id, t.name AS team_name,
               pgs.minutes, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct, {ft_select}
        FROM player_game_stats pgs
        JOIN players p ON p.id = pgs.player_id
        JOIN teams t ON t.id = p.team_id
        WHERE pgs.game_id = :game_id AND pgs.player_id = :player_id
    """)
    df = pd.read_sql(sql, _engine, params={"game_id": game_id, "player_id": player_id})
    return None if df.empty else df.iloc[0].to_dict()


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_shots_for_player(_engine: Engine, game_id: str, player_id: str) -> pd.DataFrame:
    """Tiros de UN jugador en UN partido, con las columnas de `components/court.py`."""
    sql = text("""
        SELECT s.pos_x, s.pos_y, s.made, COALESCE(s.located, 1) AS located, p.name AS player_name
        FROM shots s
        JOIN players p ON p.id = s.player_id
        WHERE s.game_id = :game_id AND s.player_id = :player_id
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "player_id": player_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_zone_profile(
    _engine: Engine, player_id: str, season_id: int, game_id: Optional[str] = None
) -> pd.DataFrame:
    """Volumen y acierto de tiro por zona de un jugador (temporada o un partido).

    Se calcula desde `shots` (tiro a tiro) y no desde `game_zone_stats` (que
    agrega por EQUIPO y no permite bajar a jugador). Los tiros sin zona
    clasificada quedan fuera en vez de caer en un cajón "otros": una zona
    inventada se leería como una zona real.

    Returns:
        `zone_label, attempts, made, fg_pct, share` (proporción de sus
        intentos en esa zona, que es lo que describe un perfil de tiro —
        acertar el 60% en la pintura no dice nada si solo tira dos veces).
    """
    clauses = ["s.player_id = :player_id", "s.zone_id IS NOT NULL"]
    params = {"player_id": player_id, "season_id": season_id}
    if game_id is not None:
        clauses.append("s.game_id = :game_id")
        params["game_id"] = game_id
    else:
        clauses.append("g.season_id = :season_id")

    sql = text(f"""
        SELECT cz.label AS zone_label,
               COUNT(*) AS attempts,
               SUM(s.made) AS made,
               100.0 * SUM(s.made) / COUNT(*) AS fg_pct
        FROM shots s
        JOIN games g ON g.id = s.game_id
        JOIN court_zones cz ON cz.id = s.zone_id
        WHERE {' AND '.join(clauses)}
        GROUP BY cz.label
        ORDER BY attempts DESC
    """)
    df = pd.read_sql(sql, _engine, params=params)
    if df.empty:
        return df
    total = df["attempts"].sum()
    return df.assign(share=100.0 * df["attempts"] / total)


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_style_row(
    _engine: Engine, team_id: str, season_id: int, competition_id: Optional[int] = None
) -> pd.DataFrame:
    """Perfil de equipo CON percentiles de liga (`team_style_percentiles`).

    Una fila por competición jugada (con `gp >= 5`, que es el filtro de la
    vista). El percentil siempre es dentro de su propia competición: comparar
    un pace de ACB con uno de Euroliga es comparar dos ligas con ritmos
    distintos, no dos equipos.
    """
    clauses = ["p.team_id = :team_id", "p.season_id = :season_id"]
    params = {"team_id": team_id, "season_id": season_id}
    if competition_id is not None:
        clauses.append("p.competition_id = :competition_id")
        params["competition_id"] = competition_id

    sql = text(f"""
        SELECT c.name AS competition, p.competition_id, p.gp, p.league_teams,
               p.pace, p.pace_pct, p.ortg, p.ortg_pct, p.drtg, p.drtg_pct,
               p.net_rating, p.net_rating_pct, p.efg_pct, p.efg_pct_pct, p.ts_pct, p.ts_pct_pct
        FROM team_style_percentiles p
        JOIN competitions c ON c.id = p.competition_id
        WHERE {' AND '.join(clauses)}
        ORDER BY p.gp DESC
    """)
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_percentile_row(
    _engine: Engine, player_id: str, season_id: int, competition_id: Optional[int] = None
) -> pd.DataFrame:
    """Percentiles de un jugador dentro de su competición y temporada."""
    clauses = ["p.player_id = :player_id", "p.season_id = :season_id"]
    params = {"player_id": player_id, "season_id": season_id}
    if competition_id is not None:
        clauses.append("p.competition_id = :competition_id")
        params["competition_id"] = competition_id

    sql = text(f"""
        SELECT c.name AS competition, p.gp, p.league_players,
               p.min_avg, p.min_pct, p.pts_avg, p.pts_pct, p.reb_avg, p.reb_pct,
               p.ast_avg, p.ast_pct, p.efg_pct, p.efg_pct_pct
        FROM player_percentiles p
        JOIN competitions c ON c.id = p.competition_id
        WHERE {' AND '.join(clauses)}
        ORDER BY p.gp DESC
    """)
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def league_leaders(
    _engine: Engine,
    metric: str,
    season_id: int,
    competition_id: int,
    min_gp: int = 5,
    limit: int = 10,
) -> pd.DataFrame:
    """Top N por métrica, dentro de una competición y temporada.

    Args:
        metric: clave de `LEADER_METRICS`. Cualquier otra cosa es un error de
            programación, no una consulta: se valida ANTES de tocar el SQL.
        min_gp: mínimo de partidos jugados — sin él, el líder de eFG% es
            siempre alguien que jugó un partido y metió su único tiro.

    Returns:
        `rank, id, name, value, gp` (+ `team` si la métrica es de jugador).

    Raises:
        ValueError: si `metric` no está en `LEADER_METRICS`.
    """
    if metric not in LEADER_METRICS:
        raise ValueError(f"métrica no soportada: {metric!r} (válidas: {sorted(LEADER_METRICS)})")

    scope, column, _ = LEADER_METRICS[metric]
    direction = "ASC" if metric in _LOWER_IS_BETTER else "DESC"

    if scope == "player":
        sql = text(f"""
            SELECT p.id, p.name, t.name AS team, s.gp, s.{column} AS value
            FROM player_stats_by_competition s
            JOIN players p ON p.id = s.player_id
            JOIN teams t ON t.id = p.team_id
            WHERE s.season_id = :season_id AND s.competition_id = :competition_id
              AND s.gp >= :min_gp AND s.{column} IS NOT NULL
            ORDER BY s.{column} {direction}
            LIMIT :limit
        """)
    else:
        sql = text(f"""
            SELECT t.id, t.name, NULL AS team, s.gp, s.{column} AS value
            FROM team_stats_by_competition s
            JOIN teams t ON t.id = s.team_id
            WHERE s.season_id = :season_id AND s.competition_id = :competition_id
              AND s.gp >= :min_gp AND s.{column} IS NOT NULL
            ORDER BY s.{column} {direction}
            LIMIT :limit
        """)

    df = pd.read_sql(
        sql,
        _engine,
        params={
            "season_id": season_id,
            "competition_id": competition_id,
            "min_gp": min_gp,
            "limit": limit,
        },
    )
    return df.assign(rank=range(1, len(df) + 1)) if not df.empty else df


@st.cache_data(ttl=_TTL, show_spinner=False)
def standings(_engine: Engine, season_id: int, competition_id: int, today: dt.date) -> pd.DataFrame:
    """Clasificación calculada desde `games` (no hay tabla de clasificación en el esquema).

    Solo partidos ya disputados (`game_date <= today`): con el calendario
    cargado por delante, contar partidos futuros daría a todo el mundo un
    récord de 0-0 inflado con ceros.
    """
    sql = text("""
        WITH results AS (
            SELECT g.home_team_id AS team_id,
                   CASE WHEN g.home_score > g.away_score THEN 1 ELSE 0 END AS win,
                   g.home_score AS pts_for, g.away_score AS pts_against
            FROM games g
            WHERE g.season_id = :season_id AND g.competition_id = :competition_id
              AND g.game_date <= :today
            UNION ALL
            SELECT g.away_team_id,
                   CASE WHEN g.away_score > g.home_score THEN 1 ELSE 0 END,
                   g.away_score, g.home_score
            FROM games g
            WHERE g.season_id = :season_id AND g.competition_id = :competition_id
              AND g.game_date <= :today
        )
        SELECT t.id AS team_id, t.name AS team,
               COUNT(*) AS gp,
               SUM(r.win) AS wins,
               COUNT(*) - SUM(r.win) AS losses,
               SUM(r.pts_for) - SUM(r.pts_against) AS point_diff
        FROM results r
        JOIN teams t ON t.id = r.team_id
        GROUP BY t.id, t.name
        ORDER BY wins DESC, point_diff DESC
    """)
    df = pd.read_sql(
        sql,
        _engine,
        params={"season_id": season_id, "competition_id": competition_id, "today": today.isoformat()},
    )
    return df.assign(rank=range(1, len(df) + 1)) if not df.empty else df


# Camino canónico: la vista `lineup_team` decide de qué equipo es un quinteto
# en UN solo sitio (dato explícito si lo hay, mayoría de `players.team_id` si
# no) y marca `is_inferred`.
_LINEUP_ROWS_SQL = """
    SELECT lt.lineup_id, lt.minutes, lt.plus_minus, lt.is_inferred,
           p.id AS player_id, p.name AS player_name
    FROM lineup_team lt
    JOIN games g ON g.id = lt.game_id
    JOIN lineup_players lp ON lp.lineup_id = lt.lineup_id
    JOIN players p ON p.id = lp.player_id
    WHERE lt.team_id = :team_id AND g.season_id = :season_id
      AND (:competition_id IS NULL OR g.competition_id = :competition_id)
"""

# Camino de compatibilidad para una base de datos que todavía no tiene la
# vista (no se ha reingerido desde que se añadió). Es LITERALMENTE lo que ya
# hacía `queries.season_lineups` antes de que existiera: atribuir el quinteto
# al equipo ACTUAL de sus jugadores. Por eso `is_inferred` es 1 fijo — porque
# aquí siempre lo está.
#
# Sí, la lógica queda en dos sitios, y no es un descuido: la alternativa era
# no registrar las herramientas de quintetos hasta la siguiente ingesta, y los
# quintetos globales SE PUEDEN contestar hoy. La vista es la fuente de verdad
# en cuanto existe; esto es un puente con fecha de caducidad.
_LINEUP_ROWS_LEGACY_SQL = """
    SELECT l.id AS lineup_id, l.minutes, l.plus_minus, 1 AS is_inferred,
           p.id AS player_id, p.name AS player_name
    FROM lineups l
    JOIN games g ON g.id = l.game_id
    JOIN lineup_players lp ON lp.lineup_id = l.id
    JOIN players p ON p.id = lp.player_id
    WHERE p.team_id = :team_id AND g.season_id = :season_id
      AND (:competition_id IS NULL OR g.competition_id = :competition_id)
"""


def _lineup_rows(
    _engine: Engine, team_id: str, season_id: int, competition_id: Optional[int]
) -> pd.DataFrame:
    """Filas crudas quinteto-jugador del equipo, por la vista o por el puente."""
    sql = _LINEUP_ROWS_SQL if _has_view(_engine, "lineup_team") else _LINEUP_ROWS_LEGACY_SQL
    return pd.read_sql(
        text(sql),
        _engine,
        params={"team_id": team_id, "season_id": season_id, "competition_id": competition_id},
    )


def _aggregate_lineups(raw: pd.DataFrame) -> pd.DataFrame:
    """Agrega las filas crudas por combinación real de cinco jugadores.

    Mismo criterio que `queries.season_lineups`: se agrupa por la combinación
    de nombres (orden alfabético estable, así la misma combinación siempre da
    la misma cadena) y no por `lineup_id`, que es un tramo suelto.
    """
    if raw.empty:
        return pd.DataFrame(columns=["jugadores", "player_ids", "minutes", "plus_minus", "stints", "is_inferred"])

    raw = raw.sort_values("player_name")
    per_lineup = raw.groupby("lineup_id").agg(
        jugadores=("player_name", " · ".join),
        player_ids=("player_id", lambda ids: ",".join(sorted(ids))),
        minutes=("minutes", "first"),
        plus_minus=("plus_minus", "first"),
        is_inferred=("is_inferred", "first"),
    )
    combos = per_lineup.groupby("jugadores").agg(
        player_ids=("player_ids", "first"),
        minutes=("minutes", "sum"),
        plus_minus=("plus_minus", "sum"),
        stints=("minutes", "size"),
        is_inferred=("is_inferred", "max"),
    )
    return combos.reset_index()


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_lineups(
    _engine: Engine,
    team_id: str,
    season_id: int,
    competition_id: Optional[int] = None,
    min_minutes: float = 10.0,
    limit: int = 10,
    order_by: str = "plus_minus",
) -> pd.DataFrame:
    """Quintetos de un equipo en una temporada, ordenados por rendimiento o por uso.

    Args:
        min_minutes: minutos mínimos jugados juntos. Sin un mínimo, el "mejor
            quinteto" es siempre uno que jugó cuarenta segundos y anotó un
            triple — ruido presentado como conclusión.
        order_by: `'plus_minus'` (rendimiento) o `'minutes'` (uso real).

    Returns:
        `jugadores, player_ids, minutes, plus_minus, plus_minus_per_40,
        stints, is_inferred`. `is_inferred = 1` avisa de que el equipo del
        quinteto se dedujo del equipo ACTUAL de sus jugadores.
    """
    combos = _aggregate_lineups(_lineup_rows(_engine, team_id, season_id, competition_id))
    if combos.empty:
        return combos

    combos = combos[combos["minutes"] >= min_minutes].copy()
    if combos.empty:
        return combos
    # Normalizado por 40 minutos y no por 100 posesiones: `lineups` no guarda
    # posesiones por tramo, y estimarlas desde el pace del partido entero
    # sería inventarse una precisión que el dato no tiene.
    combos["plus_minus_per_40"] = 40.0 * combos["plus_minus"] / combos["minutes"]

    sort_columns = ["plus_minus_per_40", "minutes"] if order_by == "plus_minus" else ["minutes", "plus_minus"]
    return combos.sort_values(sort_columns, ascending=False).head(limit).reset_index(drop=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_pair_impact(
    _engine: Engine,
    team_id: str,
    player_a: str,
    player_b: str,
    season_id: int,
    competition_id: Optional[int] = None,
) -> pd.DataFrame:
    """Rendimiento del equipo con dos jugadores JUNTOS frente a cada uno sin el otro.

    Returns:
        Una fila por situación (`juntos`, `solo A`, `solo B`, `ninguno`) con
        `minutes`, `plus_minus` y `plus_minus_per_40`. Vacío si no hay
        quintetos cargados para ese filtro.
    """
    raw = _lineup_rows(_engine, team_id, season_id, competition_id)
    if raw.empty:
        return pd.DataFrame(columns=["situacion", "minutes", "plus_minus", "plus_minus_per_40"])

    by_lineup = raw.groupby("lineup_id").agg(
        minutes=("minutes", "first"),
        plus_minus=("plus_minus", "first"),
        players=("player_id", set),
    )
    labels = {
        (True, True): "juntos",
        (True, False): "solo A",
        (False, True): "solo B",
        (False, False): "ninguno",
    }
    by_lineup["situacion"] = [
        labels[(player_a in players, player_b in players)] for players in by_lineup["players"]
    ]
    grouped = by_lineup.groupby("situacion").agg(minutes=("minutes", "sum"), plus_minus=("plus_minus", "sum"))
    grouped = grouped.reset_index()
    grouped["plus_minus_per_40"] = 40.0 * grouped["plus_minus"] / grouped["minutes"].replace(0, pd.NA)
    return grouped


@st.cache_data(ttl=_TTL, show_spinner=False)
def clutch_lineups(
    _engine: Engine,
    team_id: str,
    season_id: int,
    last_seconds: int = 300,
    max_margin: int = 5,
    min_seconds: float = 60.0,
    limit: int = 10,
    competition_id: Optional[int] = None,
) -> pd.DataFrame:
    """Quintetos filtrados por VENTANA DE TIEMPO y MARGEN, desde `lineup_stints`.

    La pregunta del encargo ("¿cuál es el mejor quinteto para los últimos
    minutos?") es esta consulta, y hasta la fase 3 no existía: `lineups`
    agrega el partido entero, así que los tramos se fundían al persistir y no
    había forma de recortar los minutos finales (§2.3). Con tramos sí:
    `end_seconds` sitúa el tramo en el partido y `margin_start` dice si el
    partido estaba abierto.

    Args:
        last_seconds: cuánto dura "el final" (300 = últimos 5 minutos del
            tiempo reglamentario).
        max_margin: diferencia máxima en el marcador al abrir el tramo. Es lo
            que separa "los últimos minutos" de "los últimos minutos de un
            partido que ya estaba decidido".
        min_seconds: mínimo de segundos acumulados juntos para aparecer.

    Returns:
        `jugadores, player_ids, seconds, minutes, points_for, points_against,
        plus_minus, stints`, mejor diferencia primero.
    """
    # 2400 s = 40 minutos reglamentarios. Los tramos de prórroga tienen
    # `end_seconds` por encima y entran igual en la ventana: una prórroga es
    # el final del partido por definición.
    threshold = 2400 - last_seconds
    sql = text("""
        SELECT s.id AS stint_id, s.start_seconds, s.end_seconds,
               s.points_for, s.points_against, s.margin_start,
               p.id AS player_id, p.name AS player_name
        FROM lineup_stints s
        JOIN games g ON g.id = s.game_id
        JOIN lineup_stint_players sp ON sp.stint_id = s.id
        JOIN players p ON p.id = sp.player_id
        WHERE s.team_id = :team_id AND g.season_id = :season_id
          AND (:competition_id IS NULL OR g.competition_id = :competition_id)
          AND s.end_seconds >= :threshold
          AND ABS(s.margin_start) <= :max_margin
    """)
    raw = pd.read_sql(
        sql,
        _engine,
        params={
            "team_id": team_id,
            "season_id": season_id,
            "competition_id": competition_id,
            "threshold": threshold,
            "max_margin": max_margin,
        },
    )
    columns = ["jugadores", "player_ids", "seconds", "minutes", "points_for", "points_against", "plus_minus", "stints"]
    if raw.empty:
        return pd.DataFrame(columns=columns)

    raw = raw.sort_values("player_name")
    # Solo cuenta el trozo del tramo que cae DENTRO de la ventana: un tramo
    # que empieza en el minuto 30 y llega al final no son diez minutos de
    # "últimos cinco".
    raw["clipped_seconds"] = raw["end_seconds"] - raw["start_seconds"].clip(lower=threshold)

    per_stint = raw.groupby("stint_id").agg(
        jugadores=("player_name", " · ".join),
        player_ids=("player_id", lambda ids: ",".join(sorted(ids))),
        seconds=("clipped_seconds", "first"),
        points_for=("points_for", "first"),
        points_against=("points_against", "first"),
    )
    combos = per_stint.groupby("jugadores").agg(
        player_ids=("player_ids", "first"),
        seconds=("seconds", "sum"),
        points_for=("points_for", "sum"),
        points_against=("points_against", "sum"),
        stints=("seconds", "size"),
    ).reset_index()
    combos["minutes"] = combos["seconds"] / 60.0
    combos["plus_minus"] = combos["points_for"] - combos["points_against"]
    combos = combos[combos["seconds"] >= min_seconds]
    if combos.empty:
        return pd.DataFrame(columns=columns)
    return combos.sort_values(["plus_minus", "seconds"], ascending=False).head(limit)[columns].reset_index(drop=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_roster_production(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Plantilla de un equipo ordenada por producción en la temporada.

    A diferencia de `queries.roster_cards` (pensada para la galería del
    Baskonia: activos, por dorsal, con foto) esta sirve para responder "quién
    juega en X": incluye a cualquiera con partidos registrados aunque ya no
    figure como activo, y ordena por minutos, que es la lectura de rol.

    OJO (§2.4): `players.team_id` es el equipo ACTUAL del jugador, así que la
    plantilla de una temporada pasada es aproximada. Quien la use debe
    decirlo.
    """
    sql = text("""
        SELECT p.id, p.name, p.number, p.position,
               s.gp, s.min_avg, s.pts_avg, s.reb_avg, s.ast_avg, s.efg_pct
        FROM players p
        LEFT JOIN player_stats_combined s
               ON s.player_id = p.id AND s.season_id = :season_id
        WHERE p.team_id = :team_id
        ORDER BY s.min_avg DESC NULLS LAST, p.number
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_form(_engine: Engine, player_id: str, season_id: int, last_n: int = 5) -> Optional[dict]:
    """Tendencia de un jugador: últimos N partidos frente a su temporada, en z-scores.

    Es el "está en racha" con un número detrás. El z-score se calcula sobre la
    desviación típica de SUS propios partidos de la temporada: un jugador muy
    regular necesita menos diferencia para que la racha signifique algo que
    uno irregular, y una media suelta no distingue esos dos casos.

    Returns:
        `{'gp', 'last_n', 'season': {...}, 'recent': {...}, 'z': {...}}`, o
        `None` si el jugador no tiene partidos suficientes (hacen falta al
        menos dos para que exista desviación típica).
    """
    sql = text("""
        SELECT g.game_date, pgs.minutes, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct
        FROM player_game_stats pgs
        JOIN games g ON g.id = pgs.game_id
        WHERE pgs.player_id = :player_id AND g.season_id = :season_id
        ORDER BY g.game_date
    """)
    log = pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})
    if len(log) < 2:
        return None

    metrics = ["minutes", "pts", "reb", "ast", "efg_pct"]
    recent = log.tail(last_n)
    season_mean = log[metrics].mean()
    season_std = log[metrics].std(ddof=0)
    recent_mean = recent[metrics].mean()
    # `std == 0` (mismo valor en todos los partidos) daría infinito: sin
    # variación no hay racha que medir, el z-score correcto es 0.
    z = ((recent_mean - season_mean) / season_std.replace(0, pd.NA)).fillna(0.0)

    return {
        "gp": int(len(log)),
        "last_n": int(len(recent)),
        "season": {k: round(float(v), 2) for k, v in season_mean.items()},
        "recent": {k: round(float(v), 2) for k, v in recent_mean.items()},
        "z": {k: round(float(v), 2) for k, v in z.items()},
    }


@st.cache_data(ttl=_TTL, show_spinner=False)
def compare_entities(
    _engine: Engine, kind: str, entity_ids: List[str], season_id: int
) -> pd.DataFrame:
    """Varias entidades del mismo tipo sobre las mismas métricas, en una tabla.

    Args:
        kind: `'player'` o `'team'`.
        entity_ids: ids ya resueltos (nunca nombres libres).

    Returns:
        Una fila por entidad con sus medias combinadas de la temporada.
        Las entidades sin datos aparecen igual, con `gp = 0` — que una de las
        dos no tenga partidos ES la respuesta a bastantes comparaciones.
    """
    if not entity_ids:
        return pd.DataFrame()

    placeholders = ", ".join(f":id_{i}" for i in range(len(entity_ids)))
    params = {f"id_{i}": entity_id for i, entity_id in enumerate(entity_ids)}
    params["season_id"] = season_id

    if kind == "player":
        sql = text(f"""
            SELECT p.id, p.name, t.name AS team,
                   COALESCE(s.gp, 0) AS gp, s.min_avg, s.pts_avg, s.reb_avg, s.ast_avg, s.efg_pct
            FROM players p
            JOIN teams t ON t.id = p.team_id
            LEFT JOIN player_stats_combined s ON s.player_id = p.id AND s.season_id = :season_id
            WHERE p.id IN ({placeholders})
        """)
    else:
        sql = text(f"""
            SELECT t.id, t.name, NULL AS team,
                   COALESCE(s.gp, 0) AS gp, s.pace, s.ortg, s.drtg, s.net_rating, s.efg_pct, s.ts_pct
            FROM teams t
            LEFT JOIN team_stats_combined s ON s.team_id = t.id AND s.season_id = :season_id
            WHERE t.id IN ({placeholders})
        """)
    return pd.read_sql(sql, _engine, params=params)
