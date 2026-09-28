"""Representación intermedia común (fuente-agnóstica) de un partido cargado.

Los parsers de `ingest.acb` y `ingest.euroleague` transforman su formato
nativo a estas estructuras; `ingest.common.loader` solo sabe volcar esto al
esquema de scouting. Así el upsert idempotente se escribe una sola vez y es
igual para las dos fuentes de partidos.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GameAdvancedStat:
    team_id: str
    efg_pct: float
    ts_pct: float
    tov_pct: float
    orb_pct: float
    ortg: Optional[float] = None
    drtg: Optional[float] = None
    net_rating: Optional[float] = None
    # Extras fieles a 04_team_stats.R de OpenACB (S_assist/S_steal/S_blocks/FT_rate/ast_to_ratio).
    ast_pct: Optional[float] = None
    stl_pct: Optional[float] = None
    blk_pct: Optional[float] = None
    ft_rate: Optional[float] = None
    ast_to_ratio: Optional[float] = None
    # Tiros libres del equipo en bruto (convertidos/intentados). `ft_rate` es
    # una tasa (FTM/FGA) y no deja reconstruir volumen ni acierto desde la
    # línea. `None` = la fuente no dio el dato, distinto de 0.
    ftm: Optional[int] = None
    fta: Optional[int] = None
    # Boxscore ampliado de EQUIPO (Fase 1, ver doc/features/ingestor/
    # 02_plan_stats_completas.md): totales que ambas fuentes ya dan en
    # `stats.total`/`totr`, verificado en vivo. `None` = la fuente no lo dio
    # para este partido, distinto de 0.
    stl: Optional[int] = None
    tov: Optional[int] = None
    blk: Optional[int] = None
    blk_against: Optional[int] = None
    pf: Optional[int] = None
    pf_drawn: Optional[int] = None
    oreb: Optional[int] = None
    dreb: Optional[int] = None
    plus_minus: Optional[int] = None
    pir: Optional[int] = None


@dataclass
class PlayerGameStat:
    player_id: str
    minutes: float
    pts: int
    reb: int
    ast: int
    efg_pct: float
    # Tiros libres convertidos/intentados (`efg_pct` los excluye por
    # definición). `None` = la fuente no dio el dato, distinto de 0.
    ftm: Optional[int] = None
    fta: Optional[int] = None
    # Boxscore ampliado de JUGADOR (Fase 1). `dunks` es ACB-only (Euroliga no
    # lo publica en su boxscore) — siempre `None` para una fila de Euroliga.
    stl: Optional[int] = None
    tov: Optional[int] = None
    blk: Optional[int] = None
    blk_against: Optional[int] = None
    pf: Optional[int] = None
    pf_drawn: Optional[int] = None
    oreb: Optional[int] = None
    dreb: Optional[int] = None
    plus_minus: Optional[int] = None
    pir: Optional[int] = None
    dunks: Optional[int] = None


@dataclass
class LineupRecord:
    player_ids: List[str]
    minutes: float
    plus_minus: int
    # Equipo del quinteto. `None` = la fuente no lo dio y no se pudo
    # reconstruir; aguas abajo se infiere por el equipo ACTUAL de sus
    # jugadores (vista `lineup_team`), que es una aproximación que un traspaso
    # estropea hacia atrás. Ver `lineups.team_id` en `schema.sql`.
    team_id: Optional[str] = None


@dataclass
class StintRecord:
    """Un quinteto continuo en pista, con reloj y marcador.

    Es `LineupRecord` SIN agregar: lo que hace falta para poder recortar una
    ventana de tiempo ("los últimos cinco minutos") y para saber si el partido
    estaba abierto cuando ese quinteto entró (`margin_start`). Ver
    `ingest/common/lineups.py` y la tabla `lineup_stints` de `schema.sql`.
    """

    team_id: str
    player_ids: List[str]
    start_seconds: float
    end_seconds: float
    points_for: int
    points_against: int
    margin_start: int


@dataclass
class ShotRecord:
    player_id: str
    team_id: str
    pos_x: float
    pos_y: float
    made: bool
    zone_id: Optional[int] = None
    # `False` si la fuente NO dio coordenadas para este tiro y `pos_x`/`pos_y`
    # son una posición inferida del tipo de tiro (ver `shots.located` en
    # `schema.sql`). Por defecto `True`: lo normal es que vengan medidas, y
    # así una fuente que no tenga el caso no necesita decir nada.
    located: bool = True
    # Reloj, marcador y contexto (2026-09-28, ver `shots` en `schema.sql`).
    # `None` = la fuente no lo dio para este tiro. `home_score`/`away_score`
    # son los de DESPUÉS del tiro, tal como los dan las dos fuentes.
    quarter: Optional[str] = None
    game_clock: Optional[str] = None
    seconds: Optional[float] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    is_fastbreak: Optional[bool] = None
    is_second_chance: Optional[bool] = None
    is_off_turnover: Optional[bool] = None


@dataclass
class KeyEvent:
    team_id: str
    quarter: str
    game_clock: str
    label: str


@dataclass
class ScoreStep:
    step_index: int
    home_score: int
    away_score: int


@dataclass
class QuarterStat:
    """Puntos anotados/encajados por cuarto (fiel a la parte "quarters" de 09_team_pace.R).

    `fouls_for`/`fouls_against` (Fase 2) NO los rellena el adapter: se
    derivan en `ingest/common/loader.py` agregando `play_events` por
    (game_id, team_id, quarter) DESPUÉS de cargarlos, para que ACB y Euroliga
    compartan una única fuente de verdad aunque el boxscore-por-cuarto de
    Euroliga no dé faltas (ver `doc/features/ingestor/
    02_plan_stats_completas.md` §Fase 2/§Fase 3). `None` hasta que el loader
    los calcula (partido sin play-by-play tipado).
    """

    team_id: str
    quarter: int
    points_for: int
    points_against: int
    fouls_for: Optional[int] = None
    fouls_against: Optional[int] = None


@dataclass
class PlayEvent:
    """Un evento tipado del play-by-play (Fase 2), con reloj exacto.

    `event_type` es uno de: 'steal', 'turnover', 'block', 'oreb', 'dreb',
    'assist', 'foul_drawn', 'foul_personal' y, desde 2026-09-28, 'fg2_made',
    'fg2_missed', 'fg3_made', 'fg3_missed', 'ft_made', 'ft_missed',
    'timeout' (tiempo muerto de EQUIPO, `player_id=None`). Esos nombres son
    contrato compartido con el cálculo de posesiones — no renombrar.
    `event_detail` solo se rellena para 'foul_personal' en ACB (código crudo
    de los 6 subtipos sin semántica distinguible, ver `ingest/acb/adapter.py`)
    y con 'dunk' en los mates de ACB ('fg2_made' del código 100).
    """

    team_id: str
    player_id: Optional[str]
    quarter: str
    game_clock: str
    seconds: float
    event_type: str
    home_score: int
    away_score: int
    event_detail: Optional[str] = None


@dataclass
class PlayerQuarterStat:
    """Boxscore de un jugador en UN cuarto (Fase 3, ACB-only, ver `player_game_quarter_stats`)."""

    player_id: str
    quarter: int
    minutes: Optional[float] = None
    pts: Optional[int] = None
    reb: Optional[int] = None
    ast: Optional[int] = None
    stl: Optional[int] = None
    tov: Optional[int] = None
    blk: Optional[int] = None
    pf: Optional[int] = None
    oreb: Optional[int] = None
    dreb: Optional[int] = None
    ftm: Optional[int] = None
    fta: Optional[int] = None
    plus_minus: Optional[int] = None
    pir: Optional[int] = None


@dataclass
class PlayerAdvancedStat:
    """Estadísticas avanzadas OFICIALES de un jugador en un partido (Fase 4, ACB-only).

    Fiel a `AdvancedStats/player-advanced-stats` (contexto `partido`, no
    `temporada`/`win`/`loss` — esos se piden aparte si hicieran falta). Ver
    `ingest/acb/adapter.py::_player_advanced_stats`.
    """

    player_id: str
    ast_ratio: Optional[float] = None
    ast_pct: Optional[float] = None
    stl_ratio: Optional[float] = None
    stl_pct: Optional[float] = None
    blk_pct: Optional[float] = None
    tov_pct: Optional[float] = None
    orb_pct: Optional[float] = None
    drb_pct: Optional[float] = None
    trb_pct: Optional[float] = None
    ts_pct: Optional[float] = None
    three_par: Optional[float] = None
    ppt: Optional[float] = None
    pp2ps: Optional[float] = None
    pp3ps: Optional[float] = None
    ppft: Optional[float] = None
    possessions: Optional[float] = None
    pace: Optional[float] = None


@dataclass
class NormalizedGame:
    id: str
    season_id: int
    competition_id: int
    home_team_id: str
    away_team_id: str
    game_date: str
    home_score: int
    away_score: int
    pace: float
    narrative: Optional[str] = None
    advanced: List[GameAdvancedStat] = field(default_factory=list)
    boxscore: List[PlayerGameStat] = field(default_factory=list)
    lineups: List[LineupRecord] = field(default_factory=list)
    # Tramos con reloj y marcador (ver `StintRecord`). Vacío si la fuente no
    # trae play-by-play: entonces `lineups` viene de la fuente ya agregado y
    # no hay tramos que reconstruir.
    stints: List[StintRecord] = field(default_factory=list)
    shots: List[ShotRecord] = field(default_factory=list)
    key_events: List[KeyEvent] = field(default_factory=list)
    score_progression: List[ScoreStep] = field(default_factory=list)
    quarter_stats: List[QuarterStat] = field(default_factory=list)
    # Metadata de partido (Fase 3): árbitros, asistencia, pabellón, entrenadores
    # — ya viaja en las respuestas que se descargan hoy en las dos fuentes, ver
    # `games.arena`/etc en `schema.sql`. Todo `None` si la fuente no lo dio.
    arena: Optional[str] = None
    attendance: Optional[int] = None
    referees: Optional[str] = None
    home_coach: Optional[str] = None
    away_coach: Optional[str] = None
    # Play-by-play tipado (Fase 2) y avanzadas oficiales por jugador (Fase 4).
    play_events: List[PlayEvent] = field(default_factory=list)
    quarter_boxscore: List[PlayerQuarterStat] = field(default_factory=list)
    player_advanced: List[PlayerAdvancedStat] = field(default_factory=list)
