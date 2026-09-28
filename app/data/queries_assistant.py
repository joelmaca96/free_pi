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
from itertools import combinations
from typing import List, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

try:  # pragma: no cover - ver nota en tools/context.py
    from app.analytics import impact
    from app.analytics.shot_quality import shrink
    from app.data import queries
except ImportError:  # pragma: no cover
    from analytics import impact
    from analytics.shot_quality import shrink
    from data import queries

_TTL = 3600  # mismo criterio que `queries.py`: la ingesta corre por su cuenta.

# Mínimos de muestra del §4 de la propuesta 07 (`doc/features/propuestas/
# 07_onoff_y_duplas.md`), NO NEGOCIABLES: con un quinteto de cinco la muestra
# se agota casi siempre (§1: solo 2 de 724 combinaciones llegan a 50 minutos
# en toda la temporada), así que jugador/pareja/trío es el nivel más bajo al
# que se puede bajar y que la cifra siga significando algo. Se usan también
# como constante `k` de encogimiento (`shrink`, ver `analytics.shot_quality`):
# a `k` minutos se conserva la mitad del valor bruto.
ON_OFF_MIN_MINUTES = 200.0
COMBO_MIN_MINUTES = 100.0


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


@st.cache_data(ttl=_TTL, show_spinner=False)
def _view_columns(_engine: Engine, view: str) -> frozenset:
    """Columnas de una vista en ESTA base de datos.

    SQLite congela la definición de una vista al crearla, así que una BD que no
    haya pasado por `_refresh_views` se queda con la versión antigua —sin las
    columnas de tiros libres, por ejemplo— aunque `schema.sql` ya las declare.
    """
    if not _has_view(_engine, view):
        return frozenset()
    return frozenset(col["name"] for col in inspect(_engine).get_columns(view))

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
    # Fase 1 (boxscore ampliado): entran solas en league_leaders/
    # league_percentiles sin tools adicionales, sumando la clave a esta lista
    # blanca — ver doc/features/ingestor/02_plan_stats_completas.md §Fase 1.
    "stl": ("player", "stl_avg", "robos por partido"),
    "blk": ("player", "blk_avg", "tapones por partido"),
    "tov": ("player", "tov_avg", "pérdidas por partido"),
    "pf": ("player", "pf_avg", "faltas cometidas por partido"),
    "oreb": ("player", "oreb_avg", "rebotes ofensivos por partido"),
    "dreb": ("player", "dreb_avg", "rebotes defensivos por partido"),
    "pir": ("player", "pir_avg", "valoración (PIR) por partido"),
}

