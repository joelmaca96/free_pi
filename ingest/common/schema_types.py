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
    """Puntos anotados/encajados por cuarto (fiel a la parte "quarters" de 09_team_pace.R)."""

    team_id: str
    quarter: int
    points_for: int
    points_against: int


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
