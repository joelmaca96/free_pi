"""Consultas de solo lectura sobre el esquema de scouting.

Cada función recibe el engine (parámetro `_engine` — el guion bajo le dice a
`st.cache_data` que no intente hashearlo, ver docs de Streamlit) y una fecha
`today` para que el resultado sea determinista y cacheable en vez de depender
de una llamada oculta a `date.today()` dentro de la consulta. Ninguna función
de este módulo escribe: son todas `SELECT`.

Convención de nombres devueltos: `pandas.DataFrame` para resultados
tabulares, `Optional[dict]`/`Optional[str]` para resultados de una sola fila
o un escalar.
"""
import datetime as dt
import math
from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

_TTL = 3600  # la ingesta corre por su cuenta (systemd timer); 1h de caché basta.


def _blank_missing_url(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Sustituye los huecos de `column` por `""` (in-place, devuelve `df`).

    Necesario para cualquier columna `*_logo_url` que se vaya a pintar con
    `st.column_config.ImageColumn`. Verificado en vivo, 2026-08-24 (con un
    mini-repro aislado, tres variantes): `float('nan')`, `pd.NA` (dtype
    `"string"`) y `None` — las TRES pintan literalmente el texto `"None"` en
    la celda en vez de dejarla en blanco (p.ej. "Coviran Granada" sin escudo
    en `list_past_games`); solo `""` (cadena vacía) deja la celda realmente
    en blanco. No es un problema de dtype/nulabilidad de pandas — es cómo
    `ImageColumn` trata cualquier valor ausente en esta versión de Streamlit.
    """
    df[column] = df[column].fillna("")
    return df


@st.cache_data(ttl=_TTL, show_spinner=False)
def get_own_team_id(_engine: Engine) -> str:
    """Id del equipo propio (`teams.is_own_team = 1`) — hoy el Baskonia (`'bas'`).

    No se hardcodea el id en las páginas para que la app siga funcionando si
    algún día cambia (p.ej. otra instancia del mismo proyecto para otro club).
    """
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM teams WHERE is_own_team = 1 LIMIT 1")).fetchone()
    if row is None:
        raise RuntimeError("Ningún equipo tiene is_own_team=1 en `teams` — revisa los datos semilla.")
    return row[0]


@st.cache_data(ttl=_TTL, show_spinner=False)
def get_current_season_id(_engine: Engine) -> int:
    """Id de la temporada más reciente en `seasons`."""
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM seasons ORDER BY id DESC LIMIT 1")).fetchone()
    if row is None:
        raise RuntimeError("La tabla `seasons` está vacía.")
    return row[0]


@st.cache_data(ttl=_TTL, show_spinner=False)
def list_competitions(_engine: Engine) -> pd.DataFrame:
    """Catálogo de competiciones (`id`, `name`), para selectores de filtro."""
    return pd.read_sql(text("SELECT id, name FROM competitions ORDER BY id"), _engine)


@st.cache_data(ttl=_TTL, show_spinner=False)
def list_seasons(_engine: Engine) -> pd.DataFrame:
    """Catálogo de temporadas (`id`, `label`), más reciente primero.

    Selector a nivel de aplicación (ver `Home.py`) — todo lo que se filtra
    por partido (récord, medias, carga de minutos, partidos anteriores)
    respeta la temporada elegida aquí, no solo la más reciente.
    """
    return pd.read_sql(text("SELECT id, label FROM seasons ORDER BY id DESC"), _engine)


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_record(_engine: Engine, team_id: str, season_id: int, today: dt.date) -> pd.DataFrame:
    """Récord (victorias/derrotas) del equipo por competición, partidos ya jugados.

    Returns:
        Columnas `competition`, `wins`, `losses`.
    """
    sql = text("""
        SELECT c.name AS competition,
               SUM(CASE WHEN (g.home_team_id = :team_id AND g.home_score > g.away_score)
                          OR (g.away_team_id = :team_id AND g.away_score > g.home_score)
                        THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN (g.home_team_id = :team_id AND g.home_score < g.away_score)
                          OR (g.away_team_id = :team_id AND g.away_score < g.home_score)
                        THEN 1 ELSE 0 END) AS losses
        FROM games g
        JOIN competitions c ON c.id = g.competition_id
        WHERE (g.home_team_id = :team_id OR g.away_team_id = :team_id)
          AND g.season_id = :season_id
          AND g.game_date <= :today
        GROUP BY c.name
        ORDER BY c.name
    """)
    params = {"team_id": team_id, "season_id": season_id, "today": today.isoformat()}
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def next_matchup(_engine: Engine, season_id: int, today: dt.date) -> Optional[dict]:
    """Próximo rival programado de `season_id` (`upcoming_matchups`), o `None` si no hay ninguno.

    Desde el 2026-08-24, `upcoming_matchups` sí tiene columna de temporada y
    `ingest/acb/pipeline.py::run_upcoming` la puebla con el calendario real
    de ACB (Liga Endesa/Copa del Rey/Supercopa) — ya no es contenido de
    ejemplo (ver historia previa en `local/features/002-ajustes-interfaz/
    00_request.md`). `ingest/euroleague/pipeline.py::run_upcoming` (mismo
    día) cubre también Euroliga, acotado a su propia `competition_id` para
    no pisar el calendario de ACB de la misma temporada — el "próximo
    partido" real puede ser de cualquiera de las dos, la consulta no
    distingue fuente. La página solo la muestra cuando la temporada
    seleccionada es la más reciente.

    Devuelve también `opponent_team_id` (no solo el nombre): lo necesita la
    pantalla "Próximo rival" para pedir todo el scouting de ese equipo con
    las consultas ya genéricas en `team_id` (`team_record`, `list_past_games`,
    `roster_cards`, `season_lineups`...), sin tener que reresolver el equipo
    por nombre — ver `local/features/004-proximo-rival/01_design.md` §3.1.
    """
    sql = text("""
        SELECT um.opponent_team_id, t.name AS opponent, t.logo_url AS opponent_logo_url,
               um.match_date, um.is_home,
               um.predicted_net_rating, um.key_player_note, c.name AS competition
        FROM upcoming_matchups um
        JOIN teams t ON t.id = um.opponent_team_id
        JOIN competitions c ON c.id = um.competition_id
        WHERE um.season_id = :season_id AND um.match_date >= :today
        ORDER BY um.match_date ASC
        LIMIT 1
    """)
    df = pd.read_sql(sql, _engine, params={"season_id": season_id, "today": today.isoformat()})
    return None if df.empty else df.iloc[0].to_dict()


@st.cache_data(ttl=_TTL, show_spinner=False)
def upcoming_matchups_list(_engine: Engine, season_id: int, today: dt.date) -> pd.DataFrame:
    """Calendario completo de partidos aún no jugados de `season_id`, cronológico.

    Mismas filas que `next_matchup` mira para elegir "el próximo", pero sin el
    `LIMIT 1` — para pintar el calendario entero (ver `ingest/acb/pipeline.py::
    run_upcoming`), no solo el siguiente partido.

    Returns:
        `match_date, competition, rival, rival_logo_url, condicion` (`'Local'`/
        `'Visitante'`), cronológico — mezcla ACB y Euroliga si ambas tienen
        calendario cargado para `season_id` (ver `next_matchup`). Vacío si la
        temporada no tiene calendario futuro cargado (p.ej. temporadas pasadas).
    """
    sql = text("""
        SELECT um.match_date, c.name AS competition, t.name AS rival, t.logo_url AS rival_logo_url,
               CASE WHEN um.is_home THEN 'Local' ELSE 'Visitante' END AS condicion
        FROM upcoming_matchups um
        JOIN teams t ON t.id = um.opponent_team_id
        JOIN competitions c ON c.id = um.competition_id
        WHERE um.season_id = :season_id AND um.match_date >= :today
        ORDER BY um.match_date ASC
    """)
    df = pd.read_sql(sql, _engine, params={"season_id": season_id, "today": today.isoformat()})
    return _blank_missing_url(df, "rival_logo_url")


@st.cache_data(ttl=_TTL, show_spinner=False)
def minutes_load(_engine: Engine, team_id: str, season_id: int, n_games: int) -> pd.DataFrame:
    """Minutos por jugador activo en los últimos `n_games` partidos del equipo, dentro de la temporada.

    Formato largo (`player_name`, `game_date`, `minutes`); un jugador sin
    minutos en un partido concreto (no convocado, lesión, DNP) aparece con
    `minutes = NaN` en vez de desaparecer de la fila — la ausencia es la
    información relevante para carga/rotación. La página descarta del todo
    a un jugador si no tiene NINGÚN minuto en toda la ventana (no aporta a
    la lectura de carga reciente), pero eso lo decide la página, no esta
    consulta — aquí se devuelve todo el universo de jugadores activos.
    """
    sql = text("""
        WITH recent_games AS (
            SELECT id, game_date FROM games
            WHERE (home_team_id = :team_id OR away_team_id = :team_id)
              AND season_id = :season_id
            ORDER BY game_date DESC
            LIMIT :n_games
        )
        SELECT p.id AS player_id, p.name AS player_name, rg.game_date, pgs.minutes
        FROM players p
        CROSS JOIN recent_games rg
        LEFT JOIN player_game_stats pgs
               ON pgs.game_id = rg.id AND pgs.player_id = p.id
        WHERE p.team_id = :team_id AND p.active = 1
        ORDER BY p.name, rg.game_date
    """)
    params = {"team_id": team_id, "season_id": season_id, "n_games": n_games}
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def season_lineups(
    _engine: Engine, team_id: str, season_id: int, competition_id: Optional[int] = None, top_n: int = 10
) -> pd.DataFrame:
    """Quintetos más utilizados de la temporada (agregados entre partidos) y su +/- acumulado.

    `lineups` tiene una fila por tramo/quinteto reconstruido (uno por
    partido, o varios dentro del mismo partido si hay sustituciones) — un
    mismo quinteto de 5 jugadores puede repetirse en muchas filas distintas.
    Esta consulta trae esas filas ya resueltas a nombre de jugador y las
    agrega en pandas por la combinación real de jugadores (no por
    `lineup_id`), igual criterio que los récords de temporada de
    `player_game_log` — no merece una vista SQL para una agregación que se
    hace en tres líneas de pandas.

    Args:
        competition_id: si es `None`, agrega todas las competiciones (mismo
            criterio que `player_averages_all`); si no, se queda solo con
            los tramos de partidos de esa competición — el "quinteto más
            usado" puede variar de una a otra (rotación distinta en Copa
            del Rey que en ACB, por ejemplo).

    Returns:
        Los `top_n` quintetos con más minutos jugados juntos en la
        temporada (y competición, si se filtra): `jugadores` (nombres
        separados por " · ", en orden alfabético estable — misma
        combinación siempre da la misma cadena sin importar en qué orden se
        reconstruyó cada vez), `minutes` (suma), `plus_minus` (suma),
        `stints` (en cuántos tramos distintos ha aparecido ese quinteto) y
        `total_combos` (cuántas combinaciones DISTINTAS ha usado el equipo
        en total para ese filtro, no solo las `top_n` de esta tabla — mismo
        valor repetido en todas las filas, para que la página pueda avisar
        de que la tabla es un recorte y no toda la rotación: con muestras
        pequeñas, p.ej. 3 partidos de Copa del Rey con mucha rotación, el
        top 10 puede cubrir bastante menos del total de minutos jugados).
        Vacío si el equipo no tiene quintetos reconstruidos todavía para
        ese filtro.
    """
    sql = text("""
        SELECT l.id AS lineup_id, l.minutes, l.plus_minus, p.name AS player_name
        FROM lineups l
        JOIN games g ON g.id = l.game_id
        JOIN lineup_players lp ON lp.lineup_id = l.id
        JOIN players p ON p.id = lp.player_id
        WHERE g.season_id = :season_id AND p.team_id = :team_id
          AND (:competition_id IS NULL OR g.competition_id = :competition_id)
    """)
    raw = pd.read_sql(
        sql, _engine, params={"season_id": season_id, "team_id": team_id, "competition_id": competition_id}
    )
    if raw.empty:
        return pd.DataFrame(columns=["jugadores", "minutes", "plus_minus", "stints", "total_combos"])

    raw = raw.sort_values("player_name")
    per_lineup = raw.groupby("lineup_id").agg(
        jugadores=("player_name", " · ".join),
        minutes=("minutes", "first"),
        plus_minus=("plus_minus", "first"),
    )
    combos = per_lineup.groupby("jugadores").agg(
        minutes=("minutes", "sum"),
        plus_minus=("plus_minus", "sum"),
        stints=("minutes", "size"),
    )
    return (
        combos.sort_values(["minutes", "plus_minus"], ascending=[False, False])
        .head(top_n)
        .reset_index()
        .assign(total_combos=len(combos))
    )


@st.cache_data(ttl=_TTL, show_spinner=False)
def list_past_games(
    _engine: Engine, team_id: str, season_id: int, today: dt.date, competition_id: Optional[int], limit: int = 40
) -> pd.DataFrame:
    """Partidos ya disputados del equipo en la temporada dada, más recientes primero.

    Returns:
        Columnas `id`, `game_date`, `competition`, `condicion`
        (`'Local'`/`'Visitante'`), `rival`, `rival_logo_url`, `rival_team_id`,
        `pts_favor`, `pts_contra`. `rival_team_id` permite a la página pedir
        boxscore/quintetos del rival con las mismas consultas ya filtrables
        por equipo (`game_boxscore`, `game_lineups`). `rival_logo_url` viene
        de `teams.logo_url` — poblado para los equipos de ACB/Euroliga que
        ya ha visto `ingest/{acb,euroleague}/pipeline.py::run_upcoming`
        (ver docstring de `upcoming_matchups_list`); `None` para el resto,
        la página debe degradar a un badge con iniciales, no a un hueco.
    """
    sql = text("""
        SELECT g.id, g.game_date, c.name AS competition,
               CASE WHEN g.home_team_id = :team_id THEN 'Local' ELSE 'Visitante' END AS condicion,
               CASE WHEN g.home_team_id = :team_id THEN t_away.name ELSE t_home.name END AS rival,
               CASE WHEN g.home_team_id = :team_id THEN t_away.logo_url ELSE t_home.logo_url END AS rival_logo_url,
               CASE WHEN g.home_team_id = :team_id THEN g.away_team_id ELSE g.home_team_id END AS rival_team_id,
               CASE WHEN g.home_team_id = :team_id THEN g.home_score ELSE g.away_score END AS pts_favor,
               CASE WHEN g.home_team_id = :team_id THEN g.away_score ELSE g.home_score END AS pts_contra
        FROM games g
        JOIN teams t_home ON t_home.id = g.home_team_id
        JOIN teams t_away ON t_away.id = g.away_team_id
        JOIN competitions c ON c.id = g.competition_id
        WHERE (g.home_team_id = :team_id OR g.away_team_id = :team_id)
          AND g.season_id = :season_id
          AND g.game_date <= :today
          AND (:competition_id IS NULL OR g.competition_id = :competition_id)
        ORDER BY g.game_date DESC
        LIMIT :limit
    """)
    params = {
        "team_id": team_id,
        "season_id": season_id,
        "today": today.isoformat(),
        "competition_id": competition_id,
        "limit": limit,
    }
    df = pd.read_sql(sql, _engine, params=params)
    return _blank_missing_url(df, "rival_logo_url")


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_quarter_stats(_engine: Engine, game_id: str) -> pd.DataFrame:
    """Puntos a favor/en contra por cuarto y equipo de un partido.

    Puede venir vacío (Euroliga sin `play_by_play_records` — limitación
    conocida, ver `doc/features/ingestor/01_estado.md`); las páginas deben
    tratarlo como sección vacía, no como error. `fouls_for`/`fouls_against`
    (Fase 2) pueden ser `NaN` incluso con puntos presentes: se derivan de
    `play_events`, que no todos los partidos tienen todavía — no es lo mismo
    "0 faltas" que "sin play-by-play tipado".
    """
    sql = text("""
        SELECT gtqs.team_id, t.name AS team_name, gtqs.quarter,
               gtqs.points_for, gtqs.points_against, gtqs.fouls_for, gtqs.fouls_against
        FROM game_team_quarter_stats gtqs
        JOIN teams t ON t.id = gtqs.team_id
        WHERE gtqs.game_id = :game_id
        ORDER BY gtqs.quarter, gtqs.team_id
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_advanced_stats(_engine: Engine, game_id: str) -> pd.DataFrame:
    """Estadísticas avanzadas de un partido, una fila por equipo.

    `ftm`/`fta` (tiros libres convertidos/intentados, en bruto) van aparte de
    un `ft_pct` ya calculado en SQL a propósito: la página necesita poder
    decir "sin datos" cuando `fta` es `NULL` (partido cargado antes de que
    existiera la columna, ver `game_advanced_stats.ftm` en `schema.sql`) en
    vez de mostrar un 0% que sugeriría 0 de 0 intentos. Fase 0: `ast_pct`/
    `stl_pct`/`blk_pct`/`ft_rate`/`ast_to_ratio` ya se cargaban y no
    entraban en este `SELECT` — quick win, sin ingesta nueva. Fase 1:
    boxscore ampliado de equipo (robos/pérdidas/tapones/faltas/rebote of-def/
    +-/PIR), `NULL` = partido ingerido antes de esta fase.
    """
    sql = text("""
        SELECT gas.team_id, t.name AS team_name, gas.ortg, gas.drtg, gas.net_rating,
               gas.efg_pct, gas.ts_pct, gas.tov_pct, gas.orb_pct, gas.ftm, gas.fta,
               gas.ast_pct, gas.stl_pct, gas.blk_pct, gas.ft_rate, gas.ast_to_ratio,
               gas.stl, gas.tov, gas.blk, gas.blk_against, gas.pf, gas.pf_drawn,
               gas.oreb, gas.dreb, gas.plus_minus, gas.pir
        FROM game_advanced_stats gas
        JOIN teams t ON t.id = gas.team_id
        WHERE gas.game_id = :game_id
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_boxscore(_engine: Engine, game_id: str, team_id: Optional[str] = None) -> pd.DataFrame:
    """Boxscore de un partido, opcionalmente filtrado a un equipo.

    Boxscore ampliado (Fase 1): robos/pérdidas/tapones (dados y recibidos)/
    faltas (cometidas y recibidas)/rebote ofensivo-defensivo/+-/PIR/mates —
    `NULL` en cualquiera de ellos = partido ingerido antes de esta fase.
    """
    sql = text("""
        SELECT p.id AS player_id, p.name AS player_name, p.team_id,
               pgs.minutes, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct,
               pgs.stl, pgs.tov, pgs.blk, pgs.blk_against, pgs.pf, pgs.pf_drawn,
               pgs.oreb, pgs.dreb, pgs.plus_minus, pgs.pir, pgs.dunks
        FROM player_game_stats pgs
        JOIN players p ON p.id = pgs.player_id
        WHERE pgs.game_id = :game_id
          AND (:team_id IS NULL OR p.team_id = :team_id)
        ORDER BY pgs.pts DESC
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "team_id": team_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_player_report(_engine: Engine, game_id: str, team_id: str) -> pd.DataFrame:
    """Boxscore ampliado de UN equipo en UN partido + foto y triples, para
    generar el informe "PPT para Paolo" (`app/reports/postgame_ppt.py`).

    Es `game_boxscore` con dos añadidos que ese sí no trae porque nadie más los
    necesita juntos: `ftm`/`fta` (tiros libres, ya en `player_game_stats` pero
    fuera de la SELECT de `game_boxscore`) y `tpm`/`tpa` (triples), que no
    existen como columna en ningún sitio — `shots` no distingue tipo de tiro,
    así que se derivan contando los tiros cuya zona (`court_zones.label`)
    empieza por "Triple" (las cinco zonas de 3 puntos, ver seed de
    `schema.sql`). `NULL`/`NaN` en `tpm`/`tpa` significa que este partido no
    tiene tiros con coordenadas, no que el jugador no lanzara ningún triple.

    Solo jugadores con minutos jugados (se descartan los convocados que no
    saltaron a pista — no hay nada destacado que sacar de una fila en blanco).
    """
    sql = text("""
        SELECT p.id AS player_id, p.name AS player_name, p.number, p.position,
               p.photo_url, p.photo_local_path,
               pgs.minutes, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct,
               pgs.stl, pgs.tov, pgs.blk, pgs.blk_against, pgs.pf, pgs.pf_drawn,
               pgs.oreb, pgs.dreb, pgs.plus_minus, pgs.pir, pgs.dunks,
               pgs.ftm, pgs.fta, t3.tpm, t3.tpa
        FROM player_game_stats pgs
        JOIN players p ON p.id = pgs.player_id
        LEFT JOIN (
            SELECT s.player_id, SUM(s.made) AS tpm, COUNT(*) AS tpa
            FROM shots s
            JOIN court_zones cz ON cz.id = s.zone_id
            WHERE s.game_id = :game_id AND cz.label LIKE 'Triple%'
            GROUP BY s.player_id
        ) t3 ON t3.player_id = p.id
        WHERE pgs.game_id = :game_id AND p.team_id = :team_id
          AND pgs.minutes IS NOT NULL AND pgs.minutes > 0
        ORDER BY pgs.pts DESC
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "team_id": team_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_metadata(_engine: Engine, game_id: str) -> Optional[dict]:
    """Metadata de un partido (Fase 3): árbitros, asistencia, pabellón, entrenadores.

    Returns:
        `arena, attendance, referees, home_coach, away_coach`, o `None` si el
        partido no existe. Cualquier campo puede ser `None` — partido
        ingerido antes de esta fase, o la fuente no lo dio para ese partido
        concreto (p.ej. asistencia de un amistoso).
    """
    sql = text("SELECT arena, attendance, referees, home_coach, away_coach FROM games WHERE id = :game_id")
    df = pd.read_sql(sql, _engine, params={"game_id": game_id})
    return None if df.empty else df.iloc[0].to_dict()


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_shots(_engine: Engine, game_id: str, team_id: Optional[str] = None) -> pd.DataFrame:
    """Tiros de un partido (tiros libres excluidos en origen).

    `located` (ver `shots.located` en `schema.sql`) distingue las coordenadas
    medidas de las inferidas del tipo de tiro — `COALESCE` a 1 para las filas
    anteriores a esa columna. `shot_chart` lo usa para pintarlas aparte.
    """
    sql = text("""
        SELECT s.pos_x, s.pos_y, s.made, COALESCE(s.located, 1) AS located,
               p.name AS player_name, p.team_id
        FROM shots s
        JOIN players p ON p.id = s.player_id
        WHERE s.game_id = :game_id
          AND (:team_id IS NULL OR p.team_id = :team_id)
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "team_id": team_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def court_zones(_engine: Engine) -> pd.DataFrame:
    """Zonas de cancha (`court_zones`) para dibujar el fondo del mapa de tiros."""
    return pd.read_sql(text("SELECT id, label, x_min, x_max, y_min, y_max FROM court_zones"), _engine)


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_lineups(_engine: Engine, game_id: str, team_id: Optional[str] = None) -> pd.DataFrame:
    """Quintetos usados en un partido, con minutos/+-, uno por fila (jugadores concatenados)."""
    sql = text("""
        SELECT l.id AS lineup_id, l.minutes, l.plus_minus,
               GROUP_CONCAT(p.name, ' · ') AS jugadores
        FROM lineups l
        JOIN lineup_players lp ON lp.lineup_id = l.id
        JOIN players p ON p.id = lp.player_id
        WHERE l.game_id = :game_id
          AND (:team_id IS NULL OR p.team_id = :team_id)
        GROUP BY l.id, l.minutes, l.plus_minus
        ORDER BY l.minutes DESC
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "team_id": team_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_logo_url(_engine: Engine, team_id: str) -> Optional[str]:
    """Escudo de un equipo (`teams.logo_url`), o `None` si no está poblado.

    Columna nueva y aún sin ninguna fuente que la escriba (ver
    `local/features/003-vista-plantilla/01_design.md` §9) — hoy siempre
    devuelve `None` para todos los equipos, incluido el Baskonia. Las
    páginas deben degradar a un badge con iniciales, no a un hueco vacío
    (`components/avatar.py::team_crest_html`).
    """
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT logo_url FROM teams WHERE id = :team_id"), {"team_id": team_id}).fetchone()
    return row[0] if row else None


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_name(_engine: Engine, team_id: str) -> Optional[str]:
    """Nombre de un equipo por id, o `None` si no existe.

    Trivial pero necesaria: varias pantallas ya tenían el id y solo querían
    el nombre para un título o un prompt, y estaban abriendo una conexión a
    mano cada una (ver `app/pages/asistente.py`).
    """
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT name FROM teams WHERE id = :team_id"), {"team_id": team_id}).fetchone()
    return row[0] if row else None


@st.cache_data(ttl=_TTL, show_spinner=False)
def roster_cards(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Plantilla activa para la galería de tarjetas de "Plantilla".

    Igual que `player_scouting_season`, pero resuelto por jugador dentro de
    una sola consulta en vez de una llamada por tarjeta: cada jugador saca su
    `pts_avg` de `season_id`, o si ahí no tiene partidos todavía (fichaje
    reciente, o inicio de temporada antes de su debut) de su última temporada
    con datos hasta `season_id` — nunca de una posterior. La tarjeta antes se
    quedaba en "Sin partidos todavía" en ese caso aunque el jugador sí tuviera
    historial; ver `player_scouting_season` para el caso real que motiva esta
    caída, aquí aplicada también a la vista de galería.

    Returns:
        Una fila por jugador activo, orden por dorsal: `id, name, number,
        position, photo_url, photo_local_path, pts_avg, pir_avg,
        stats_season_id, stats_season_label`. `pts_avg`/`pir_avg`/
        `stats_season_id`/`stats_season_label` son `NaN`/`None` si el
        jugador no tiene partidos registrados en ninguna temporada hasta
        `season_id` (`pir_avg` también si esta BD no tiene boxscore
        ampliado todavía, Fase 1). `stats_season_id != season_id` indica que
        los promedios son de una temporada anterior (fallback), para que la
        tarjeta pueda avisarlo. `photo_local_path` es la copia descargada a
        disco por `ingest/baskonia_web` — `components/avatar.py` la
        prefiere sobre `photo_url` (hotlink remoto), ver su docstring.
    """
    sql = text("""
        SELECT p.id, p.name, p.number, p.position, p.photo_url, p.photo_local_path,
               s.pts_avg, s.pir_avg, s.season_id AS stats_season_id, se.label AS stats_season_label
        FROM players p
        LEFT JOIN (
            SELECT sc.player_id, sc.pts_avg, sc.pir_avg, sc.season_id
            FROM player_stats_combined sc
            WHERE sc.season_id = (
                SELECT MAX(sc2.season_id)
                FROM player_stats_combined sc2
                WHERE sc2.player_id = sc.player_id AND sc2.season_id <= :season_id
            )
        ) s ON s.player_id = p.id
        LEFT JOIN seasons se ON se.id = s.season_id
        WHERE p.team_id = :team_id AND p.active = 1
        ORDER BY p.number
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_bio(_engine: Engine, player_id: str) -> Optional[dict]:
    """Datos de bio de un jugador para la cabecera del detalle.

    Returns:
        `name, number, position, photo_url, photo_local_path, birth_date,
        nationality, height_cm`, o `None` si el id no existe. Ver
        `roster_cards` para qué es `photo_local_path`.
    """
    sql = text("""
        SELECT name, number, position, photo_url, photo_local_path, birth_date, nationality, height_cm
        FROM players
        WHERE id = :player_id
    """)
    df = pd.read_sql(sql, _engine, params={"player_id": player_id})
    return None if df.empty else df.iloc[0].to_dict()


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_averages_all(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Medias combinadas + por competición de un jugador, en una sola tabla.

    Returns:
        Una fila por competición jugada más la combinada (`competition =
        'Combinado'`, siempre primera si existe): `competition, gp, min_avg,
        pts_avg, reb_avg, ast_avg, efg_pct, gp_ft, ft_pct`. `ft_pct` puede ser
        `NaN` con `gp` > 0 — `gp_ft` cuenta solo los partidos con `ftm`/`fta`
        cargados (columnas añadidas 2026-08-24, `NULL` en partidos ingeridos
        antes; ver `player_game_stats.ftm` en `schema.sql`), así que `gp_ft <
        gp` es cobertura parcial, no "no tira libres". Igual criterio para
        `gp_box_extras` (Fase 1: `stl_avg`..`pir_avg`) y `gp_dunks`/`dunks`
        (`dunks` es ACB-only, siempre `NULL`/0 en un jugador que solo ha
        jugado Euroliga). Vacío si el jugador no tiene partidos registrados
        todavía.
    """
    sql = text("""
        SELECT 'Combinado' AS competition, gp, min_avg, pts_avg, reb_avg, ast_avg, efg_pct, gp_ft, ft_pct,
               gp_box_extras, stl_avg, tov_avg, blk_avg, blk_against_avg, pf_avg, pf_drawn_avg,
               oreb_avg, dreb_avg, plus_minus_avg, pir_avg, gp_dunks, dunks
        FROM player_stats_combined
        WHERE player_id = :player_id AND season_id = :season_id
        UNION ALL
        SELECT c.name AS competition, s.gp, s.min_avg, s.pts_avg, s.reb_avg, s.ast_avg, s.efg_pct,
               s.gp_ft, s.ft_pct,
               s.gp_box_extras, s.stl_avg, s.tov_avg, s.blk_avg, s.blk_against_avg, s.pf_avg, s.pf_drawn_avg,
               s.oreb_avg, s.dreb_avg, s.plus_minus_avg, s.pir_avg, s.gp_dunks, s.dunks
        FROM player_stats_by_competition s
        JOIN competitions c ON c.id = s.competition_id
        WHERE s.player_id = :player_id AND s.season_id = :season_id
    """)
    df = pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})
    # SQLite no admite una expresión CASE sobre el alias de una columna en el
    # ORDER BY de una consulta compuesta (UNION ALL) — "Combinado" primero se
    # resuelve en pandas en vez de forzarlo en SQL.
    is_combined = (df["competition"] != "Combinado").astype(int)
    return df.assign(_sort=is_combined).sort_values(["_sort", "competition"]).drop(columns="_sort").reset_index(drop=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_game_log(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Boxscore partido a partido de un jugador en la temporada, cronológico.

    Returns:
        `game_date, competition, rival, rival_logo_url, condicion, minutes,
        pts, reb, ast, efg_pct, stl, blk` (Fase 1: `stl`/`blk` pueden ser
        `NULL`, partido ingerido antes de esa fase). `rival_logo_url` viene
        de `teams.logo_url` — poblado para los rivales que ya ha visto
        `run_upcoming` de ACB o Euroliga (ver `list_past_games`), `NULL`
        para el resto; la página debe pintarlo con
        `st.column_config.ImageColumn` y dejar la celda en blanco cuando
        falte, no como error.
    """
    sql = text("""
        SELECT g.game_date, c.name AS competition,
               CASE WHEN g.home_team_id = p.team_id THEN t_away.name ELSE t_home.name END AS rival,
               CASE WHEN g.home_team_id = p.team_id THEN t_away.logo_url ELSE t_home.logo_url END AS rival_logo_url,
               CASE WHEN g.home_team_id = p.team_id THEN 'Local' ELSE 'Visitante' END AS condicion,
               pgs.minutes, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct, pgs.stl, pgs.blk
        FROM player_game_stats pgs
        JOIN players p ON p.id = pgs.player_id
        JOIN games g ON g.id = pgs.game_id
        JOIN teams t_home ON t_home.id = g.home_team_id
        JOIN teams t_away ON t_away.id = g.away_team_id
        JOIN competitions c ON c.id = g.competition_id
        WHERE pgs.player_id = :player_id AND g.season_id = :season_id
        ORDER BY g.game_date
    """)
    df = pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})
    return _blank_missing_url(df, "rival_logo_url")


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_shots_season(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Tiros con coordenadas reales de un jugador en toda la temporada.

    Returns:
        `pos_x, pos_y, made, located, player_name` — mismas columnas que
        `game_shots` para reutilizar `components/court.py::shot_chart` tal cual.
    """
    sql = text("""
        SELECT s.pos_x, s.pos_y, s.made, COALESCE(s.located, 1) AS located,
               p.name AS player_name
        FROM shots s
        JOIN players p ON p.id = s.player_id
        JOIN games g ON g.id = s.game_id
        WHERE s.player_id = :player_id AND g.season_id = :season_id
    """)
    return pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_key_events(_engine: Engine, game_id: str) -> pd.DataFrame:
    """Eventos clave de un partido (hoy con cobertura real escasa, ver diseño §4)."""
    sql = text("""
        SELECT ke.quarter, ke.game_clock, ke.label, t.name AS team_name
        FROM key_events ke
        JOIN teams t ON t.id = ke.team_id
        WHERE ke.game_id = :game_id
        ORDER BY ke.quarter, ke.game_clock DESC
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id})


# ---------------------------------------------------------------------------
# Scouting a nivel de EQUIPO — pantalla "Próximo rival"
#
# Ninguna de estas consultas filtra por competición ni por fuente: trabajan
# sobre `team_id` puro, así que dan lo mismo de completo para un rival de
# Liga Endesa/Copa del Rey/Supercopa (`ingest/acb`) que de Euroliga
# (`ingest/euroleague`) — ver `local/features/004-proximo-rival/01_design.md`
# §10. La competición es una columna más del resultado donde aporta
# (`team_advanced_profile`, `head_to_head`), no un filtro.
# ---------------------------------------------------------------------------


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_scouting_season(_engine: Engine, team_id: str, preferred_season_id: int) -> Optional[dict]:
    """Temporada de la que sacar el scouting de `team_id`: la pedida, o la última con datos.

    Existe por un caso REAL, no hipotético (verificado contra
    `data/baskonia.db` el 2026-08-24): el próximo partido del Baskonia era
    Olympiacos el 24-sep-2026, de la temporada 2026-2027 — que tenía 72
    partidos en `upcoming_matchups` y CERO en `games`, porque aún no había
    empezado. Todas las consultas de scouting acotadas a esa temporada
    (récord, avanzadas, cuartos, tiros, quintetos, medias del roster)
    devolvían vacío, así que la pantalla "Próximo rival" salía en blanco
    justo cuando más falta hace: preparando la primera jornada. Los datos
    existían — 43 partidos de Olympiacos en 2025-2026 —, solo estaban en la
    temporada anterior.

    Preparar un partido con lo último que jugó el rival es exactamente lo
    que se hace en pretemporada, así que la degradación correcta no es
    mostrar la página vacía sino caer a esa temporada anterior **diciéndolo**
    (`is_fallback`), nunca presentando dato viejo como si fuera del año en
    curso.

    Args:
        team_id: equipo del que se quiere el scouting.
        preferred_season_id: temporada seleccionada en la app. Nunca se cae
            hacia ADELANTE (`season_id <= preferred`): si se está mirando una
            temporada pasada, datos de una posterior no serían una
            aproximación razonable, serían otra cosa.

    Returns:
        `{"season_id": int, "label": str, "is_fallback": bool}` —
        `is_fallback` es `True` cuando la temporada devuelta no es la pedida.
        `None` si el equipo no tiene partidos en ninguna temporada hasta la
        pedida (rival recién ascendido, o BD sin histórico todavía).
    """
    sql = text("""
        SELECT g.season_id, s.label
        FROM games g
        JOIN seasons s ON s.id = g.season_id
        WHERE (g.home_team_id = :team_id OR g.away_team_id = :team_id)
          AND g.season_id <= :preferred_season_id
        GROUP BY g.season_id, s.label
        ORDER BY g.season_id DESC
        LIMIT 1
    """)
    params = {"team_id": team_id, "preferred_season_id": preferred_season_id}
    df = pd.read_sql(sql, _engine, params=params)
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "season_id": int(row["season_id"]),
        "label": row["label"],
        "is_fallback": int(row["season_id"]) != preferred_season_id,
    }


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_scouting_season(_engine: Engine, player_id: str, preferred_season_id: int) -> Optional[dict]:
    """Temporada de la que sacar el scouting de `player_id`: la pedida, o la última con datos.

    Mismo caso que `team_scouting_season` pero a nivel de jugador: un fichaje
    de esta pretemporada, o cualquiera al que se le pregunte por su temporada
    en curso antes de que juegue su primer partido, tiene cero filas en
    `player_game_stats` para `preferred_season_id` aunque sí las tenga en la
    anterior (con su equipo de entonces). Sin este fallback, el asistente
    respondía "sin datos" para un jugador que sí tiene historial — sonaba a
    fallo de la base de datos, no a que la temporada aún no ha empezado para
    él.

    Args:
        player_id: jugador del que se quiere el scouting.
        preferred_season_id: temporada pedida. Nunca se cae hacia ADELANTE
            (`season_id <= preferred`), mismo motivo que en `team_scouting_season`.

    Returns:
        `{"season_id": int, "label": str, "is_fallback": bool}`, o `None` si
        el jugador no tiene partidos en ninguna temporada hasta la pedida.
    """
    sql = text("""
        SELECT g.season_id, s.label
        FROM player_game_stats pgs
        JOIN games g ON g.id = pgs.game_id
        JOIN seasons s ON s.id = g.season_id
        WHERE pgs.player_id = :player_id
          AND g.season_id <= :preferred_season_id
        GROUP BY g.season_id, s.label
        ORDER BY g.season_id DESC
        LIMIT 1
    """)
    params = {"player_id": player_id, "preferred_season_id": preferred_season_id}
    df = pd.read_sql(sql, _engine, params=params)
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "season_id": int(row["season_id"]),
        "label": row["label"],
        "is_fallback": int(row["season_id"]) != preferred_season_id,
    }


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_advanced_profile(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Perfil avanzado de un equipo: combinado + una fila por competición jugada.

    Equivalente de equipo a `player_averages_all` (mismo patrón `UNION ALL` +
    "Combinado" siempre primero, resuelto en pandas por la misma limitación
    de SQLite con `ORDER BY` sobre el alias de una consulta compuesta).

    Returns:
        `competition, gp, pace, net_rating, ortg, drtg, efg_pct, ts_pct,
        gp_ft, ft_pct`. `ft_pct` (acierto de tiro libre, ponderado por
        volumen — `100*SUM(ftm)/SUM(fta)`, ver la vista) puede ser `NaN` con
        `gp` > 0: `gp_ft` cuenta solo los partidos con `ftm`/`fta` cargados
        (columnas añadidas 2026-08-24, `NULL` en partidos ingeridos antes;
        ver `game_advanced_stats.ftm` en `schema.sql`), así que `gp_ft <
        gp` es "cobertura parcial", no "el equipo no tira libres". Vacío si
        el equipo no tiene ningún partido con `game_advanced_stats` en esa
        temporada.

    OJO al comparar `ortg`/`drtg`/`pace` entre competiciones: en ACB son el
    dato OFICIAL de acb.com y en Euroliga una estimación propia (fórmula
    Dean Oliver, no hay endpoint equivalente en esa fuente) — ver
    `doc/features/ingestor/01_estado.md` §2.2/§2.3. La página lo advierte por
    pestaña; esta consulta devuelve ambos tal cual, sin normalizar nada.

    Fase 0: `ast_pct`/`stl_pct`/`blk_pct`/`ft_rate`/`ast_to_ratio` (tasas de
    equipo, ya cargadas, quick win sin ingesta nueva). Fase 1: boxscore
    ampliado (`stl_avg`..`pir_avg`), propio Y concedido (`opp_*` — cuántos
    robos/tapones/pérdidas/faltas registra el RIVAL en esos mismos partidos).
    """
    sql = text("""
        SELECT 'Combinado' AS competition, gp, pace, net_rating, ortg, drtg, efg_pct, ts_pct, gp_ft, ft_pct,
               ast_pct, stl_pct, blk_pct, ft_rate, ast_to_ratio,
               gp_box_extras, stl_avg, tov_avg, blk_avg, blk_against_avg, pf_avg, pf_drawn_avg,
               oreb_avg, dreb_avg, pir_avg, opp_stl_avg, opp_tov_avg, opp_blk_avg, opp_pf_avg
        FROM team_stats_combined
        WHERE team_id = :team_id AND season_id = :season_id
        UNION ALL
        SELECT c.name AS competition, s.gp, s.pace, s.net_rating, s.ortg, s.drtg, s.efg_pct, s.ts_pct,
               s.gp_ft, s.ft_pct,
               s.ast_pct, s.stl_pct, s.blk_pct, s.ft_rate, s.ast_to_ratio,
               s.gp_box_extras, s.stl_avg, s.tov_avg, s.blk_avg, s.blk_against_avg, s.pf_avg, s.pf_drawn_avg,
               s.oreb_avg, s.dreb_avg, s.pir_avg, s.opp_stl_avg, s.opp_tov_avg, s.opp_blk_avg, s.opp_pf_avg
        FROM team_stats_by_competition s
        JOIN competitions c ON c.id = s.competition_id
        WHERE s.team_id = :team_id AND s.season_id = :season_id
    """)
    df = pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})
    if df.empty:
        return df
    is_combined = (df["competition"] != "Combinado").astype(int)
    return (
        df.assign(_sort=is_combined)
        .sort_values(["_sort", "competition"])
        .drop(columns="_sort")
        .reset_index(drop=True)
    )


@st.cache_data(ttl=_TTL, show_spinner=False)
def head_to_head(_engine: Engine, team_a: str, team_b: str, limit: int = 12) -> pd.DataFrame:
    """Enfrentamientos directos entre dos equipos, más recientes primero.

    Deliberadamente SIN filtro de temporada ni de competición: para preparar
    un partido, un cruce de hace dos temporadas o uno de Copa del Rey/
    Euroliga informan igual que uno de Liga Endesa de este año. Por eso
    devuelve `season_label` — para que la tabla distinga de un vistazo un
    cruce reciente de uno viejo, que sin la temporada delante se confunden.

    Args:
        team_a: equipo desde cuya perspectiva se leen `condicion`/`pts_favor`/
            `pts_contra` (la página pasa aquí el equipo propio).
        team_b: el otro equipo (el rival).

    Returns:
        `game_date, season_label, competition, condicion, pts_favor,
        pts_contra` — mismas columnas que `list_past_games` (menos `rival`,
        que aquí es constante) más `season_label`, para poder reutilizar el
        mismo `column_config`. Vacío si nunca se han enfrentado en los datos
        cargados.
    """
    sql = text("""
        SELECT g.game_date, s.label AS season_label, c.name AS competition,
               CASE WHEN g.home_team_id = :team_a THEN 'Local' ELSE 'Visitante' END AS condicion,
               CASE WHEN g.home_team_id = :team_a THEN g.home_score ELSE g.away_score END AS pts_favor,
               CASE WHEN g.home_team_id = :team_a THEN g.away_score ELSE g.home_score END AS pts_contra
        FROM games g
        JOIN seasons s ON s.id = g.season_id
        JOIN competitions c ON c.id = g.competition_id
        WHERE (g.home_team_id = :team_a AND g.away_team_id = :team_b)
           OR (g.home_team_id = :team_b AND g.away_team_id = :team_a)
        ORDER BY g.game_date DESC
        LIMIT :limit
    """)
    params = {"team_a": team_a, "team_b": team_b, "limit": limit}
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_quarter_profile(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Puntos medios a favor/en contra por cuarto de un equipo en la temporada.

    Agregado de temporada de lo que `game_quarter_stats` da para un partido
    suelto — para leer si un equipo arranca fuerte, se hunde en el tercero o
    aguanta los finales.

    Returns:
        `quarter, avg_points_for, avg_points_against, gp` (partidos con dato
        de ESE cuarto, para poder avisar de muestra pequeña — mismo criterio
        que `total_combos` en `season_lineups`). Vacío si el equipo no tiene
        parciales por cuarto registrados en esa temporada.
    """
    sql = text("""
        SELECT gtqs.quarter,
               AVG(gtqs.points_for)     AS avg_points_for,
               AVG(gtqs.points_against) AS avg_points_against,
               COUNT(*)                 AS gp
        FROM game_team_quarter_stats gtqs
        JOIN games g ON g.id = gtqs.game_id
        WHERE gtqs.team_id = :team_id AND g.season_id = :season_id
        GROUP BY gtqs.quarter
        ORDER BY gtqs.quarter
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_foul_quarter_profile(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Faltas medias cometidas/recibidas por cuarto de un equipo (Fase 2).

    Clon exacto de `team_quarter_profile` pero sobre `fouls_for`/
    `fouls_against` en vez de `points_for`/`points_against` — mismo patrón,
    misma tabla origen. `gp` cuenta solo los cuartos con faltas derivadas de
    `play_events` (Fase 2): un partido sin play-by-play tipado tiene
    `points_for`/`points_against` pero NO `fouls_for`/`fouls_against`, así
    que su `gp` puede ser menor que el de `team_quarter_profile` para el
    mismo equipo/temporada.

    Returns:
        `quarter, avg_fouls_for, avg_fouls_against, gp`. Vacío si el equipo
        no tiene faltas por cuarto derivadas en esa temporada.
    """
    sql = text("""
        SELECT gtqs.quarter,
               AVG(gtqs.fouls_for)     AS avg_fouls_for,
               AVG(gtqs.fouls_against) AS avg_fouls_against,
               COUNT(gtqs.fouls_for)   AS gp
        FROM game_team_quarter_stats gtqs
        JOIN games g ON g.id = gtqs.game_id
        WHERE gtqs.team_id = :team_id AND g.season_id = :season_id
        GROUP BY gtqs.quarter
        HAVING COUNT(gtqs.fouls_for) > 0
        ORDER BY gtqs.quarter
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_shots_season(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Tiros con coordenadas de TODOS los jugadores de un equipo en la temporada.

    Variante de `player_shots_season` sin filtro de jugador.

    Returns:
        `pos_x, pos_y, made, located, player_name` — mismas columnas que
        `game_shots`/`player_shots_season`, para reutilizar
        `components/court.py::shot_chart` sin tocarlo.

    Atribuye cada tiro al equipo ACTUAL del jugador (`players.team_id`),
    igual que ya hace `game_shots` — un jugador que cambió de club a mitad de
    temporada arrastra sus tiros anteriores al equipo nuevo. El esquema no
    guarda a qué equipo pertenecía en cada partido (`player_game_stats` no
    tiene `team_id`), así que no es algo que esta consulta pueda arreglar por
    su cuenta; se documenta en vez de disimularlo.
    """
    sql = text("""
        SELECT s.pos_x, s.pos_y, s.made, COALESCE(s.located, 1) AS located,
               p.name AS player_name
        FROM shots s
        JOIN players p ON p.id = s.player_id
        JOIN games g ON g.id = s.game_id
        WHERE p.team_id = :team_id AND g.season_id = :season_id
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_zone_profile(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Acierto y volumen de tiro por zona de cancha de un equipo en la temporada.

    Acompaña en números al mapa de puntos de `team_shots_season`: la nube de
    tiros se lee bien para densidad y patrón, pero un porcentaje exacto por
    zona no se estima a ojo.

    El `fg_pct` de temporada se pondera por volumen
    (`SUM(fg_pct*volume)/SUM(volume)`), no es la media simple de los
    porcentajes de cada partido — un 100% de un único tiro en un partido no
    puede pesar lo mismo que un 45% de veinte tiros en otro.

    Returns:
        `zone_label, fg_pct, volume, made`, de más a menos volumen. `made`
        (para poder escribir "aciertos/intentos", no solo el %) se
        REDERIVA de `fg_pct*volume` porque `game_zone_stats` no guarda los
        aciertos en bruto por fila (solo el `fg_pct` ya redondeado a 1
        decimal que deja `ingest/common/loader.py`) — con `ROUND()` queda a
        lo sumo a una fracción de tiro de distancia del entero real, de
        sobra para mostrarlo, no para volver a hacer cuentas con él. Vacío
        si el equipo no tiene tiros con zona registrados en esa temporada.
    """
    sql = text("""
        SELECT cz.label                                   AS zone_label,
               SUM(gzs.fg_pct * gzs.volume) / SUM(gzs.volume) AS fg_pct,
               SUM(gzs.volume)                            AS volume,
               ROUND(SUM(gzs.fg_pct / 100.0 * gzs.volume)) AS made
        FROM game_zone_stats gzs
        JOIN court_zones cz ON cz.id = gzs.zone_id
        JOIN games g ON g.id = gzs.game_id
        WHERE gzs.team_id = :team_id AND g.season_id = :season_id
        GROUP BY cz.label
        HAVING SUM(gzs.volume) > 0
        ORDER BY volume DESC
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_zone_profile(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Acierto y volumen de tiro por zona de cancha de UN jugador en la temporada.

    Equivalente de jugador a `team_zone_profile`, pero sin tabla origen
    equivalente que ofrecer: `game_zone_stats` es un agregado por EQUIPO (la
    fuente no lo desglosa por jugador), así que aquí se calcula directamente
    de `shots.zone_id` — el mismo campo que ya resuelve `ingest/common/
    zones.py` tiro a tiro y que `team_zone_profile` no usa por tener ya el
    agregado oficial a mano. Al ser un `GROUP BY` sobre filas individuales,
    `fg_pct` sale automáticamente ponderado por volumen, sin necesitar el
    `SUM(fg_pct*volume)/SUM(volume)` de la versión de equipo.

    Returns:
        `zone_label, fg_pct, volume, made`, de más a menos volumen. `made` es
        exacto (viene de sumar `shots.made` fila a fila, sin el redondeo que
        hace falta en `team_zone_profile`). Vacío si el jugador no tiene
        tiros con zona registrados en esa temporada.
    """
    sql = text("""
        SELECT cz.label                        AS zone_label,
               100.0 * SUM(s.made) / COUNT(*)   AS fg_pct,
               COUNT(*)                         AS volume,
               SUM(s.made)                      AS made
        FROM shots s
        JOIN court_zones cz ON cz.id = s.zone_id
        JOIN games g ON g.id = s.game_id
        WHERE s.player_id = :player_id AND g.season_id = :season_id
        GROUP BY cz.label
        ORDER BY volume DESC
    """)
    return pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})


# ---------------------------------------------------------------------------
# Rotaciones y parciales — pestaña "Rotaciones" de "Partidos anteriores"
# (`doc/features/propuestas/01_rotaciones_y_parciales.md`).
#
# Las tres piezas que la pantalla junta —quién estaba en pista, cómo iba el
# marcador y qué pasó en ese segundo— ya están cargadas y **en la misma escala
# de tiempo**: `lineup_stints.start_seconds`/`end_seconds` y
# `play_events.seconds` son los dos segundos desde el inicio del partido
# (`ingest/common/game_clock.py::game_clock_to_seconds`), así que cruzarlas es
# un `BETWEEN` y no una conversión con supuestos.
#
# El marcador NO sale de `score_progression`: según `schema.sql` esa tabla es
# hoy una interpolación sintética entre 0 y el resultado final, o sea una
# recta que no dice nada de cuándo se decidió el partido. El marcador real es
# el que viaja en cada fila del play-by-play (`play_events.home_score`/
# `away_score`), y se trata como ESCALERA: entre dos eventos el marcador no
# "sube un poco", se queda quieto hasta el siguiente.
# ---------------------------------------------------------------------------

#: Duración del tiempo reglamentario y de cada prórroga, en segundos. Se usan
#: para saber dónde ACABA el partido: el último evento del play-by-play no es
#: el final del partido (ver `_score_steps`).
_REGULATION_SECONDS = 2400.0
_OVERTIME_SECONDS = 300.0


def game_end_seconds(last_seconds: float) -> float:
    """Segundo en el que acaba el partido, dado el último instante con dato.

    Redondea hacia arriba al final del tiempo reglamentario o de la prórroga
    en curso: 2384 → 2400 (partido normal), 2694 → 2700 (una prórroga). Es lo
    que permite que el eje del gráfico llegue hasta el final y que la escalera
    del marcador no se corte quince segundos antes.
    """
    if last_seconds <= _REGULATION_SECONDS:
        return _REGULATION_SECONDS
    overtimes = math.ceil((last_seconds - _REGULATION_SECONDS) / _OVERTIME_SECONDS)
    return _REGULATION_SECONDS + _OVERTIME_SECONDS * overtimes


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_stints(_engine: Engine, game_id: str, team_id: str) -> pd.DataFrame:
    """Tramos de quinteto de un equipo en un partido, una fila por (tramo, jugador).

    No se agrega por combinación de cinco a propósito —a diferencia de
    `game_lineups`—: el timeline de rotaciones necesita cada tramo situado en
    el reloj, que es justo lo que `lineups` funde al persistir (§2.3 del
    diseño del asistente).

    Returns:
        `stint_id, start_seconds, end_seconds, points_for, points_against,
        margin_start, player_id, player_name`, en orden de aparición. Vacío
        si el partido se ingirió antes de la fase 3 (sin tramos).
    """
    sql = text("""
        SELECT s.id AS stint_id, s.start_seconds, s.end_seconds,
               s.points_for, s.points_against, s.margin_start,
               p.id AS player_id, p.name AS player_name
        FROM lineup_stints s
        JOIN lineup_stint_players sp ON sp.stint_id = s.id
        JOIN players p ON p.id = sp.player_id
        WHERE s.game_id = :game_id AND s.team_id = :team_id
        ORDER BY s.start_seconds, p.name
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "team_id": team_id})


def _score_steps(engine: Engine, game_id: str, team_id: Optional[str]) -> pd.DataFrame:
    """Implementación sin cachear de `game_score_steps` (ver su docstring).

    Vive aparte para que `game_runs` la pueda reutilizar sin llamar a otra
    función cacheada desde dentro de una función cacheada: encadenar dos
    cachés de `st.cache_data` complica la invalidación sin ganar nada aquí
    (la consulta es una sola lectura de un partido).
    """
    columns = ["seconds", "quarter", "game_clock", "score_for", "score_against", "margin"]

    header = pd.read_sql(
        text("SELECT home_team_id, home_score, away_score FROM games WHERE id = :game_id"),
        engine,
        params={"game_id": game_id},
    )
    if header.empty:
        return pd.DataFrame(columns=columns)

    # Un mismo segundo trae varios eventos (un robo y la pérdida que lo
    # acompaña son dos filas con el mismo reloj): la escalera quiere UN
    # escalón por segundo. `MAX` sobre el marcador es exacto porque el
    # marcador solo sube, y `quarter`/`game_clock` son función del segundo
    # (la ingesta deriva `seconds` de ellos), así que agregarlos no mezcla
    # valores distintos.
    sql = text("""
        SELECT pe.seconds,
               MIN(pe.quarter)     AS quarter,
               MIN(pe.game_clock)  AS game_clock,
               MAX(pe.home_score)  AS home_score,
               MAX(pe.away_score)  AS away_score
        FROM play_events pe
        WHERE pe.game_id = :game_id
        GROUP BY pe.seconds
        ORDER BY pe.seconds
    """)
    steps = pd.read_sql(sql, engine, params={"game_id": game_id})
    if steps.empty:
        return pd.DataFrame(columns=columns)

    is_home = team_id is None or team_id == header["home_team_id"].iloc[0]
    for_col, against_col = ("home_score", "away_score") if is_home else ("away_score", "home_score")
    steps = steps.rename(columns={for_col: "score_for", against_col: "score_against"})

    last_stint = pd.read_sql(
        text("SELECT MAX(end_seconds) AS end_seconds FROM lineup_stints WHERE game_id = :game_id"),
        engine,
        params={"game_id": game_id},
    )["end_seconds"].iloc[0]
    end = game_end_seconds(max(float(steps["seconds"].max()), float(last_stint or 0.0)))

    # Cierre de la escalera en el final del partido con el marcador OFICIAL.
    # No es cosmético: los eventos tipados no incluyen los tiros, así que la
    # última canasta puede caer después del último evento tipado y la
    # escalera se quedaría corta (caso real: `acb-105371`, 102-75 final
    # contra 102-72 en el último evento del play-by-play).
    closing = pd.DataFrame([{
        "seconds": end,
        "quarter": "OT" if end > _REGULATION_SECONDS else f"Q{int(end // 600)}",
        "game_clock": "00:00",
        "score_for": int(header[for_col].iloc[0]),
        "score_against": int(header[against_col].iloc[0]),
    }])
    # Origen 0-0: sin él, un parcial de salida (los primeros tres minutos) no
    # tiene contra qué medirse.
    opening = pd.DataFrame([
        {"seconds": 0.0, "quarter": "Q1", "game_clock": "10:00", "score_for": 0, "score_against": 0}
    ])
    steps = pd.concat([opening, steps, closing], ignore_index=True)
    steps = steps.drop_duplicates(subset="seconds", keep="last").sort_values("seconds").reset_index(drop=True)
    steps["margin"] = steps["score_for"] - steps["score_against"]
    return steps[columns]


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_score_steps(_engine: Engine, game_id: str, team_id: Optional[str] = None) -> pd.DataFrame:
    """Marcador real de un partido como ESCALERA, visto desde `team_id`.

    Args:
        team_id: desde qué equipo se mira el marcador. `None` = el local.

    Returns:
        `seconds, quarter, game_clock, score_for, score_against, margin`, un
        escalón por segundo con evento, más el 0-0 de salida y el marcador
        final. Vacío si el partido no tiene play-by-play tipado (fase 2 sin
        reingerir) o no existe.

    Ojo con la granularidad: `play_events` no tipa los tiros (ver §5 de la
    propuesta), así que el marcador se OBSERVA en los eventos que sí están
    (rebotes, faltas, pérdidas...). Los puntos aparecen igual —el marcador
    que viaja con cada evento ya los lleva— pero el escalón se sitúa en el
    siguiente evento tipado, no en el segundo exacto de la canasta.
    """
    return _score_steps(_engine, game_id, team_id)


def detect_runs(steps: pd.DataFrame, window_s: float = 180.0, min_swing: int = 8) -> pd.DataFrame:
    """Parciales de un partido: ventanas cortas en las que el marcador se movió mucho.

    Un parcial es una ventana de duración ≤ `window_s` en la que el margen se
    mueve ≥ `min_swing` puntos, en cualquiera de los dos sentidos. Los dos
    umbrales son parámetros y no constantes porque cada entrenador tiene su
    idea de "parcial preocupante": 8 puntos en 3 minutos es el punto de
    partida, no la definición.

    El algoritmo, en tres pasos:

    1. Para cada escalón `i`, la ventana `[i, j]` que cabe en `window_s`, y el
       escalón de dentro que más aleja el margen de `margin[i]`.
    2. Se recorta la ventana por los dos extremos hasta el último/primer
       escalón que mantiene el mismo swing, para que el parcial no arrastre
       los segundos planos de antes y de después.
    3. Se ordena por magnitud y se descartan los candidatos que solapan con
       uno ya aceptado: dos ventanas que describen el mismo arreón son un
       parcial, no dos.

    Args:
        steps: salida de `game_score_steps` (la escalera, ya orientada a un
            equipo — el signo del swing es desde ESE equipo).

    Returns:
        `start_seconds, end_seconds, duration_s, margin_start, margin_end,
        swing, points_for, points_against, direction ('favor'|'contra'),
        quarter_start, clock_start, quarter_end, clock_end, label`, del
        parcial más grande al más pequeño. Vacío si ninguno cumple los
        umbrales.
    """
    columns = [
        "start_seconds", "end_seconds", "duration_s", "margin_start", "margin_end",
        "swing", "points_for", "points_against", "direction",
        "quarter_start", "clock_start", "quarter_end", "clock_end", "label",
    ]
    if steps is None or len(steps) < 2:
        return pd.DataFrame(columns=columns)

    seconds = steps["seconds"].to_numpy(dtype=float)
    margin = steps["margin"].to_numpy(dtype=float)
    n = len(seconds)

    candidates = []
    for i in range(n - 1):
        best_k, best_swing = None, 0.0
        for k in range(i + 1, n):
            if seconds[k] - seconds[i] > window_s:
                break
            swing = margin[k] - margin[i]
            if abs(swing) > abs(best_swing):
                best_k, best_swing = k, swing
        if best_k is None or abs(best_swing) < min_swing:
            continue
        # Recorte: el parcial empieza en el ÚLTIMO escalón con el margen de
        # partida y acaba en el PRIMERO con el de llegada. Sin esto, los
        # segundos planos de antes del arreón entran dentro del parcial y lo
        # alargan sin aportarle un punto.
        start = i
        while start + 1 < best_k and margin[start + 1] == margin[i]:
            start += 1
        end = best_k
        while end - 1 > start and margin[end - 1] == margin[best_k]:
            end -= 1
        candidates.append((abs(best_swing), -(seconds[end] - seconds[start]), start, end, best_swing))

    # Magnitud primero y, a igualdad, el parcial más corto (más concentrado).
    candidates.sort(reverse=True)
    accepted = []
    for _, _, start, end, swing in candidates:
        if any(seconds[start] < seconds[b] and seconds[a] < seconds[end] for a, b, _ in accepted):
            continue
        accepted.append((start, end, swing))

    rows = []
    for start, end, swing in accepted:
        head, tail = steps.iloc[start], steps.iloc[end]
        points_for = int(tail["score_for"] - head["score_for"])
        points_against = int(tail["score_against"] - head["score_against"])
        rows.append({
            "start_seconds": float(head["seconds"]),
            "end_seconds": float(tail["seconds"]),
            "duration_s": float(tail["seconds"] - head["seconds"]),
            "margin_start": int(head["margin"]),
            "margin_end": int(tail["margin"]),
            "swing": int(swing),
            "points_for": points_for,
            "points_against": points_against,
            "direction": "favor" if swing > 0 else "contra",
            "quarter_start": head["quarter"],
            "clock_start": head["game_clock"],
            "quarter_end": tail["quarter"],
            "clock_end": tail["game_clock"],
            # Sin flecha "→" a propósito: esta etiqueta viaja también al
            # `tool_result` del asistente y de ahí al log del servidor, y una
            # consola Windows en cp1252 revienta con cualquier carácter fuera
            # de esa página (mismo motivo que en `tools/__init__.py::summarize`).
            "label": (
                f"{head['quarter']} {head['game_clock']} a {tail['quarter']} {tail['game_clock']} · "
                f"{points_for}-{points_against} ({int(swing):+d})"
            ),
        })
    runs = pd.DataFrame(rows, columns=columns)
    if runs.empty:
        return runs
    return runs.sort_values("swing", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_runs(
    _engine: Engine,
    game_id: str,
    team_id: Optional[str] = None,
    window_s: float = 180.0,
    min_swing: int = 8,
) -> pd.DataFrame:
    """Parciales de un partido vistos desde `team_id` (`detect_runs` sobre la escalera)."""
    return detect_runs(_score_steps(_engine, game_id, team_id), window_s, min_swing)


def window_lineup(stints: pd.DataFrame, start_seconds: float, end_seconds: float) -> pd.DataFrame:
    """El quinteto de un parcial: el tramo que más segundos aporta a la ventana.

    Solo cuenta el TROZO del tramo que cae dentro de la ventana —el mismo
    recorte que ya hace `queries_assistant.clutch_lineups`—: un tramo que
    empieza en el minuto 20 y llega al final no son veinte minutos de un
    parcial de tres.

    Los segundos que se devuelven por jugador son los suyos DENTRO de la
    ventana sumando todos sus tramos, no solo el del quinteto elegido: en un
    parcial con un cambio por medio, un jugador puede llevar los tres minutos
    en pista aunque su quinteto solo dure dos.

    Args:
        stints: salida de `game_stints` (una fila por tramo y jugador).

    Returns:
        `player_id, player_name, seconds, minutes, share` (share = % de la
        ventana que el jugador ha estado en pista), de más a menos minutos.
        Vacío si el partido no tiene tramos o si ninguno solapa la ventana.
    """
    columns = ["player_id", "player_name", "seconds", "minutes", "share"]
    if stints is None or stints.empty:
        return pd.DataFrame(columns=columns)

    clipped = stints.assign(
        clipped_seconds=(
            stints["end_seconds"].clip(upper=end_seconds) - stints["start_seconds"].clip(lower=start_seconds)
        ).clip(lower=0.0)
    )
    clipped = clipped[clipped["clipped_seconds"] > 0]
    if clipped.empty:
        return pd.DataFrame(columns=columns)

    # El quinteto del parcial: el tramo con más segundos dentro de la ventana.
    main_stint = clipped.groupby("stint_id")["clipped_seconds"].first().idxmax()
    five = set(clipped.loc[clipped["stint_id"] == main_stint, "player_id"])

    per_player = (
        clipped[clipped["player_id"].isin(five)]
        .groupby(["player_id", "player_name"], as_index=False)["clipped_seconds"]
        .sum()
        .rename(columns={"clipped_seconds": "seconds"})
    )
    window = max(float(end_seconds) - float(start_seconds), 1.0)
    per_player["minutes"] = per_player["seconds"] / 60.0
    per_player["share"] = 100.0 * per_player["seconds"] / window
    return per_player.sort_values("seconds", ascending=False).reset_index(drop=True)[columns]


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_window_events(
    _engine: Engine,
    game_id: str,
    start_seconds: float,
    end_seconds: float,
    team_id: Optional[str] = None,
) -> pd.DataFrame:
    """Play-by-play tipado de UNA ventana del partido, con el marcador en cada evento.

    Es la segunda capa de la vista de parciales: sin ella el timeline dice
    cuándo se fue el partido, pero no qué pasó. Los tiros NO aparecen —
    `shots` no guarda ni cuarto ni reloj (§5 de la propuesta)—, así que los
    puntos se leen por el salto del marcador y no como un evento más; es la
    mayor carencia de esta vista y la interfaz la dice en voz alta en vez de
    dejar que el entrenador la deduzca.

    Args:
        team_id: equipo desde el que se orienta el marcador y se marca
            `is_own`. `None` = el local.

    Returns:
        `seconds, quarter, game_clock, event_type, event_detail, team_id,
        team_name, player_name, score_for, score_against, is_own`, en orden
        cronológico. Vacío si la ventana no tiene eventos tipados.
    """
    columns = [
        "seconds", "quarter", "game_clock", "event_type", "event_detail",
        "team_id", "team_name", "player_name", "score_for", "score_against", "is_own",
    ]
    sql = text("""
        SELECT pe.seconds, pe.quarter, pe.game_clock, pe.event_type, pe.event_detail,
               pe.team_id, t.name AS team_name, p.name AS player_name,
               pe.home_score, pe.away_score, g.home_team_id
        FROM play_events pe
        JOIN games g ON g.id = pe.game_id
        JOIN teams t ON t.id = pe.team_id
        LEFT JOIN players p ON p.id = pe.player_id
        WHERE pe.game_id = :game_id
          AND pe.seconds BETWEEN :start_seconds AND :end_seconds
        ORDER BY pe.seconds, pe.id
    """)
    df = pd.read_sql(
        sql,
        _engine,
        params={"game_id": game_id, "start_seconds": start_seconds, "end_seconds": end_seconds},
    )
    if df.empty:
        return pd.DataFrame(columns=columns)

    own = team_id if team_id is not None else df["home_team_id"].iloc[0]
    is_home = own == df["home_team_id"].iloc[0]
    df["score_for"] = df["home_score"] if is_home else df["away_score"]
    df["score_against"] = df["away_score"] if is_home else df["home_score"]
    df["is_own"] = df["team_id"] == own
    return df[columns]


# ---------------------------------------------------------------------------
# Calidad de tiro (xPPS) — `doc/features/propuestas/02_calidad_de_tiro.md`.
#
# Las cuatro consultas de esta sección devuelven lo MISMO con distinto corte:
# recuentos de tiros agregados por (competición, zona, `located`), nunca tiros
# fila a fila. En la v1 por zonas el valor esperado es constante dentro de una
# zona, así que agregar en SQL no pierde un solo decimal y evita arrastrar
# 92.000 filas hasta la interfaz para volver a agruparlas en pandas.
#
# `located` viaja SIN filtrar a propósito, en vez de descartarse aquí: quien
# calcula (`app/analytics/shot_quality.py::split_usable`) necesita poder
# contar lo que se queda fuera para declararlo en pantalla. Una consulta que
# filtra en silencio deja a la interfaz sin forma de decir "el 2% de los tiros
# no entra en este número".
#
# ATRIBUCIÓN DE EQUIPO: como en `game_shots`/`team_shots_season`, el equipo de
# un tiro es el equipo ACTUAL del tirador (`players.team_id`) — el esquema no
# guarda a qué club pertenecía en cada partido. Aquí se acota además a los
# partidos del equipo y a los dos equipos de cada partido
# (`p.team_id IN (g.home_team_id, g.away_team_id)`), que corrige el caso más
# molesto: los tiros que un jugador traspasado hizo en OTRO club ya no cuentan
# ni como nuestros ni como concedidos. Lo que sigue sin poder corregirse es el
# jugador que se fue a mitad de temporada: sus tiros en nuestros partidos
# pasan a contarse en el bando contrario. Se documenta en vez de disimularlo.
# ---------------------------------------------------------------------------

#: Columnas comunes a las cuatro consultas de recuentos. `COALESCE(s.located,
#: 1)` replica el criterio del resto del módulo (fila anterior a esa columna =
#: localizada, ver `game_shots`).
_SHOT_COUNT_COLUMNS = """
               g.competition_id, s.zone_id, cz.label AS zone_label,
               COALESCE(s.located, 1) AS located,
               COUNT(*) AS shots, SUM(s.made) AS made
"""
_SHOT_COUNT_GROUP = "GROUP BY g.competition_id, s.zone_id, cz.label, COALESCE(s.located, 1)"


@st.cache_data(ttl=_TTL, show_spinner=False)
def league_shot_counts(_engine: Engine, season_id: int) -> pd.DataFrame:
    """Todos los tiros de la temporada, agregados por competición y zona.

    Es la materia prima de la línea base de liga
    (`analytics/shot_quality.py::league_baseline`): con qué se compara cada
    tiro del Baskonia o de un rival. Son TODOS los equipos, no solo los
    nuestros — esa es exactamente la referencia que hace falta.

    No se materializa en ninguna tabla: agregado en SQL son unas decenas de
    filas (competiciones x zonas x `located`), así que cabe de sobra en
    `st.cache_data`. La tabla precalculada que menciona §6 del documento solo
    haría falta para la v2 por posición, que no está implementada.

    Returns:
        `competition_id, zone_id, zone_label, located, shots, made`.
        `zone_id`/`zone_label` son `NULL` en los tiros que la ingesta no pudo
        clasificar. Vacío si la temporada no tiene tiros.
    """
    sql = text(f"""
        SELECT {_SHOT_COUNT_COLUMNS}
        FROM shots s
        JOIN games g ON g.id = s.game_id
        LEFT JOIN court_zones cz ON cz.id = s.zone_id
        WHERE g.season_id = :season_id
        {_SHOT_COUNT_GROUP}
    """)
    return pd.read_sql(sql, _engine, params={"season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_shot_counts(_engine: Engine, team_id: str, season_id: int, conceded: bool = False) -> pd.DataFrame:
    """Tiros de un equipo en la temporada (o los que CONCEDE), por competición y zona.

    Args:
        conceded: `False` (por defecto) para los tiros del propio equipo;
            `True` para los del rival en los partidos de ese equipo — que es
            lo que hace posible la lectura defensiva honesta del documento
            (§2.3): el xPPS concedido no premia que el rival falle tiros
            abiertos, a diferencia del %TC en contra.

    Returns:
        Mismas columnas que `league_shot_counts`. Vacío si el equipo no tiene
        partidos con tiros en esa temporada.
    """
    side = (
        "p.team_id <> :team_id AND p.team_id IN (g.home_team_id, g.away_team_id)"
        if conceded
        else "p.team_id = :team_id"
    )
    sql = text(f"""
        SELECT {_SHOT_COUNT_COLUMNS}
        FROM shots s
        JOIN players p ON p.id = s.player_id
        JOIN games g ON g.id = s.game_id
        LEFT JOIN court_zones cz ON cz.id = s.zone_id
        WHERE g.season_id = :season_id
          AND (g.home_team_id = :team_id OR g.away_team_id = :team_id)
          AND ({side})
        {_SHOT_COUNT_GROUP}
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_shot_counts(_engine: Engine, game_id: str) -> pd.DataFrame:
    """Tiros de UN partido, por equipo, competición y zona.

    Los dos equipos en la misma consulta (no una llamada por bando): la
    lectura de §2.1 del documento es comparativa —lo que generamos contra lo
    que concedimos— y separarla en dos consultas solo obligaría a la página a
    volver a juntarlas.

    Returns:
        `team_id, season_id` más las columnas de `league_shot_counts`.
        `season_id` viaja para que quien pida los tiros de un partido pueda
        pedir la línea base de SU temporada sin una consulta más (un partido
        de hace dos años no se juzga contra la liga de este año). Vacío si el
        partido no tiene tiros cargados.
    """
    sql = text(f"""
        SELECT p.team_id, g.season_id, {_SHOT_COUNT_COLUMNS}
        FROM shots s
        JOIN players p ON p.id = s.player_id
        JOIN games g ON g.id = s.game_id
        LEFT JOIN court_zones cz ON cz.id = s.zone_id
        WHERE s.game_id = :game_id
          AND p.team_id IN (g.home_team_id, g.away_team_id)
        GROUP BY p.team_id, g.season_id, g.competition_id, s.zone_id, cz.label, COALESCE(s.located, 1)
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_shot_counts(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Tiros de cada jugador de un equipo en la temporada, por competición y zona.

    Alimenta el ranking de §2.2 del documento: quién ELIGE bien (xPPS) y quién
    ACIERTA por encima de lo que valen sus tiros (PPS − xPPS). Son dos
    habilidades distintas que hoy se mezclan en una sola columna de %.

    Returns:
        `player_id, player_name` más las columnas de `league_shot_counts`.
        Solo jugadores hoy en la plantilla del equipo (`players.team_id`),
        con sus tiros en los partidos de ese equipo. Vacío si no hay tiros.
    """
    sql = text(f"""
        SELECT p.id AS player_id, p.name AS player_name, {_SHOT_COUNT_COLUMNS}
        FROM shots s
        JOIN players p ON p.id = s.player_id
        JOIN games g ON g.id = s.game_id
        LEFT JOIN court_zones cz ON cz.id = s.zone_id
        WHERE g.season_id = :season_id
          AND (g.home_team_id = :team_id OR g.away_team_id = :team_id)
          AND p.team_id = :team_id
        GROUP BY p.id, p.name, g.competition_id, s.zone_id, cz.label, COALESCE(s.located, 1)
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


# ---------------------------------------------------------------------------
# Fatiga y calendario cruzado ACB + Euroliga
# (`doc/features/propuestas/04_fatiga_y_calendario.md`).
#
# Las tres consultas comparten un mismo cálculo de fondo: el DESCANSO de un
# equipo es la distancia en días hasta su partido anterior EN CUALQUIER
# COMPETICIÓN, no solo la que se esté mirando — es el matiz que ninguna web
# de estadística puede dar porque ninguna tiene ACB y Euroliga bajo la misma
# identidad de club (§1/§4 del documento). El `LAG` de SQLite corre sobre TODO
# el historial del equipo, sin filtrar por `season_id`, y solo el resultado
# final se recorta a la temporada pedida — así el primer partido de una
# temporada nueva no pierde su descanso real (el del final de la anterior)
# solo por caer justo en el corte.
#
# Requieren que `team_external_ids` no tenga un club partido en dos
# `team_id` (§5 del documento: el caso real era el Barça, `barca` + `fcb`,
# arreglado con `tools/fix_barca_identity.py`) — un club así ve la mitad de
# sus partidos aquí, con el descanso y la carga equivocados.
# ---------------------------------------------------------------------------


@st.cache_data(ttl=_TTL, show_spinner=False)
def rest_days(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Días de descanso con los que un equipo llega a cada partido de la temporada.

    Returns:
        `game_id, game_date, competition, condicion, rival, rest_days`,
        cronológico. `rest_days` es `None` en el primerísimo partido del
        equipo en TODA la base de datos (no hay "anterior" que restar);
        cualquier otro hueco, incluido el de pretemporada al empezar una
        temporada nueva, se calcula contra el último partido jugado de
        verdad, sea de la competición que sea. Vacío si el equipo no tiene
        partidos en esa temporada.
    """
    sql = text("""
        WITH team_games AS (
            SELECT g.id, g.game_date, g.season_id, c.name AS competition,
                   CASE WHEN g.home_team_id = :team_id THEN 'Local' ELSE 'Visitante' END AS condicion,
                   CASE WHEN g.home_team_id = :team_id THEN t_away.name ELSE t_home.name END AS rival,
                   LAG(g.game_date) OVER (ORDER BY g.game_date) AS previous_game_date
            FROM games g
            JOIN teams t_home ON t_home.id = g.home_team_id
            JOIN teams t_away ON t_away.id = g.away_team_id
            JOIN competitions c ON c.id = g.competition_id
            WHERE g.home_team_id = :team_id OR g.away_team_id = :team_id
        )
        SELECT id AS game_id, game_date, competition, condicion, rival,
               CAST(julianday(game_date) - julianday(previous_game_date) AS INTEGER) AS rest_days
        FROM team_games
        WHERE season_id = :season_id
        ORDER BY game_date
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def rolling_load(_engine: Engine, team_id: str, season_id: int, days: int) -> pd.DataFrame:
    """Minutos acumulados de cada jugador activo en una ventana móvil de `days` días.

    A diferencia de `minutes_load` (últimos N PARTIDOS), la ventana es de
    CALENDARIO: cinco partidos en nueve días y cinco en tres semanas no son la
    misma carga, y contar partidos confunde las dos (§2a del documento). La
    ventana de cada fila termina en la fecha de ESE partido y mira `days` días
    atrás, semiabierta por la izquierda (mismo criterio que
    `pandas.DataFrame.rolling` con ventana temporal): con `days=7`, una fila
    del domingo suma desde el lunes anterior en adelante, no desde el domingo
    previo, que quedaría justo fuera.

    Un partido sin minutos (DNP, no convocado) cuenta como 0 en la suma de la
    ventana, no como hueco: la ausencia no descansa menos a nadie, y dejarla
    fuera de la cuenta rebajaría artificialmente la carga de un jugador con
    bajas intermitentes.

    Args:
        days: tamaño de la ventana en días (7 o 14 en la pantalla, ver el
            documento; cualquier entero positivo funciona).

    Returns:
        `player_id, player_name, game_date, minutes, rolling_minutes,
        games_in_window, minutes_per_day`. `minutes` es el dato crudo de ESE
        partido (`NaN` = no jugó, mismo criterio que `minutes_load`);
        `rolling_minutes`/`games_in_window` sí cuentan esos partidos, como
        0 minutos / 1 partido. `minutes_per_day = rolling_minutes / days`,
        para comparar jugadores con roles distintos sin que un partido más o
        menos en la ventana desnivele la lectura (sugerencia del documento,
        §4). Vacío si el equipo no tiene jugadores activos con partidos en
        esa temporada.
    """
    sql = text("""
        SELECT p.id AS player_id, p.name AS player_name, g.game_date, pgs.minutes
        FROM players p
        JOIN games g ON (g.home_team_id = :team_id OR g.away_team_id = :team_id) AND g.season_id = :season_id
        LEFT JOIN player_game_stats pgs ON pgs.game_id = g.id AND pgs.player_id = p.id
        WHERE p.team_id = :team_id AND p.active = 1
        ORDER BY p.name, g.game_date
    """)
    df = pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})
    if df.empty:
        return df

    df["game_date"] = pd.to_datetime(df["game_date"])
    df["_minutes_filled"] = df["minutes"].fillna(0.0)

    def _rolling(group: pd.DataFrame) -> pd.DataFrame:
        windowed = group.rolling(f"{days}D", on="game_date")["_minutes_filled"]
        # pandas 3.x quita la columna de agrupación (`player_id`) del grupo
        # que llega aquí y solo la deja en `group.name` — hay que devolverla
        # explícitamente o la fila pierde con qué jugador identificarse.
        return group.assign(
            player_id=group.name, rolling_minutes=windowed.sum(), games_in_window=windowed.count()
        )

    result = df.groupby("player_id", group_keys=False, sort=False).apply(_rolling)
    result["minutes_per_day"] = result["rolling_minutes"] / days
    columns = ["player_id", "player_name", "game_date", "minutes", "rolling_minutes", "games_in_window", "minutes_per_day"]
    return result.sort_values(["player_name", "game_date"]).reset_index(drop=True)[columns]


#: Orden explícito de los tramos de descanso — NO alfabético: "≥5" iría antes
#: que "≤1" por el punto de código de sus símbolos, que es justo lo que
#: produciría un `ORDER BY rest_bucket` sin más.
_REST_BUCKETS = ["≤1", "2", "3-4", "≥5"]


@st.cache_data(ttl=_TTL, show_spinner=False)
def performance_by_rest(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Net rating del equipo agrupado por tramos de descanso (≤1, 2, 3-4, ≥5 días).

    El dato que convierte la ficha de fatiga en decisión (§2b del documento):
    "con dos días de descanso concede X puntos más por 100 posesiones que con
    más". Mismo cálculo de descanso que `rest_days` (cualquier competición,
    `LAG` sobre todo el historial del equipo), aquí cruzado con
    `game_advanced_stats` en vez de mostrado como calendario.

    Returns:
        `rest_bucket, gp, net_rating`, en el orden fijo de `_REST_BUCKETS`
        (un tramo sin partidos con `game_advanced_stats` no aparece). `gp`
        viaja siempre: con 78 partidos como mucho por temporada de doble
        competición (§4 del documento), un tramo puede tener muestra
        minúscula y hay que poder decirlo, no solo enseñar la media. El
        primerísimo partido del equipo en toda la base de datos (sin
        "anterior" del que restar el descanso) queda fuera de los cuatro
        tramos, no metido en ninguno. Vacío si el equipo no tiene partidos
        con estadísticas avanzadas en esa temporada.
    """
    sql = text("""
        WITH team_games AS (
            SELECT g.id, g.season_id,
                   CAST(julianday(g.game_date) - julianday(
                       LAG(g.game_date) OVER (ORDER BY g.game_date)
                   ) AS INTEGER) AS rest_days
            FROM games g
            WHERE g.home_team_id = :team_id OR g.away_team_id = :team_id
        )
        SELECT
            CASE
                WHEN tg.rest_days <= 1 THEN '≤1'
                WHEN tg.rest_days = 2 THEN '2'
                WHEN tg.rest_days IN (3, 4) THEN '3-4'
                ELSE '≥5'
            END AS rest_bucket,
            COUNT(*) AS gp,
            AVG(gas.net_rating) AS net_rating
        FROM team_games tg
        JOIN game_advanced_stats gas ON gas.game_id = tg.id AND gas.team_id = :team_id
        WHERE tg.season_id = :season_id AND tg.rest_days IS NOT NULL
        GROUP BY rest_bucket
    """)
    df = pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})
    if df.empty:
        return df
    order = {bucket: i for i, bucket in enumerate(_REST_BUCKETS)}
    return (
        df.assign(_sort=df["rest_bucket"].map(order))
        .sort_values("_sort")
        .drop(columns="_sort")
        .reset_index(drop=True)
    )