# Métricas donde MENOS es mejor: ordenar todas descendente pondría al peor
# defensor del campeonato en lo alto de "mejor defensa", o al que más pierde
# el balón en lo alto de "mejor manejador".
_LOWER_IS_BETTER = {"drtg", "tov", "pf"}


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
    # Los tiros libres/boxscore ampliado se piden solo si la columna existe
    # aquí (ver `_table_columns`); si no, se devuelven como NULL, que es
    # exactamente lo que significan: "este dato no está", no "cero".
    columns = _table_columns(_engine, "player_game_stats")
    has_ft = {"ftm", "fta"} <= columns
    ft_select = "pgs.ftm, pgs.fta" if has_ft else "NULL AS ftm, NULL AS fta"
    box_extras_cols = ["stl", "tov", "blk", "blk_against", "pf", "pf_drawn", "oreb", "dreb", "plus_minus", "pir", "dunks"]
    has_box_extras = set(box_extras_cols) <= columns
    box_extras_select = (
        ", ".join(f"pgs.{col}" for col in box_extras_cols) if has_box_extras
        else ", ".join(f"NULL AS {col}" for col in box_extras_cols)
    )
    sql = text(f"""
        SELECT pgs.player_id, p.name AS player_name, p.team_id, t.name AS team_name,
               pgs.minutes, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct, {ft_select}, {box_extras_select}
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
def player_free_throws(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Tiros libres de un jugador por competición: volumen y acierto ponderado.

    Va aparte de `queries.player_averages_all` (que no los selecciona) y no
    dentro, porque las columnas solo existen si las vistas de esta base de
    datos son las nuevas — ver `_view_columns`. Una BD sin reingerir devuelve
    vacío en vez de reventar.

    `ft_pct` sale de la vista y es el acierto PONDERADO POR VOLUMEN
    (SUM(ftm)/SUM(fta)), no la media de los porcentajes de cada partido: un
    1/1 no puede pesar lo mismo que un 8/12.

    Returns:
        `competition, gp, gp_ft, ftm, fta, ftm_avg, fta_avg, ft_pct`.
        `gp_ft` (partidos CON dato de tiros libres) va aparte de `gp` a
        propósito: sin él no se distingue "no tiró un solo libre" de "ese
        partido no trae el dato".
    """
    if not {"ft_pct", "gp_ft"} <= _view_columns(_engine, "player_stats_by_competition"):
        return pd.DataFrame()

    sql = text("""
        SELECT c.name AS competition, s.gp, s.gp_ft, s.ftm, s.fta,
               s.ftm_avg, s.fta_avg, s.ft_pct
        FROM player_stats_by_competition s
        JOIN competitions c ON c.id = s.competition_id
        WHERE s.player_id = :player_id AND s.season_id = :season_id
        ORDER BY s.gp DESC
    """)
    return pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_free_throws(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Tiros libres que un equipo LANZA y los que CONCEDE, por competición.

    Los del rival (`opp_*`) salen del self-join que ya hace la vista sobre
    `game_advanced_stats`: no hay columnas "concedidas" duplicadas, son la fila
    del otro equipo del mismo partido. Es la mitad que hace útil el dato para
    scouting — cuántos libres regala una defensa dice más de ella que cuántos
    lanza su ataque.
    """
    if not {"ft_pct", "opp_ft_pct"} <= _view_columns(_engine, "team_stats_by_competition"):
        return pd.DataFrame()

    sql = text("""
        SELECT c.name AS competition, s.gp, s.gp_ft,
               s.ftm, s.fta, s.ftm_avg, s.fta_avg, s.ft_pct,
               s.opp_ftm, s.opp_fta, s.opp_ftm_avg, s.opp_fta_avg, s.opp_ft_pct
        FROM team_stats_by_competition s
        JOIN competitions c ON c.id = s.competition_id
        WHERE s.team_id = :team_id AND s.season_id = :season_id
        ORDER BY s.gp DESC
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_box_extras(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Boxscore ampliado de un jugador por competición (Fase 1).

    Mismo patrón que `player_free_throws`: va aparte de
    `queries.player_averages_all` porque solo existe si esta base de datos
    tiene el bloque nuevo — `_view_columns` protege una BD sin reingerir.

    Returns:
        `competition, gp, gp_box_extras, stl_avg, tov_avg, blk_avg,
        blk_against_avg, pf_avg, pf_drawn_avg, oreb_avg, dreb_avg,
        plus_minus_avg, pir_avg, gp_dunks, dunks`. Vacío si la BD no tiene
        el boxscore ampliado todavía.
    """
    if not {"gp_box_extras", "stl_avg", "pir_avg"} <= _view_columns(_engine, "player_stats_by_competition"):
        return pd.DataFrame()

    sql = text("""
        SELECT c.name AS competition, s.gp, s.gp_box_extras,
               s.stl_avg, s.tov_avg, s.blk_avg, s.blk_against_avg, s.pf_avg, s.pf_drawn_avg,
               s.oreb_avg, s.dreb_avg, s.plus_minus_avg, s.pir_avg, s.gp_dunks, s.dunks
        FROM player_stats_by_competition s
        JOIN competitions c ON c.id = s.competition_id
        WHERE s.player_id = :player_id AND s.season_id = :season_id
        ORDER BY s.gp DESC
    """)
    return pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def team_box_extras(_engine: Engine, team_id: str, season_id: int) -> pd.DataFrame:
    """Boxscore ampliado de un equipo por competición, propio y CONCEDIDO (Fase 1).

    Mismo patrón que `team_free_throws`. `opp_*` sale del self-join que ya
    hace la vista sobre `game_advanced_stats` (rival del mismo `game_id`).
    """
    if not {"gp_box_extras", "opp_stl_avg"} <= _view_columns(_engine, "team_stats_by_competition"):
        return pd.DataFrame()

    sql = text("""
        SELECT c.name AS competition, s.gp, s.gp_box_extras,
               s.stl_avg, s.tov_avg, s.blk_avg, s.blk_against_avg, s.pf_avg, s.pf_drawn_avg,
               s.oreb_avg, s.dreb_avg, s.pir_avg,
               s.opp_stl_avg, s.opp_tov_avg, s.opp_blk_avg, s.opp_pf_avg
        FROM team_stats_by_competition s
        JOIN competitions c ON c.id = s.competition_id
        WHERE s.team_id = :team_id AND s.season_id = :season_id
        ORDER BY s.gp DESC
    """)
    return pd.read_sql(sql, _engine, params={"team_id": team_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def game_play_events(_engine: Engine, game_id: str, event_type: Optional[str] = None) -> pd.DataFrame:
    """Play-by-play tipado de un partido (Fase 2), con reloj y marcador.

    Args:
        event_type: acota a un tipo ('steal', 'turnover', 'block', 'oreb',
            'dreb', 'assist', 'foul_drawn', 'foul_personal' y, en partidos
            reingeridos desde 2026-09-28, 'fg2_made', 'fg2_missed',
            'fg3_made', 'fg3_missed', 'ft_made', 'ft_missed', 'timeout'), o
            `None` para todos.

    Returns:
        `quarter, game_clock, event_type, event_detail, team_name,
        player_name, home_score, away_score`, en orden cronológico
        aproximado (por cuarto; `game_clock` cuenta hacia atrás dentro de
        cada cuarto, igual que `game_key_events`). Vacío si el partido no
        tiene play-by-play tipado (Fase 2 sin reingerir para ese partido).
    """
    sql = text("""
        SELECT pe.quarter, pe.game_clock, pe.event_type, pe.event_detail,
               t.name AS team_name, p.name AS player_name,
               pe.home_score, pe.away_score
        FROM play_events pe
        JOIN teams t ON t.id = pe.team_id
        LEFT JOIN players p ON p.id = pe.player_id
        WHERE pe.game_id = :game_id
          AND (:event_type IS NULL OR pe.event_type = :event_type)
        ORDER BY pe.quarter, pe.game_clock DESC
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id, "event_type": event_type})


@st.cache_data(ttl=_TTL, show_spinner=False)
def foul_timeline(_engine: Engine, game_id: str) -> pd.DataFrame:
    """Faltas de un partido, cometidas y provocadas, con reloj exacto y equipo.

    Gestión de faltas (`doc/features/propuestas/06_gestion_de_faltas.md`,
    §2c): es `game_play_events` acotado a los dos tipos de falta, con
    `team_id` añadido — que `game_play_events` no trae — porque la capa de
    marcas sobre el timeline de rotaciones
    ([01](../../doc/features/propuestas/01_rotaciones_y_parciales.md))
    necesita saber de qué equipo es cada falta para pintarla sobre la barra
    correcta, y `foul_bonus_minutes` necesita agrupar por (`team_id`,
    `quarter`) para saber cuándo un equipo entra en bonus.

    Returns:
        `event_type` ('foul_personal'|'foul_drawn'), `team_id`, `team_name`,
        `player_id`, `player_name`, `quarter`, `game_clock`, `seconds`, en
        orden cronológico. Vacío si el partido no tiene play-by-play tipado
        (fase 2 sin reingerir para ese partido).
    """
    sql = text("""
        SELECT pe.event_type, pe.team_id, t.name AS team_name,
               p.id AS player_id, p.name AS player_name,
               pe.quarter, pe.game_clock, pe.seconds
        FROM play_events pe
        JOIN teams t ON t.id = pe.team_id
        LEFT JOIN players p ON p.id = pe.player_id
        WHERE pe.game_id = :game_id
          AND pe.event_type IN ('foul_personal', 'foul_drawn')
        ORDER BY pe.seconds
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id})


def foul_bonus_minutes(events: pd.DataFrame) -> pd.DataFrame:
    """Minuto en que cada equipo entra en bonus, cuarto a cuarto.

    Bonus = 4.ª falta de EQUIPO en el cuarto (§4 de la propuesta 06): a
    partir de ahí, las siguientes faltas del mismo cuarto regalan tiros
    libres, y es justo el dato que hoy no se ve en ningún sitio ("entramos
    en bonus en el minuto 4 del tercer cuarto" — causa habitual de parcial).

    Función PURA sobre la salida de `foul_timeline`, sin tocar la base de
    datos — mismo patrón que `queries.detect_runs` sobre `game_score_steps`:
    encadenar una consulta cacheada dentro de otra no gana nada aquí, la
    entrada ya está en memoria.

    Args:
        events: salida de `foul_timeline` (de un único partido).

    Returns:
        `team_id, team_name, quarter, bonus_seconds, bonus_clock`, una fila
        por (equipo, cuarto) que llegó a la 4.ª falta. Vacío si ninguno la
        alcanzó. Como documenta §5 de la propuesta, `event_detail` no
        distingue con fiabilidad falta en tiro/antideportiva/técnica, así
        que TODAS las `foul_personal` cuentan igual para el bonus — es la
        misma limitación, no una nueva.
    """
    columns = ["team_id", "team_name", "quarter", "bonus_seconds", "bonus_clock"]
    if events is None or events.empty:
        return pd.DataFrame(columns=columns)

    personal = events[events["event_type"] == "foul_personal"].sort_values("seconds")
    if personal.empty:
        return pd.DataFrame(columns=columns)

    personal = personal.assign(foul_no=personal.groupby(["team_id", "quarter"]).cumcount() + 1)
    fourth = personal[personal["foul_no"] == 4]
    if fourth.empty:
        return pd.DataFrame(columns=columns)

    return (
        fourth.rename(columns={"seconds": "bonus_seconds", "game_clock": "bonus_clock"})[columns]
        .sort_values(["quarter", "team_id"])
        .reset_index(drop=True)
    )


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_advanced_profile(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Avanzadas oficiales por partido de un jugador (Fase 4, ACB-only).

    Contexto W/L APROXIMADO: se deriva de si el equipo ACTUAL del jugador
    (`players.team_id`) ganó ese partido concreto — misma aproximación ya
    documentada en `queries.team_shots_season`/`team_roster_production`
    (el esquema no guarda a qué equipo pertenecía el jugador EN CADA
    partido, así que un traspaso a mitad de temporada la ensucia hacia
    atrás).

    Returns:
        `game_date, competition, win` + todas las columnas de
        `player_advanced_stats` (`ast_ratio`..`pace`), un partido por fila.
        Vacío si el jugador no tiene avanzadas oficiales en esa temporada
        (siempre vacío para un jugador de Euroliga: fuente ACB-only).
    """
    if not inspect(_engine).has_table("player_advanced_stats"):
        return pd.DataFrame()

    sql = text("""
        SELECT g.game_date, c.name AS competition,
               CASE WHEN (g.home_team_id = p.team_id AND g.home_score > g.away_score)
                      OR (g.away_team_id = p.team_id AND g.away_score > g.home_score)
                    THEN 1 ELSE 0 END AS win,
               pas.ast_ratio, pas.ast_pct, pas.stl_ratio, pas.stl_pct, pas.blk_pct, pas.tov_pct,
               pas.orb_pct, pas.drb_pct, pas.trb_pct, pas.ts_pct, pas.three_par, pas.ppt,
               pas.pp2ps, pas.pp3ps, pas.ppft, pas.possessions, pas.pace
        FROM player_advanced_stats pas
        JOIN games g ON g.id = pas.game_id
        JOIN players p ON p.id = pas.player_id
        JOIN competitions c ON c.id = g.competition_id
        WHERE pas.player_id = :player_id AND g.season_id = :season_id
        ORDER BY g.game_date
    """)
    return pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_quarter_profile(_engine: Engine, player_id: str, season_id: int) -> pd.DataFrame:
    """Rendimiento medio de un jugador por cuarto, a lo largo de una temporada (Fase 3, ACB-only).

    Es la mitad "jugador" de `team_quarter_profile`: en vez de puntos de
    equipo por cuarto, aquí se agregan las filas de `player_game_quarter_stats`
    (boxscore de jugador por cuarto, solo existe para partidos de ACB — ver
    `Capabilities.quarter_player_stats`) sobre TODOS los partidos de la
    temporada, para leer si el jugador arranca fuerte y decae o al revés.

    Returns:
        `quarter, gp, min_avg, pts_avg, reb_avg, ast_avg, plus_minus_avg,
        pir_avg`, una fila por cuarto (1-4). Vacío si esta base de datos no
        tiene boxscore por cuarto o el jugador no tiene ninguno en esa
        temporada (siempre vacío para un jugador que solo jugó en Euroliga).
    """
    if not inspect(_engine).has_table("player_game_quarter_stats"):
        return pd.DataFrame()

    sql = text("""
        SELECT pgqs.quarter,
               COUNT(DISTINCT pgqs.game_id) AS gp,
               AVG(pgqs.minutes) AS min_avg,
               AVG(pgqs.pts) AS pts_avg,
               AVG(pgqs.reb) AS reb_avg,
               AVG(pgqs.ast) AS ast_avg,
               AVG(pgqs.plus_minus) AS plus_minus_avg,
               AVG(pgqs.pir) AS pir_avg
        FROM player_game_quarter_stats pgqs
        JOIN games g ON g.id = pgqs.game_id
        WHERE pgqs.player_id = :player_id AND g.season_id = :season_id
        GROUP BY pgqs.quarter
        ORDER BY pgqs.quarter
    """)
    return pd.read_sql(sql, _engine, params={"player_id": player_id, "season_id": season_id})


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
    """Percentiles de un jugador dentro de su competición y temporada.

    Fase 1 (2026-08-29): se añaden robos/tapones/pérdidas/faltas/rebote
    of-def/valoración — ya calculados en la vista `player_percentiles`
    (Fase 1 de boxscore ampliado) pero fuera de esta `SELECT` porque hasta
    ahora nadie los pedía; el dossier de scouting del rival
    (`app/reports/scouting_ppt.py`) es el primer consumidor que sí necesita
    poder decir "roba mucho balón" o "acumula faltas rápido" con un percentil
    detrás, no solo puntos/rebotes/asistencias/acierto. `tov_pct`/`pf_pct`
    vienen ya invertidos en la vista (percentil bajo = pierde/comete más),
    ver su comentario en `schema.sql`.
    """
    clauses = ["p.player_id = :player_id", "p.season_id = :season_id"]
    params = {"player_id": player_id, "season_id": season_id}
    if competition_id is not None:
        clauses.append("p.competition_id = :competition_id")
        params["competition_id"] = competition_id

    sql = text(f"""
        SELECT c.name AS competition, p.gp, p.league_players,
               p.min_avg, p.min_pct, p.pts_avg, p.pts_pct, p.reb_avg, p.reb_pct,
               p.ast_avg, p.ast_pct, p.efg_pct, p.efg_pct_pct,
               p.stl_avg, p.stl_pct, p.blk_avg, p.blk_pct, p.tov_avg, p.tov_pct,
               p.pf_avg, p.pf_pct, p.oreb_avg, p.oreb_pct, p.dreb_avg, p.dreb_pct,
               p.pir_avg, p.pir_pct
        FROM player_percentiles p
        JOIN competitions c ON c.id = p.competition_id
        WHERE {' AND '.join(clauses)}
        ORDER BY p.gp DESC
    """)
    return pd.read_sql(sql, _engine, params=params)


@st.cache_data(ttl=_TTL, show_spinner=False)
def league_player_percentiles(_engine: Engine, season_id: int) -> pd.DataFrame:
    """`player_percentile_row`, pero de TODOS los jugadores a la vez (propuesta 11).

    Base del vector de perfil de `app/analytics/similarity.py`: un jugador
    puede aparecer una vez por competición si reparte la temporada entre ACB
    y Euroliga — `similarity.py` decide cuál es su competición "principal"
    (más partidos) antes de comparar, esta consulta se limita a traer las
    filas tal cual están en la vista, sin elegir.

    Returns:
        Mismas columnas que `player_percentile_row` más `player_id` (aquí
        hace falta para saber DE QUIÉN es cada fila). Vacío si ningún
        jugador llega a los 5 partidos por competición que exige la vista
        `player_percentiles`.
    """
    sql = text("""
        SELECT p.player_id, c.name AS competition, p.competition_id, p.gp, p.league_players,
               p.min_avg, p.min_pct, p.pts_avg, p.pts_pct, p.reb_avg, p.reb_pct,
               p.ast_avg, p.ast_pct, p.efg_pct, p.efg_pct_pct,
               p.stl_avg, p.stl_pct, p.blk_avg, p.blk_pct, p.tov_avg, p.tov_pct,
               p.pf_avg, p.pf_pct, p.oreb_avg, p.oreb_pct, p.dreb_avg, p.dreb_pct,
               p.pir_avg, p.pir_pct
        FROM player_percentiles p
        JOIN competitions c ON c.id = p.competition_id
        WHERE p.season_id = :season_id
    """)
    return pd.read_sql(sql, _engine, params={"season_id": season_id})


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
        plus_minus, plus_minus_per_40, stints`, mejor diferencia BRUTA primero
        (ver el comentario del orden, al final).
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
    columns = [
        "jugadores", "player_ids", "seconds", "minutes", "points_for", "points_against",
        "plus_minus", "plus_minus_per_40", "stints",
    ]
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
    # La misma normalización que ya devuelven `team_lineups`, `player_on_off` y
    # `player_pairs`. Faltaba solo aquí, y el banco de pruebas lo pilló dos
    # veces en 48 intentos: sin la columna, el modelo hacía la división él
    # mismo ("+22 en 13,7 minutos, unos +64 por 40") y el verificador marcaba
    # el 64 como cifra sin verificar en una respuesta correcta. Que la cifra
    # la dé la herramienta es la salida de siempre en este repo; enseñar al
    # verificador a aceptar cuentas del modelo no lo es (medido: dejaría pasar
    # casi cualquier número).
    combos["plus_minus_per_40"] = 40.0 * combos["plus_minus"] / combos["minutes"]
    # El orden sigue siendo por diferencia BRUTA, a propósito, aunque
    # `team_lineups` ordene por el por-40. Con un mínimo de 60 segundos, ordenar
    # por la tasa pondría arriba un +11 en 2,6 minutos (+168 por 40) por encima
    # del +22 en 13,7 minutos, que es el único quinteto con muestra de verdad.
    # En el tramo final hay muy pocos segundos por quinteto, y ahí la tasa
    # premia al que menos jugó.
    return combos.sort_values(["plus_minus", "seconds"], ascending=False).head(limit)[columns].reset_index(drop=True)


# ------------------------------------------------------- on/off y duplas --
# Propuesta 07 (`doc/features/propuestas/07_onoff_y_duplas.md`): el quinteto
# de cinco no tiene muestra (§1), así que se baja a jugador suelto, pareja y
# trío, calculado sobre `lineup_stints` (§4) y NO sobre `lineups` — a
# diferencia de `team_lineups`/`_lineup_rows`, que agregan el partido entero
# por combinación de cinco. Trabajar desde los tramos es lo que permite sumar
# `points_for`/`points_against` por separado (para tríos y parejas los tramos
# se recortan igual que en `clutch_lineups`, si algún día hace falta acotar
# por ventana) y no arrastra el `is_inferred` de `lineup_team`: `lineup_stints
# .team_id` es directo, no deducido del equipo actual de los jugadores.

# `{possession_columns}`: `lineup_stints.possessions_for/_against` si ESTA base
# de datos las tiene (columnas aditivas del 2026-09-28, ver
# `ingest/common/possessions.py`), o dos NULL si no — mismo motivo que
# `_table_columns`: nombrarlas sin más falla en una BD sin migrar.
_STINT_PLAYER_ROWS_SQL = """
    SELECT s.id AS stint_id, s.end_seconds - s.start_seconds AS seconds,
           s.points_for, s.points_against, {possession_columns}, g.game_date,
           p.id AS player_id, p.name AS player_name
    FROM lineup_stints s
    JOIN games g ON g.id = s.game_id
    JOIN lineup_stint_players sp ON sp.stint_id = s.id
    JOIN players p ON p.id = sp.player_id
    WHERE s.team_id = :team_id AND g.season_id = :season_id
      AND (:competition_id IS NULL OR g.competition_id = :competition_id)
"""


def _stint_player_rows(
    _engine: Engine, team_id: str, season_id: int, competition_id: Optional[int]
) -> pd.DataFrame:
    """Filas crudas tramo-jugador del equipo: una fila por jugador en cada tramo.

    Incluye `possessions_for`/`possessions_against` del tramo (NULL si la BD no
    las tiene o el partido no tiene tiros tipados todavía).
    """
    if "possessions_for" in _table_columns(_engine, "lineup_stints"):
        possession_columns = "s.possessions_for, s.possessions_against"
    else:
        possession_columns = "NULL AS possessions_for, NULL AS possessions_against"
    return pd.read_sql(
        text(_STINT_PLAYER_ROWS_SQL.format(possession_columns=possession_columns)),
        _engine,
        params={"team_id": team_id, "season_id": season_id, "competition_id": competition_id},
    )


def _player_names(raw: pd.DataFrame) -> "dict[str, str]":
    """`{player_id: player_name}` a partir de las filas crudas, sin duplicados."""
    return dict(raw.drop_duplicates("player_id")[["player_id", "player_name"]].to_numpy())


def _stints_by_id(raw: pd.DataFrame) -> pd.DataFrame:
    """Un tramo por fila: `seconds`, `plus_minus`, `game_date` y el conjunto de jugadores en pista.

    Es el nivel al que se recorre para on/off, para combos y para las señales
    semanales de rotación (propuesta 10) — cada tramo aporta una vez a cada
    jugador/pareja/trío que estuviera en pista, ni más ni menos, sin volver a
    tocar la base de datos. `game_date` viaja para poder partir los tramos en
    "últimos K partidos" contra "el resto" sin una segunda consulta.
    """
    columns = [
        "stint_id", "seconds", "plus_minus", "game_date", "players",
        "points_for", "points_against", "possessions_for", "possessions_against",
    ]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    per_stint = raw.groupby("stint_id").agg(
        seconds=("seconds", "first"),
        points_for=("points_for", "first"),
        points_against=("points_against", "first"),
        possessions_for=("possessions_for", "first"),
        possessions_against=("possessions_against", "first"),
        game_date=("game_date", "first"),
        players=("player_id", frozenset),
    ).reset_index()
    per_stint["plus_minus"] = per_stint["points_for"] - per_stint["points_against"]
    return per_stint[columns]


def _net_rating_per_100(points_for, points_against, possessions_for, possessions_against) -> "tuple[float, float]":
    """`(posesiones, net rating por 100)` de un conjunto de tramos, o `(nan, nan)` sin posesiones.

    Net rating = ORtg − DRtg = 100·puntos/posesiones_propias − 100·encajados/
    posesiones_rival, igual que `game_advanced_stats.net_rating`. Las
    posesiones son ESTIMADAS por tramo (`ingest/common/possessions.py`); los
    cuatro argumentos son sumas sobre los MISMOS tramos (solo los que tienen
    posesiones), para no dividir puntos de unos tramos entre posesiones de otros.
    """
    if not possessions_for or not possessions_against or possessions_for <= 0 or possessions_against <= 0:
        return float("nan"), float("nan")
    net = 100.0 * points_for / possessions_for - 100.0 * points_against / possessions_against
    return (possessions_for + possessions_against) / 2.0, net


def _stints_net_rating(stints: pd.DataFrame) -> "tuple[float, float]":
    """`_net_rating_per_100` sobre los tramos de `stints` que tienen posesiones."""
    if stints.empty:
        return float("nan"), float("nan")
    poss_for = pd.to_numeric(stints["possessions_for"], errors="coerce")
    poss_against = pd.to_numeric(stints["possessions_against"], errors="coerce")
    known = poss_for.notna() & poss_against.notna()
    if not known.any():
        return float("nan"), float("nan")
    return _net_rating_per_100(
        float(stints.loc[known, "points_for"].sum()), float(stints.loc[known, "points_against"].sum()),
        float(poss_for[known].sum()), float(poss_against[known].sum()),
    )


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_on_off(
    _engine: Engine,
    team_id: str,
    season_id: int,
    competition_id: Optional[int] = None,
    min_minutes: float = ON_OFF_MIN_MINUTES,
) -> pd.DataFrame:
    """On/Off de cada jugador que ha pisado la pista con este equipo en la temporada.

    On/Off = (diferencia por 40 en los tramos CON el jugador) − (diferencia
    por 40 en los tramos SIN él), del mismo equipo y temporada (§4). No es una
    medida de calidad del jugador — es contexto: quien comparte pista siempre
    con los mejores sale beneficiado, y al revés (§5). Se devuelven TODOS los
    jugadores, con `reliable` marcando quién llega a `min_minutes` en pista;
    por debajo, la cifra existe pero no debe presentarse como concluyente
    (§4, mínimo no negociable de 200 minutos).

    Returns:
        `player_id, player_name, on_minutes, on_plus_minus, on_per_40,
        off_minutes, off_plus_minus, off_per_40, on_off, on_off_shrunk,
        reliable`. Ordenado por fiabilidad y luego por `on_off_shrunk`
        (encogido con `n / (n + min_minutes)`, igual criterio que la
        propuesta 02). Vacío si el equipo no tiene tramos en la temporada.

        Además, `on_possessions, on_net_100, off_possessions, off_net_100,
        on_off_100`: lo mismo en net rating por 100 posesiones ESTIMADAS
        (capacidad `stint_possessions`), calculado solo sobre los tramos que
        tienen posesiones. NaN si ninguno las tiene (BD sin tiros tipados).
    """
    columns = [
        "player_id", "player_name", "on_minutes", "on_plus_minus", "on_per_40",
        "off_minutes", "off_plus_minus", "off_per_40", "on_off", "on_off_shrunk", "reliable",
        "on_possessions", "on_net_100", "off_possessions", "off_net_100", "on_off_100",
    ]
    raw = _stint_player_rows(_engine, team_id, season_id, competition_id)
    if raw.empty:
        return pd.DataFrame(columns=columns)

    names = _player_names(raw)
    stints = _stints_by_id(raw)

    rows = []
    for player_id, player_name in names.items():
        on_mask = stints["players"].apply(lambda players, pid=player_id: pid in players)
        on, off = stints[on_mask], stints[~on_mask]
        on_minutes, off_minutes = on["seconds"].sum() / 60.0, off["seconds"].sum() / 60.0
        on_pm = int(on["plus_minus"].sum()) if not on.empty else 0
        off_pm = int(off["plus_minus"].sum()) if not off.empty else 0
        on_per_40 = 40.0 * on_pm / on_minutes if on_minutes > 0 else float("nan")
        off_per_40 = 40.0 * off_pm / off_minutes if off_minutes > 0 else float("nan")
        on_off = on_per_40 - off_per_40
        on_poss, on_net_100 = _stints_net_rating(on)
        off_poss, off_net_100 = _stints_net_rating(off)
        rows.append({
            "player_id": player_id,
            "player_name": player_name,
            "on_minutes": on_minutes,
            "on_plus_minus": on_pm,
            "on_per_40": on_per_40,
            "off_minutes": off_minutes,
            "off_plus_minus": off_pm,
            "off_per_40": off_per_40,
            "on_off": on_off,
            "on_off_shrunk": shrink(on_off, on_minutes, k=int(min_minutes)) if on_minutes > 0 else 0.0,
            "reliable": bool(on_minutes >= min_minutes),
            "on_possessions": on_poss,
            "on_net_100": on_net_100,
            "off_possessions": off_poss,
            "off_net_100": off_net_100,
            "on_off_100": on_net_100 - off_net_100,
        })
    df = pd.DataFrame(rows, columns=columns)
    return df.sort_values(["reliable", "on_off_shrunk"], ascending=[False, False]).reset_index(drop=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_combos(
    _engine: Engine,
    team_id: str,
    season_id: int,
    size: int = 2,
    competition_id: Optional[int] = None,
    min_minutes: float = COMBO_MIN_MINUTES,
) -> pd.DataFrame:
    """Todas las combinaciones de `size` jugadores que han coincidido en pista.

    Recorre los tramos y acumula en cada subconjunto (§4): con 5 jugadores
    por tramo son 10 parejas y 10 tríos por tramo, así que no hace falta SQL
    recursivo, con `itertools.combinations` sobre cada tramo basta. Igual que
    `player_on_off`, se devuelven TODAS las combinaciones con `reliable`
    marcando quién llega a `min_minutes` juntos — la matriz de parejas de la
    pantalla filtra por esa marca (§2b), no por un corte silencioso aquí.

    Args:
        size: 2 para parejas, 3 para tríos.

    Returns:
        `player_ids, jugadores, minutes, plus_minus, plus_minus_per_40,
        plus_minus_per_40_shrunk, stints, reliable, possessions,
        net_rating_100`. Ordenado por fiabilidad y luego por
        `plus_minus_per_40_shrunk`, mejor primero. Las dos últimas, igual que
        en `player_on_off`: posesiones estimadas y net rating por 100 sobre
        los tramos con posesiones, NaN si no hay ninguno.
    """
    columns = [
        "player_ids", "jugadores", "minutes", "plus_minus",
        "plus_minus_per_40", "plus_minus_per_40_shrunk", "stints", "reliable",
        "possessions", "net_rating_100",
    ]
    raw = _stint_player_rows(_engine, team_id, season_id, competition_id)
    if raw.empty:
        return pd.DataFrame(columns=columns)

    names = _player_names(raw)
    stints = _stints_by_id(raw)

    accum: "dict[tuple, list]" = {}
    # (puntos a favor, en contra, posesiones a favor, en contra) SOLO de los
    # tramos con posesiones — ver `_net_rating_per_100`.
    per_100: "dict[tuple, list]" = {}
    for row in stints.itertuples(index=False):
        players = sorted(row.players)
        if len(players) < size:
            continue
        has_possessions = pd.notna(row.possessions_for) and pd.notna(row.possessions_against)
        for combo in combinations(players, size):
            entry = accum.setdefault(combo, [0.0, 0, 0])
            entry[0] += row.seconds
            entry[1] += row.plus_minus
            entry[2] += 1
            if has_possessions:
                totals = per_100.setdefault(combo, [0.0, 0.0, 0.0, 0.0])
                totals[0] += row.points_for
                totals[1] += row.points_against
                totals[2] += float(row.possessions_for)
                totals[3] += float(row.possessions_against)

    if not accum:
        return pd.DataFrame(columns=columns)

    rows = []
    for combo, (seconds, plus_minus, n_stints) in accum.items():
        minutes = seconds / 60.0
        possessions, net_100 = _net_rating_per_100(*per_100.get(combo, (0.0, 0.0, 0.0, 0.0)))
        per_40 = 40.0 * plus_minus / minutes if minutes > 0 else float("nan")
        rows.append({
            "player_ids": ",".join(combo),
            "jugadores": " · ".join(names[pid] for pid in combo),
            "minutes": minutes,
            "plus_minus": plus_minus,
            "plus_minus_per_40": per_40,
            "plus_minus_per_40_shrunk": shrink(per_40, minutes, k=int(min_minutes)) if minutes > 0 else 0.0,
            "stints": n_stints,
            "reliable": bool(minutes >= min_minutes),
            "possessions": possessions,
            "net_rating_100": net_100,
        })
    df = pd.DataFrame(rows, columns=columns)
    return df.sort_values(["reliable", "plus_minus_per_40_shrunk"], ascending=[False, False]).reset_index(drop=True)


#: Partidos mínimos a cada lado (recientes / resto) para que la ventana de
#: rotación de la propuesta 10 tenga algo que comparar. Por debajo, ni se
#: calcula: menos tramos que jugadores en pista no da ni una pareja fiable.
PAIR_WINDOW_MIN_GAMES = 3


@st.cache_data(ttl=_TTL, show_spinner=False)
def pair_minutes_by_window(
    _engine: Engine, team_id: str, season_id: int, last_n_games: int = 5, competition_id: Optional[int] = None
) -> pd.DataFrame:
    """Peso de cada pareja de jugadores (parte de los minutos de pista) últimos K partidos vs. el resto.

    Señal de rotación de la propuesta 10 (`10_senales_semanales.md` §2): "una
    pareja... que ha ganado peso". Mismos tramos que `player_combos`, partidos
    por FECHA de partido en dos ventanas — los `last_n_games` partidos más
    recientes con tramos registrados, y el resto de la temporada — y agregados
    por separado a cada lado. El "peso" de una pareja es cuánto de los minutos
    DE TRAMO trackeados en esa ventana coincidieron los dos en pista, no sus
    minutos individuales: una pareja que juega junta la mitad del partido pesa
    50%, jueguen 20 o 35 minutos cada uno.

    A propósito NO reutiliza `player_combos` (que agrega TODA la temporada de
    una vez): aquí hacen falta las dos ventanas por separado, con sus propios
    totales, para poder calcular una diferencia de peso con su propio
    contraste estadístico (`analytics.signals.detect_rotation_signals`).

    Returns:
        `player_ids, jugadores, recent_minutes, recent_share, recent_seconds,
        recent_total_seconds, n_recent_games, baseline_minutes, baseline_share,
        baseline_seconds, baseline_total_seconds, n_baseline_games`. Vacío si
        el equipo no tiene tramos en la temporada, o si no hay
        `PAIR_WINDOW_MIN_GAMES` partidos con tramos a cada lado de la ventana
        (sin `lineup_stints` cargados, `raw` llega vacío igual que en
        `player_on_off`/`player_combos`).
    """
    columns = [
        "player_ids", "jugadores",
        "recent_minutes", "recent_share", "recent_seconds", "recent_total_seconds", "n_recent_games",
        "baseline_minutes", "baseline_share", "baseline_seconds", "baseline_total_seconds", "n_baseline_games",
    ]
    raw = _stint_player_rows(_engine, team_id, season_id, competition_id)
    if raw.empty:
        return pd.DataFrame(columns=columns)

    names = _player_names(raw)
    stints = _stints_by_id(raw)
    game_dates = sorted(stints["game_date"].unique())
    if len(game_dates) < last_n_games + PAIR_WINDOW_MIN_GAMES:
        return pd.DataFrame(columns=columns)

    recent_dates = set(game_dates[-last_n_games:])
    recent = stints[stints["game_date"].isin(recent_dates)]
    baseline = stints[~stints["game_date"].isin(recent_dates)]

    def _combo_seconds(subset: pd.DataFrame) -> "dict[tuple, float]":
        accum: "dict[tuple, float]" = {}
        for row in subset.itertuples(index=False):
            players = sorted(row.players)
            if len(players) < 2:
                continue
            for combo in combinations(players, 2):
                accum[combo] = accum.get(combo, 0.0) + row.seconds
        return accum

    recent_accum = _combo_seconds(recent)
    baseline_accum = _combo_seconds(baseline)
    recent_total = float(recent["seconds"].sum())
    baseline_total = float(baseline["seconds"].sum())
    if recent_total <= 0 or baseline_total <= 0:
        return pd.DataFrame(columns=columns)

    rows = []
    for combo in set(recent_accum) | set(baseline_accum):
        recent_seconds = recent_accum.get(combo, 0.0)
        baseline_seconds = baseline_accum.get(combo, 0.0)
        rows.append({
            "player_ids": ",".join(combo),
            "jugadores": " · ".join(names[pid] for pid in combo),
            "recent_minutes": recent_seconds / 60.0,
            "recent_share": 100.0 * recent_seconds / recent_total,
            "recent_seconds": recent_seconds,
            "recent_total_seconds": recent_total,
            "n_recent_games": len(recent_dates),
            "baseline_minutes": baseline_seconds / 60.0,
            "baseline_share": 100.0 * baseline_seconds / baseline_total,
            "baseline_seconds": baseline_seconds,
            "baseline_total_seconds": baseline_total,
            "n_baseline_games": len(game_dates) - len(recent_dates),
        })
    df = pd.DataFrame(rows, columns=columns)
    return df.sort_values("recent_share", ascending=False).reset_index(drop=True)


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


# ---------- Perfil arbitral (Fase 5, doc/features/propuestas/05_perfil_arbitral.md) ----------
# `game_referees` es ADITIVA (`packages/baskonia_core/db/scouting/engine.py::
# _ADDITIVE_TABLES`): una base de datos ya inicializada la gana vacía hasta
# que se reingiere o se corre `tools/backfill_game_referees.py` sobre lo que
# ya hay — `inspect(_engine).has_table` protege ese caso, mismo criterio que
# `player_advanced_profile`/`player_quarter_profile` un poco más arriba.

# Umbrales de presentación (§4 del documento): por debajo de 15 partidos un
# árbitro no entra en el RANKING (su media se compara mal con la de alguien
# con 40), pero entre 8 y 14 sí se puede enseñar su ficha individual con
# aviso; por debajo de 8, ni eso — demasiado poco para que la media diga algo.
REFEREE_MIN_GAMES_TO_SHOW = 8
REFEREE_MIN_GAMES_FOR_RANKING = 15


@st.cache_data(ttl=_TTL, show_spinner=False)
def referees_for_game(_engine: Engine, game_id: str) -> pd.DataFrame:
    """Terna de un partido, en orden (`game_referees`, ya canonicalizada).

    Vacío si el partido no tiene terna registrada (1 de 737 en
    `data/baskonia.db`, ver el documento §3) o si esta base de datos no tiene
    `game_referees` todavía.
    """
    if not inspect(_engine).has_table("game_referees"):
        return pd.DataFrame(columns=["position", "referee_name"])
    sql = text("""
        SELECT position, referee_name
        FROM game_referees
        WHERE game_id = :game_id
        ORDER BY position
    """)
    return pd.read_sql(sql, _engine, params={"game_id": game_id})


@st.cache_data(ttl=_TTL, show_spinner=False)
def referee_rankings(_engine: Engine, season_id: int) -> pd.DataFrame:
    """Perfil de todos los árbitros con muestra (§4 del documento): faltas y
    tiros libres AJUSTADOS por competición, sesgo local/visitante y ritmo.

    El ajuste por competición es OBLIGATORIO, no opcional (§4): hay casi
    cuatro faltas de diferencia entre ACB y Euroliga (verificado en vivo)
    frente a un rango total entre árbitros de unas diez — sin corregirlo, el
    "perfil" de un árbitro mide sobre todo dónde le designan. El residuo se
    calcula PARTIDO A PARTIDO (faltas del partido - media de SU competición
    esa temporada, sumando ambos equipos) y se promedia por árbitro, así que
    un árbitro que reparte su temporada entre ACB y Euroliga no queda mal
    parado solo por pitar la liga que más se pita.

    Returns:
        `referee_name, gp, pf_avg, fta_avg, pf_residual, fta_residual,
        pf_residual_pct, home_bias_fta, pace_avg, pace_residual,
        sample_size` (`'ok'` con >= `REFEREE_MIN_GAMES_FOR_RANKING` partidos,
        si no `'caution'`), de más a menos pitado (`pf_residual` descendente).
        `pf_residual_pct` es el percentil DENTRO del grupo `'ok'` únicamente
        — compararlo contra árbitros de muestra corta movería el percentil de
        todo el mundo cada vez que llega uno nuevo a mitad de temporada.
        Vacío si esta base de datos no tiene `game_referees` todavía, o si
        ningún árbitro llega al mínimo de partidos para mostrarse.
    """
    if not inspect(_engine).has_table("game_referees"):
        return pd.DataFrame()

    sql = text("""
        WITH team_fouls AS (
            SELECT game_id, team_id, pf, fta
            FROM game_advanced_stats
            WHERE pf IS NOT NULL AND fta IS NOT NULL
        ),
        game_fouls AS (
            SELECT g.id AS game_id, g.competition_id, g.pace,
                   SUM(tf.pf) AS total_pf,
                   SUM(tf.fta) AS total_fta,
                   MAX(CASE WHEN tf.team_id = g.home_team_id THEN tf.fta END) AS home_fta,
                   MAX(CASE WHEN tf.team_id = g.away_team_id THEN tf.fta END) AS away_fta
            FROM games g
            JOIN team_fouls tf ON tf.game_id = g.id
            WHERE g.season_id = :season_id
            GROUP BY g.id
            HAVING COUNT(*) = 2
        ),
        comp_avg AS (
            SELECT competition_id,
                   AVG(total_pf) AS avg_pf,
                   AVG(total_fta) AS avg_fta,
                   AVG(pace) AS avg_pace
            FROM game_fouls
            GROUP BY competition_id
        )
        SELECT gr.referee_name,
               COUNT(*) AS gp,
               AVG(gf.total_pf) AS pf_avg,
               AVG(gf.total_fta) AS fta_avg,
               AVG(gf.total_pf - ca.avg_pf) AS pf_residual,
               AVG(gf.total_fta - ca.avg_fta) AS fta_residual,
               AVG(gf.home_fta - gf.away_fta) AS home_bias_fta,
               AVG(gf.pace) AS pace_avg,
               AVG(gf.pace - ca.avg_pace) AS pace_residual
        FROM game_referees gr
        JOIN game_fouls gf ON gf.game_id = gr.game_id
        JOIN comp_avg ca ON ca.competition_id = gf.competition_id
        GROUP BY gr.referee_name
    """)
    df = pd.read_sql(sql, _engine, params={"season_id": season_id})
    if df.empty:
        return df

    df["sample_size"] = df["gp"].apply(lambda gp: "ok" if gp >= REFEREE_MIN_GAMES_FOR_RANKING else "caution")
    df = df[df["gp"] >= REFEREE_MIN_GAMES_TO_SHOW].copy()
    if df.empty:
        return df

    # Mismo `PERCENT_RANK` que `team_style_percentiles`/`player_percentiles`
    # en `schema.sql` ((posición - 1) / (n - 1)): con solo dos árbitros de
    # muestra plena, el de más faltas tiene que quedar en el percentil 1.0 y
    # el de menos en 0.0, no en 0.5/0.5 (que es lo que daría dividir entre
    # `n` en vez de `n - 1`). Se calcula en pandas y no en SQL porque el
    # tamaño del grupo "ok" es pequeño y variable (depende de cuántos
    # árbitros lleguen al mínimo esta temporada), no una vista fija.
    ranked_pool = df.loc[df["sample_size"] == "ok", "pf_residual"]
    pool_size = len(ranked_pool)
    if pool_size <= 1:
        df["pf_residual_pct"] = pd.NA
    else:
        df["pf_residual_pct"] = df["pf_residual"].apply(lambda v: float((ranked_pool < v).sum()) / (pool_size - 1))
    return df.sort_values("pf_residual", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def referee_profile(_engine: Engine, referee_name: str, season_id: int) -> Optional[dict]:
    """Ficha de UN árbitro (§2a del documento): una fila de `referee_rankings`.

    `referee_name` debe llegar ya canonicalizado — `referees_for_game` ya lo
    devuelve así; un nombre suelto con una variante de acento o apellido
    distinta a la guardada en `game_referees` simplemente no encuentra nada
    (ver `packages/baskonia_core/referees.py`).

    Returns:
        Dict con las columnas de `referee_rankings`, o `None` si el árbitro
        no llega a `REFEREE_MIN_GAMES_TO_SHOW` partidos con datos completos
        en esta temporada — quien pinte esto debe decir "muestra
        insuficiente", nunca ocultar sin más al árbitro que le acaban de
        asignar al próximo partido.
    """
    rankings = referee_rankings(_engine, season_id)
    if rankings.empty:
        return None
    match = rankings[rankings["referee_name"] == referee_name]
    return None if match.empty else match.iloc[0].to_dict()


@st.cache_data(ttl=_TTL, show_spinner=False)
def referee_team_history(_engine: Engine, referee_name: str, season_id: int, team_id: str) -> Optional[dict]:
    """Historial de UN equipo con UN árbitro ("historial con nosotros y con el
    rival", §2a): se llama una vez con el equipo propio y otra con el rival
    del próximo partido — nunca cruzando los dos a la vez, porque "nosotros
    contra este rival con este árbitro" sería una muestra de como mucho un
    par de partidos (§5: correlación gruesa, no afirmaciones finas).

    Returns:
        `gp, wins, losses, pf_avg, pf_drawn_avg, season_pf_avg,
        season_pf_drawn_avg` — las dos últimas de CUALQUIER árbitro en la
        temporada (`team_stats_combined`), para leer "con este árbitro le
        pitan más o menos de lo habitual a este equipo". `None` si el equipo
        no tiene ningún partido con este árbitro en la temporada, o si esta
        base de datos no tiene `game_referees` todavía.
    """
    if not inspect(_engine).has_table("game_referees"):
        return None

    sql = text("""
        SELECT CASE WHEN (g.home_team_id = :team_id AND g.home_score > g.away_score)
                      OR (g.away_team_id = :team_id AND g.away_score > g.home_score)
                    THEN 1 ELSE 0 END AS win,
               gas.pf, gas.pf_drawn
        FROM game_referees gr
        JOIN games g ON g.id = gr.game_id
        LEFT JOIN game_advanced_stats gas ON gas.game_id = g.id AND gas.team_id = :team_id
        WHERE gr.referee_name = :referee_name AND g.season_id = :season_id
          AND (g.home_team_id = :team_id OR g.away_team_id = :team_id)
    """)
    games = pd.read_sql(
        sql, _engine, params={"referee_name": referee_name, "season_id": season_id, "team_id": team_id}
    )
    if games.empty:
        return None

    season_pf_avg = season_pf_drawn_avg = None
    if {"pf_avg", "pf_drawn_avg"} <= _view_columns(_engine, "team_stats_combined"):
        baseline = pd.read_sql(
            text("SELECT pf_avg, pf_drawn_avg FROM team_stats_combined WHERE team_id = :team_id AND season_id = :season_id"),
            _engine,
            params={"team_id": team_id, "season_id": season_id},
        )
        if not baseline.empty:
            season_pf_avg = float(baseline.iloc[0]["pf_avg"]) if pd.notna(baseline.iloc[0]["pf_avg"]) else None
            season_pf_drawn_avg = (
                float(baseline.iloc[0]["pf_drawn_avg"]) if pd.notna(baseline.iloc[0]["pf_drawn_avg"]) else None
            )

    wins = int(games["win"].sum())
    return {
        "gp": int(len(games)),
        "wins": wins,
        "losses": int(len(games)) - wins,
        "pf_avg": float(games["pf"].mean()) if games["pf"].notna().any() else None,
        "pf_drawn_avg": float(games["pf_drawn"].mean()) if games["pf_drawn"].notna().any() else None,
        "season_pf_avg": season_pf_avg,
        "season_pf_drawn_avg": season_pf_drawn_avg,
    }


@st.cache_data(ttl=_TTL, show_spinner=False)
def player_referee_effect(_engine: Engine, player_id: str, referee_name: str, season_id: int) -> Optional[dict]:
    """Faltas por 40 minutos de UN jugador con UN árbitro, frente a su media de
    temporada con cualquier árbitro (§2b: "¿a nuestro 5 le pitan más con
    este árbitro?"). Muestra pequeña A PROPÓSITO (serán 2-5 partidos, §2b/§5
    del documento): se devuelve igual que con cualquier muestra corta en esta
    app — marcado con `gp` para que quien lo pinte muestre el aviso, no que
    lo esconda.

    Returns:
        `gp, pf_per40, season_pf_per40, diff` (`diff = pf_per40 -
        season_pf_per40`), o `None` si el jugador no tiene ningún partido con
        ese árbitro y minutos jugados en la temporada.
    """
    if not inspect(_engine).has_table("game_referees"):
        return None

    with_ref = pd.read_sql(
        text("""
            SELECT pgs.pf, pgs.minutes
            FROM game_referees gr
            JOIN games g ON g.id = gr.game_id
            JOIN player_game_stats pgs ON pgs.game_id = g.id AND pgs.player_id = :player_id
            WHERE gr.referee_name = :referee_name AND g.season_id = :season_id
              AND pgs.pf IS NOT NULL AND pgs.minutes > 0
        """),
        _engine,
        params={"player_id": player_id, "referee_name": referee_name, "season_id": season_id},
    )
    if with_ref.empty:
        return None

    season = pd.read_sql(
        text("""
            SELECT pgs.pf, pgs.minutes
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            WHERE pgs.player_id = :player_id AND g.season_id = :season_id
              AND pgs.pf IS NOT NULL AND pgs.minutes > 0
        """),
        _engine,
        params={"player_id": player_id, "season_id": season_id},
    )

    # Ponderado por minutos (SUM(pf)*40/SUM(minutos)), no la media de las
    # razones de cada partido: un partido de 4 minutos con una falta no puede
    # pesar lo mismo que uno de 30 con tres (mismo criterio que `ft_pct` en
    # `schema.sql`).
    pf_per40 = 40.0 * with_ref["pf"].sum() / with_ref["minutes"].sum()
    season_pf_per40 = 40.0 * season["pf"].sum() / season["minutes"].sum() if not season.empty else None

    return {
        "gp": int(len(with_ref)),
        "pf_per40": round(float(pf_per40), 2),
        "season_pf_per40": round(float(season_pf_per40), 2) if season_pf_per40 is not None else None,
        "diff": round(float(pf_per40 - season_pf_per40), 2) if season_pf_per40 is not None else None,
    }


@st.cache_data(ttl=_TTL, show_spinner=False)
def all_referee_names(_engine: Engine) -> List[str]:
    """Todos los nombres de árbitro conocidos (ya canonicalizados), para un selector.

    A diferencia de `referee_rankings`, SIN umbral de muestra: el cuerpo
    técnico tiene que poder buscar al árbitro que le acaban de asignar aunque
    lleve pocos partidos pitados — `referee_profile` es quien avisa de la
    muestra insuficiente en ese caso, no este selector escondiéndolo.
    """
    if not inspect(_engine).has_table("game_referees"):
        return []
    df = pd.read_sql(text("SELECT DISTINCT referee_name FROM game_referees ORDER BY referee_name"), _engine)
    return df["referee_name"].tolist()


#: Columnas de reparto por cuarto de `foul_profile` — Q1..Q4 fijos y un
#: cajón "OT" para cualquier prórroga: un jugador con faltas en dos
#: prórrogas distintas no necesita dos columnas, solo saber que se cargó en
#: tiempo extra. `foul_drawing_leaders` no lo necesita: esa solo pregunta
#: CUÁNTO se carga, no EN QUÉ CUARTO.
_FOUL_QUARTER_COLUMNS = ["pf_q1_share", "pf_q2_share", "pf_q3_share", "pf_q4_share", "pf_ot_share"]


def _early_foul_events(
    fouls: pd.DataFrame, early_n1: int, early_min1: float, early_n2: int, early_min2: float
) -> pd.DataFrame:
    """Las faltas concretas que disparan "carga temprana", de las dos reglas.

    Args:
        fouls: `game_id, player_id, seconds, foul_no` — todas las
            `foul_personal` de los jugadores en cuestión, ya numeradas por
            orden de aparición dentro de cada partido (ver `foul_profile`).

    Returns:
        El subconjunto de `fouls` que cumple la 1.ª regla (`early_n1` antes
        de `early_min1`) o la 2.ª (`early_n2` antes de `early_min2`) —
        puede tener las dos filas de un mismo (`game_id`, `player_id`) si
        cumple ambas. Vacío si ninguna falta las cumple.
    """
    early1 = fouls[(fouls["foul_no"] == early_n1) & (fouls["seconds"] < early_min1 * 60)]
    early2 = fouls[(fouls["foul_no"] == early_n2) & (fouls["seconds"] < early_min2 * 60)]
    return pd.concat([early1, early2], ignore_index=True)


@st.cache_data(ttl=_TTL, show_spinner=False)
def foul_profile(
    _engine: Engine,
    team_id: str,
    season_id: int,
    early_n1: int = 2,
    early_min1: float = 10.0,
    early_n2: int = 3,
    early_min2: float = 20.0,
) -> pd.DataFrame:
    """Perfil de faltas de la plantilla (§2a de la propuesta 06).

    Tres preguntas del entrenador en una tabla: quién se carga pronto,
    cuánto le cuesta y en qué cuarto acumula. "Carga temprana" NO es una
    constante en el código: son los cuatro parámetros de interfaz que pide
    §4 — por defecto, `early_n1` faltas antes del minuto `early_min1` (2
    antes del 10, todo el primer cuarto) o `early_n2` antes de `early_min2`
    (3 antes del 20, el descanso).

    El coste en minutos se calcula en DOS capas, a propósito (§4):

    - `minutes_lost_avg`: minutos jugados esa noche frente a la media de
      TEMPORADA del jugador (`player_stats_combined.min_avg`) — rápido,
      pero mezcla el efecto de la falta con cualquier otro motivo por el
      que jugara distinto esa noche (una lesión, una paliza ya decidida).
    - `bench_gap_avg_min`: el hueco REAL en `lineup_stints`, desde la falta
      que dispara el aviso hasta el siguiente tramo del jugador en pista (o
      el final del partido si no vuelve a entrar). Es la medida honesta que
      pide §4. `NaN` si esta base de datos no tiene `lineup_stints` (fase
      3) o si ningún partido con carga temprana tiene tramos reconstruidos.

    `bench_margin_per_min`/`team_margin_per_min_season` son la parte (d) de
    la propuesta, LA MÁS DELICADA de presentar: diferencia de puntos del
    EQUIPO durante esos huecos (prorrateada sobre los tramos de
    `lineup_stints` que solapan la ventana, por tiempo de solape — no hay
    forma más fina de partir el marcador sin datos de posesión) frente a su
    diferencia habitual esa temporada (`SUM(points_for - points_against) /
    SUM(minutos)` de TODOS sus tramos, no solo estos). Es **descriptivo, no
    causal**: el rival, el momento y el marcador de esos minutos concretos
    no son comparables sin más con un minuto cualquiera de la temporada.
    Nunca se debe leer como "sentarlo costó X puntos" (§5 de la propuesta).

    Returns:
        Una fila por jugador con boxscore ampliado (`pf`) en `season_id`:
        `player_id, player_name, gp, minutes_avg, pf_total, pf_per40,
        min_2nd_foul_avg, min_3rd_foul_avg, pf_q1_share..pf_q4_share,
        pf_ot_share, early_2_games, early_3_games, early_trouble_games,
        minutes_lost_avg, bench_gap_avg_min, bench_margin_per_min,
        team_margin_per_min_season`. Las columnas que dependen de
        `play_events`/`lineup_stints` salen en `NaN` (o en 0 los contadores
        de partidos) cuando esta base de datos no las tiene todavía — no se
        rellenan con un valor inventado. Vacío si el equipo no tiene ningún
        partido con boxscore ampliado en esa temporada.
    """
    if not inspect(_engine).has_table("play_events"):
        return pd.DataFrame()

    rate_sql = text("""
        SELECT p.id AS player_id, p.name AS player_name,
               SUM(pgs.pf) AS pf_total, SUM(pgs.minutes) AS minutes_total,
               COUNT(pgs.pf) AS gp, AVG(pgs.minutes) AS minutes_avg
        FROM player_game_stats pgs
        JOIN players p ON p.id = pgs.player_id
        JOIN games g ON g.id = pgs.game_id
        WHERE p.team_id = :team_id AND g.season_id = :season_id AND pgs.pf IS NOT NULL
        GROUP BY p.id, p.name
    """)
    profile = pd.read_sql(rate_sql, _engine, params={"team_id": team_id, "season_id": season_id})
    if profile.empty:
        return profile
    profile["pf_per40"] = 40.0 * profile["pf_total"] / profile["minutes_total"].replace(0, pd.NA)

    def _finalize(df: pd.DataFrame) -> pd.DataFrame:
        """Garantiza el esquema completo aunque falten `play_events`/`lineup_stints` a mitad de cálculo."""
        for col in (
            "min_2nd_foul_avg", "min_3rd_foul_avg", *_FOUL_QUARTER_COLUMNS,
            "minutes_lost_avg", "bench_gap_avg_min", "bench_margin_per_min", "team_margin_per_min_season",
        ):
            if col not in df.columns:
                df[col] = pd.NA
        for col in ("early_2_games", "early_3_games", "early_trouble_games"):
            df[col] = df[col].fillna(0).astype(int) if col in df.columns else 0
        return df

    fouls_sql = text("""
        SELECT pe.game_id, pe.player_id, pe.seconds, pe.quarter
        FROM play_events pe
        JOIN players p ON p.id = pe.player_id
        JOIN games g ON g.id = pe.game_id
        WHERE p.team_id = :team_id AND g.season_id = :season_id
          AND pe.event_type = 'foul_personal'
        ORDER BY pe.player_id, pe.game_id, pe.seconds
    """)
    fouls = pd.read_sql(fouls_sql, _engine, params={"team_id": team_id, "season_id": season_id})
    if fouls.empty:
        return _finalize(profile)

    fouls["foul_no"] = fouls.groupby(["game_id", "player_id"]).cumcount() + 1

    # -------------------------------------------- minuto de la 2.ª y 3.ª falta --
    minute_2nd = (
        (fouls[fouls["foul_no"] == 2].groupby("player_id")["seconds"].mean() / 60.0)
        .rename("min_2nd_foul_avg").reset_index()
    )
    minute_3rd = (
        (fouls[fouls["foul_no"] == 3].groupby("player_id")["seconds"].mean() / 60.0)
        .rename("min_3rd_foul_avg").reset_index()
    )

    # ---------------------------------------------------- reparto por cuarto --
    fouls["quarter_bucket"] = fouls["quarter"].where(fouls["quarter"].isin(["Q1", "Q2", "Q3", "Q4"]), "OT")
    by_quarter = fouls.groupby(["player_id", "quarter_bucket"]).size().unstack(fill_value=0)
    by_quarter = by_quarter.reindex(columns=["Q1", "Q2", "Q3", "Q4", "OT"], fill_value=0)
    quarter_totals = by_quarter.sum(axis=1)
    quarter_shares = (by_quarter.div(quarter_totals.replace(0, pd.NA), axis=0) * 100.0)
    quarter_shares.columns = _FOUL_QUARTER_COLUMNS
    quarter_shares = quarter_shares.reset_index()

    # ------------------------------------------------------ carga temprana --
    triggers = _early_foul_events(fouls, early_n1, early_min1, early_n2, early_min2)
    early_n1_hits = fouls[(fouls["foul_no"] == early_n1) & (fouls["seconds"] < early_min1 * 60)]
    early_n2_hits = fouls[(fouls["foul_no"] == early_n2) & (fouls["seconds"] < early_min2 * 60)]
    early_1_games = early_n1_hits.groupby("player_id")["game_id"].nunique().rename("early_2_games").reset_index()
    early_2_games = early_n2_hits.groupby("player_id")["game_id"].nunique().rename("early_3_games").reset_index()

    profile = (
        profile.merge(minute_2nd, on="player_id", how="left")
        .merge(minute_3rd, on="player_id", how="left")
        .merge(quarter_shares, on="player_id", how="left")
        .merge(early_1_games, on="player_id", how="left")
        .merge(early_2_games, on="player_id", how="left")
    )
    for col in ("early_2_games", "early_3_games"):
        profile[col] = profile[col].fillna(0).astype(int)

    if triggers.empty:
        return _finalize(profile)

    early_counts = (
        triggers.drop_duplicates(subset=["game_id", "player_id"])
        .groupby("player_id")["game_id"].nunique()
        .rename("early_trouble_games").reset_index()
    )
    # La falta que dispara el aviso: la más temprana de las dos reglas, por
    # (partido, jugador) — el instante desde el que se mide el coste real.
    trigger_first = (
        triggers.sort_values("seconds")
        .drop_duplicates(subset=["game_id", "player_id"], keep="first")[["game_id", "player_id", "seconds"]]
    )
    profile = profile.merge(early_counts, on="player_id", how="left")
    profile["early_trouble_games"] = profile["early_trouble_games"].fillna(0).astype(int)

    # -------------------------------------------------- minutos perdidos (rápido) --
    game_minutes = pd.read_sql(
        text("""
            SELECT pgs.game_id, pgs.player_id, pgs.minutes
            FROM player_game_stats pgs
            JOIN players p ON p.id = pgs.player_id
            WHERE p.team_id = :team_id
        """),
        _engine, params={"team_id": team_id},
    )
    flagged_minutes = trigger_first.merge(game_minutes, on=["game_id", "player_id"], how="left")
    flagged_minutes = flagged_minutes.merge(profile[["player_id", "minutes_avg"]], on="player_id", how="left")
    flagged_minutes["lost"] = flagged_minutes["minutes_avg"] - flagged_minutes["minutes"]
    minutes_lost_avg = (
        flagged_minutes.groupby("player_id")["lost"].mean().rename("minutes_lost_avg").reset_index()
    )
    profile = profile.merge(minutes_lost_avg, on="player_id", how="left")

    # ---------------------------------------- hueco real y coste de banquillo --
    if not inspect(_engine).has_table("lineup_stints"):
        return _finalize(profile)

    game_ids = trigger_first["game_id"].unique().tolist()
    placeholders = ", ".join(f":g{i}" for i in range(len(game_ids)))
    params = {f"g{i}": gid for i, gid in enumerate(game_ids)}
    params["team_id"] = team_id

    # Tramos del EQUIPO (una fila por tramo, no por jugador) — hacen falta
    # los dos: unidos a `lineup_stint_players` para saber cuándo vuelve a
    # jugar CADA jugador (el hueco), y solos para prorratear el margen del
    # equipo durante ese hueco (§2d).
    team_stints = pd.read_sql(
        text(f"""
            SELECT s.id AS stint_id, s.game_id, s.start_seconds, s.end_seconds,
                   s.points_for, s.points_against
            FROM lineup_stints s
            WHERE s.team_id = :team_id AND s.game_id IN ({placeholders})
            ORDER BY s.game_id, s.start_seconds
        """),
        _engine, params=params,
    )
    player_stints = pd.read_sql(
        text(f"""
            SELECT s.game_id, sp.player_id, s.start_seconds, s.end_seconds
            FROM lineup_stints s
            JOIN lineup_stint_players sp ON sp.stint_id = s.id
            WHERE s.team_id = :team_id AND s.game_id IN ({placeholders})
            ORDER BY sp.player_id, s.game_id, s.start_seconds
        """),
        _engine, params=params,
    )
    if team_stints.empty or player_stints.empty:
        return _finalize(profile)

    game_end = team_stints.groupby("game_id")["end_seconds"].max()

    # El hueco: desde la falta hasta el PRÓXIMO tramo del jugador (si vuelve
    # a entrar) o hasta el final de sus propios tramos en ese partido (si no
    # vuelve) — no hasta el final "oficial" del partido, que puede incluir
    # prórroga que ese partido concreto no tuvo.
    gaps = []
    for row in trigger_first.itertuples():
        own_stints = player_stints[
            (player_stints["game_id"] == row.game_id) & (player_stints["player_id"] == row.player_id)
        ]
        if own_stints.empty:
            continue
        later = own_stints[own_stints["start_seconds"] >= row.seconds]
        gap_end = float(later["start_seconds"].min()) if not later.empty else float(game_end[row.game_id])
        if gap_end <= row.seconds:
            continue
        gaps.append(
            {"player_id": row.player_id, "game_id": row.game_id, "gap_start": row.seconds, "gap_end": gap_end}
        )

    if not gaps:
        return _finalize(profile)

    gaps_df = pd.DataFrame(gaps)
    gaps_df["gap_minutes"] = (gaps_df["gap_end"] - gaps_df["gap_start"]) / 60.0
    bench_gap_avg = gaps_df.groupby("player_id")["gap_minutes"].mean().rename("bench_gap_avg_min").reset_index()
    profile = profile.merge(bench_gap_avg, on="player_id", how="left")

    # Margen del equipo durante cada hueco: los tramos de EQUIPO que solapan
    # la ventana, prorrateados por el tiempo de solape — mismo recorte que
    # `queries.window_lineup`, aplicado a puntos en vez de a jugadores. Es
    # una aproximación (los puntos de un tramo no se reparten uniformemente
    # en el tiempo) y se declara como tal arriba, en el docstring; no hay
    # forma más fina de partir el marcador sin datos de posesión.
    margin_rows = []
    for gap in gaps_df.itertuples():
        overlap = team_stints[
            (team_stints["game_id"] == gap.game_id)
            & (team_stints["end_seconds"] > gap.gap_start)
            & (team_stints["start_seconds"] < gap.gap_end)
        ]
        if overlap.empty:
            continue
        overlap_seconds = (
            overlap["end_seconds"].clip(upper=gap.gap_end) - overlap["start_seconds"].clip(lower=gap.gap_start)
        )
        stint_seconds = overlap["end_seconds"] - overlap["start_seconds"]
        frac = (overlap_seconds / stint_seconds.replace(0, pd.NA)).clip(lower=0, upper=1)
        margin = ((overlap["points_for"] - overlap["points_against"]) * frac).sum()
        margin_rows.append({"player_id": gap.player_id, "margin": margin, "seconds": overlap_seconds.sum()})

    if margin_rows:
        margin_df = pd.DataFrame(margin_rows)
        pooled = (
            margin_df.groupby("player_id").agg(margin=("margin", "sum"), seconds=("seconds", "sum")).reset_index()
        )
        pooled["bench_margin_per_min"] = pooled["margin"] / (pooled["seconds"] / 60.0)
        profile = profile.merge(pooled[["player_id", "bench_margin_per_min"]], on="player_id", how="left")

    baseline = pd.read_sql(
        text("""
            SELECT SUM(s.points_for - s.points_against) AS margin_sum,
                   SUM(s.end_seconds - s.start_seconds) AS seconds_sum
            FROM lineup_stints s
            JOIN games g ON g.id = s.game_id
            WHERE s.team_id = :team_id AND g.season_id = :season_id
        """),
        _engine, params={"team_id": team_id, "season_id": season_id},
    ).iloc[0]
    if baseline["seconds_sum"]:
        profile["team_margin_per_min_season"] = float(baseline["margin_sum"]) / (float(baseline["seconds_sum"]) / 60.0)

    return _finalize(profile)


@st.cache_data(ttl=_TTL, show_spinner=False)
def foul_drawing_leaders(
    _engine: Engine,
    team_id: str,
    season_id: int,
    min_minutes: float = 500.0,
    early_n1: int = 2,
    early_min1: float = 10.0,
    early_n2: int = 3,
    early_min2: float = 20.0,
) -> pd.DataFrame:
    """A quién no ponerle la mano: ranking de un equipo por faltas provocadas (§2b de la propuesta 06).

    Pensada para "Próximo rival" con `team_id` del rival, pero no asume
    nada sobre quién es "propio" — funciona igual para cualquier equipo
    (mismo criterio que el resto de `queries.py`, ver su cabecera).

    Args:
        min_minutes: mínimo de minutos jugados en la temporada para entrar
            en el ranking (§4: `40 · Σ pf_drawn / Σ minutes` con un jugador
            de 40 minutos totales se dispara con una sola falta provocada —
            el mínimo evita que se cuele). 500 es el de los ejemplos de §2,
            no una regla fija.

    Returns:
        `player_id, player_name, gp, minutes_total, pf_drawn_total,
        pf_drawn_per40, fta_total, fta_per40, ft_pct, early_trouble_games,
        early_trouble_rate`, de más a menos `pf_drawn_per40`.
        `early_trouble_rate` (`early_trouble_games / gp`) es la otra mitad
        de la lectura: quién de la plantilla rival está a un aviso de
        sentarse, con la misma regla de "carga temprana" que
        `foul_profile` (mismos parámetros, mismo valor por defecto).

        OJO: no hay `ft_rate` por jugador como en `queries.team_advanced_profile`
        — esa tasa se define sobre tiros de campo intentados (`fga`), que
        `player_game_stats` no guarda por jugador (solo `efg_pct` ya
        calculado). `fta_per40` es la lectura equivalente disponible: cuánto
        se planta en la línea, no relativizado a cuánto tira de campo.
        Vacío si el equipo no tiene boxscore ampliado (`pf_drawn`) en esa
        temporada, o si nadie llega a `min_minutes`.
    """
    if not inspect(_engine).has_table("play_events"):
        return pd.DataFrame()

    rate_sql = text("""
        SELECT p.id AS player_id, p.name AS player_name,
               SUM(pgs.pf_drawn) AS pf_drawn_total, SUM(pgs.minutes) AS minutes_total,
               SUM(pgs.fta) AS fta_total, SUM(pgs.ftm) AS ftm_total,
               COUNT(pgs.pf_drawn) AS gp
        FROM player_game_stats pgs
        JOIN players p ON p.id = pgs.player_id
        JOIN games g ON g.id = pgs.game_id
        WHERE p.team_id = :team_id AND g.season_id = :season_id AND pgs.pf_drawn IS NOT NULL
        GROUP BY p.id, p.name
        HAVING SUM(pgs.minutes) >= :min_minutes
    """)
    leaders = pd.read_sql(
        rate_sql, _engine, params={"team_id": team_id, "season_id": season_id, "min_minutes": min_minutes}
    )
    if leaders.empty:
        return leaders

    leaders["pf_drawn_per40"] = 40.0 * leaders["pf_drawn_total"] / leaders["minutes_total"]
    leaders["fta_per40"] = 40.0 * leaders["fta_total"] / leaders["minutes_total"]
    leaders["ft_pct"] = 100.0 * leaders["ftm_total"] / leaders["fta_total"].replace(0, pd.NA)
    leaders = leaders.drop(columns=["ftm_total"])

    placeholders = ", ".join(f":p{i}" for i in range(len(leaders)))
    fouls_sql = text(f"""
        SELECT pe.game_id, pe.player_id, pe.seconds
        FROM play_events pe
        JOIN players p ON p.id = pe.player_id
        JOIN games g ON g.id = pe.game_id
        WHERE p.team_id = :team_id AND g.season_id = :season_id
          AND pe.event_type = 'foul_personal' AND p.id IN ({placeholders})
        ORDER BY pe.player_id, pe.game_id, pe.seconds
    """)
    params = {"team_id": team_id, "season_id": season_id}
    params.update({f"p{i}": pid for i, pid in enumerate(leaders["player_id"])})
    fouls = pd.read_sql(fouls_sql, _engine, params=params)

    leaders["early_trouble_games"] = 0
    if not fouls.empty:
        fouls["foul_no"] = fouls.groupby(["game_id", "player_id"]).cumcount() + 1
        triggers = _early_foul_events(fouls, early_n1, early_min1, early_n2, early_min2)
        if not triggers.empty:
            early_games = (
                triggers.drop_duplicates(subset=["game_id", "player_id"])
                .groupby("player_id")["game_id"].nunique()
                .rename("early_trouble_games").reset_index()
            )
            leaders = leaders.drop(columns=["early_trouble_games"]).merge(early_games, on="player_id", how="left")
            leaders["early_trouble_games"] = leaders["early_trouble_games"].fillna(0).astype(int)

    leaders["early_trouble_rate"] = leaders["early_trouble_games"] / leaders["gp"].replace(0, pd.NA)

    return leaders.sort_values("pf_drawn_per40", ascending=False).reset_index(drop=True)


def previous_season(_engine: Engine, season_id: int) -> Optional[dict]:
    """La temporada anterior a `season_id` con partidos cargados: `{"id", "label"}` o `None`.

    "Anterior" = la más reciente por AÑO DE INICIO de la etiqueta
    ('2024-2025' → 2024) por debajo de la de `season_id`, no por `id`: el
    `id` es autoincremental (`ingest.common.identity.get_or_create_season`)
    y sigue el orden de INGESTA, así que cargar una temporada histórica
    después de la actual le daría un `id` mayor — y por `id` la temporada
    vieja tomaría de prior la NUEVA (información del futuro) y la actual se
    quedaría sin prior. Si alguna etiqueta no empieza por un año se vuelve
    al orden por `id` (el de `queries.list_seasons`). Se salta una
    temporada sin partidos cargados: existir en `seasons` sin un solo
    partido (el calendario ingerido y nada más) no aporta prior.
    Sin caché propia: `list_seasons` ya está cacheada.
    """
    seasons = queries.list_seasons(_engine)
    if seasons.empty or season_id not in set(seasons["id"].astype(int)):
        return None
    start_year = pd.to_numeric(seasons["label"].astype(str).str.extract(r"^\s*(\d{4})")[0], errors="coerce")
    order = start_year if start_year.notna().all() else seasons["id"]
    seasons = seasons.assign(_order=order.to_numpy())
    current = seasons.loc[seasons["id"].astype(int) == int(season_id), "_order"].iloc[0]
    earlier = seasons[(seasons["_order"] < current) & (seasons["games"] > 0)].sort_values(["_order", "id"])
    if earlier.empty:
        return None
    row = earlier.iloc[-1]
    return {"id": int(row["id"]), "label": str(row["label"])}


@st.cache_data(ttl=_TTL, show_spinner=False)
def season_impact(
    _engine: Engine, season_id: int, competition_id: Optional[int] = None, use_prior: bool = True
) -> dict:
    """RAPM de la temporada (propuesta 12), cacheado por temporada, competición y prior.

    El ajuste es de TODA la liga, no de un equipo: lo comparten la pantalla de
    quintetos, cualquier equipo de su selector y el asistente, así que se
    calcula una vez y se reutiliza.

    Con `use_prior` (por defecto) el RAPM se encoge hacia el de la temporada
    anterior (`previous_season`, misma competición) en vez de hacia 0 — ver
    `impact.fit_rapm`. La temporada anterior se ajusta SIN prior, con esta
    misma función (y por tanto su propia entrada de caché: el ajuste
    anterior se calcula una sola vez aunque se pida con y sin prior la
    actual), para que el prior sea solo un año hacia atrás y no una cadena.
    Sin temporada anterior, o sin tramos en ella, el resultado es idéntico al
    de `use_prior=False`.

    Returns:
        `{"fit": impact.fit_rapm(...), "segments": DataFrame, "names": {player_id: nombre},
        "prior_season": {"id", "label"} | None}` — `prior_season` es la
        temporada que se ha usado de punto de partida (`None` si ninguna).
    """
    rows = queries.season_stint_rows(_engine, season_id, competition_id)
    segments = impact.build_segments(rows)
    names = dict(rows.drop_duplicates("player_id")[["player_id", "player_name"]].to_numpy()) if not rows.empty else {}

    prior, prior_season = None, None
    if use_prior and not segments.empty:
        previous = previous_season(_engine, season_id)
        if previous is not None:
            previous_fit = season_impact(_engine, previous["id"], competition_id, use_prior=False)["fit"]
            prior = impact.prior_from_fit(previous_fit) or None
            prior_season = previous if prior else None
    return {
        "fit": impact.fit_rapm(segments, prior=prior),
        "segments": segments,
        "names": names,
        "prior_season": prior_season,
    }
