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


@dataclass
class PlayerGameStat:
    player_id: str
    minutes: float
    pts: int
    reb: int
    ast: int
    efg_pct: float


@dataclass
class LineupRecord:
    player_ids: List[str]
    minutes: float
    plus_minus: int


@dataclass
class ShotRecord:
    player_id: str
    team_id: str
    pos_x: float
    pos_y: float
    made: bool
    zone_id: Optional[int] = None


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
    shots: List[ShotRecord] = field(default_factory=list)
    key_events: List[KeyEvent] = field(default_factory=list)
    score_progression: List[ScoreStep] = field(default_factory=list)
