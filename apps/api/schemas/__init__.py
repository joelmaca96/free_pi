"""Schemas Pydantic de la API (contrato de respuesta).

Un módulo por router. Los schemas reflejan el contrato del §"Contratos de datos"
de `local/features/012-api-nuevo-modelo-datos/01_design.md`: JSON en
`snake_case`, números como números, fechas ISO-8601 y `null` para ausencia de
dato. La identidad usa `team_id`/`game_id` TEXT (esquema de scouting).
"""
from .discovery import MissingGamesResponse
from .games import (
    BaskoniaBlock,
    BoxScoreResponse,
    BoxScoreRow,
    GameAdvanced,
    GameDetailResponse,
    GameItem,
    GamesResponse,
    KeyEvent,
    Lineup,
    ZoneStat,
)
from .matchups import (
    DifficultyOpponent,
    HeadToHeadGame,
    HeadToHeadResponse,
    NarrativeResponse,
    Projection,
    ProjectionResponse,
    ScheduleDifficultyResponse,
    UpcomingMatchup,
)
from .meta import DataFreshnessResponse, HealthResponse
from .refresh import RefreshResponse
from .players import (
    LoadItem,
    LoadResponse,
    PlayerFormItem,
    PlayerFormResponse,
    RosterPlayer,
    RosterResponse,
)
from .teams import (
    AdvancedSummary,
    CompetitionOption,
    FiltersResponse,
    RatingTrendItem,
    RatingTrendResponse,
    SummaryResponse,
    TeamRef,
    TeamResponse,
)

__all__ = [
    # meta
    "HealthResponse",
    "DataFreshnessResponse",
    # teams
    "TeamRef",
    "TeamResponse",
    "CompetitionOption",
    "FiltersResponse",
    "AdvancedSummary",
    "SummaryResponse",
    "RatingTrendItem",
    "RatingTrendResponse",
    # players
    "RosterPlayer",
    "RosterResponse",
    "PlayerFormItem",
    "PlayerFormResponse",
    "LoadItem",
    "LoadResponse",
    # games
    "GameItem",
    "GamesResponse",
    "BoxScoreRow",
    "BoxScoreResponse",
    "GameAdvanced",
    "GameDetailResponse",
    "BaskoniaBlock",
    "Lineup",
    "ZoneStat",
    "KeyEvent",
    # matchups
    "DifficultyOpponent",
    "ScheduleDifficultyResponse",
    "Projection",
    "ProjectionResponse",
    "HeadToHeadGame",
    "HeadToHeadResponse",
    "UpcomingMatchup",
    "NarrativeResponse",
    # refresco / discovery bajo demanda
    "RefreshResponse",
    "MissingGamesResponse",
]
