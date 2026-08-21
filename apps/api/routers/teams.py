"""Routers de equipos: /teams, /teams/{team_id}, /filters, /summary, /games,
/roster, /rating-trend, /schedule-difficulty, /narrative.

Consume exclusivamente `ScoutingRepository` (métodos de repositorio) y, donde
el repositorio no expone una consulta (listado de equipos, filtros, medias
avanzadas), consulta directa sobre el engine del repositorio. No depende del
modelo viejo (`models.*`/`services.*`).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from packages.baskonia_core.db.scouting.repository import ScoutingRepository
from packages.baskonia_core.errors import TeamNotFound

from .. import mappers
from ..deps import get_repository, get_team_id, season_label_param
from ..schemas.games import GamesResponse
from ..schemas.matchups import NarrativeResponse, ScheduleDifficultyResponse
from ..schemas.players import RosterResponse
from ..schemas.teams import (
    AdvancedSummary,
    CompetitionOption,
    FiltersResponse,
    RatingTrendResponse,
    SummaryResponse,
    TeamRef,
    TeamResponse,
)

router = APIRouter(tags=["teams"])


def _team_row(repo: ScoutingRepository, team_id: str) -> dict | None:
    """Devuelve la fila `teams` de un equipo o None si no existe."""
    with repo._engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, name, is_own_team FROM teams WHERE id = :tid"),
            {"tid": team_id},
        ).mappings().first()
    return dict(row) if row is not None else None


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(
    repo: ScoutingRepository = Depends(get_repository),
) -> list[TeamResponse]:
    """Lista todos los equipos conocidos (id TEXT + nombre + condición de propio)."""
    with repo._engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, is_own_team FROM teams ORDER BY name")
        ).mappings().all()
    return [
        TeamResponse(
            id=row["id"],
            name=row["name"],
            is_own_team=bool(row["is_own_team"]),
        )
        for row in rows
    ]


@router.get("/teams/{team_id}", response_model=TeamResponse)
def get_team_detail(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
) -> TeamResponse:
    """Detalle de un equipo por id TEXT."""
    row = _team_row(repo, team_id)
    if row is None:
        raise TeamNotFound(team_id)
    return TeamResponse(
        id=row["id"],
        name=row["name"],
        is_own_team=bool(row["is_own_team"]),
    )


@router.get("/teams/{team_id}/filters", response_model=FiltersResponse)
def get_filters(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
) -> FiltersResponse:
    """Filtros disponibles para un equipo: temporadas y competiciones."""
    if _team_row(repo, team_id) is None:
        raise TeamNotFound(team_id)
    with repo._engine.connect() as conn:
        seasons = [
            row["label"]
            for row in conn.execute(
                text("SELECT label FROM seasons ORDER BY id")
            ).mappings().all()
        ]
        competitions = [
            {"id": row["id"], "name": row["name"]}
            for row in conn.execute(
                text("SELECT id, name FROM competitions ORDER BY id")
            ).mappings().all()
        ]
    return FiltersResponse(
        seasons=seasons,
        default_season=seasons[-1] if seasons else None,
        competitions=[CompetitionOption(**c) for c in competitions],
    )


def _advanced_summary(
    repo: ScoutingRepository, team_id: str, season_label: str | None
) -> AdvancedSummary:
    """Medias de estadísticas avanzadas de un equipo (four factors + extras).

    Agrega `game_advanced_stats` del equipo (todas las competiciones, o la
    temporada si `season_label` se da). Cada media puede ser None si no hay
    partidos con ese dato.
    """
    params: dict = {"team_id": team_id}
    season_filter = ""
    if season_label is not None:
        season_filter = (
            "AND g.season_id = (SELECT id FROM seasons WHERE label = :season_label)"
        )
        params["season_label"] = season_label
    sql = text(
        f"""
        SELECT
          AVG(gas.ortg) AS avg_ortg,
          AVG(gas.drtg) AS avg_drtg,
          AVG(gas.net_rating) AS avg_net_rating,
          AVG(gas.efg_pct) AS avg_efg_pct,
          AVG(gas.ts_pct) AS avg_ts_pct,
          AVG(gas.tov_pct) AS avg_tov_pct,
          AVG(gas.orb_pct) AS avg_orb_pct,
          AVG(gas.ast_pct) AS avg_ast_pct,
          AVG(gas.stl_pct) AS avg_stl_pct,
          AVG(gas.blk_pct) AS avg_blk_pct,
          AVG(gas.ft_rate) AS avg_ft_rate,
          AVG(gas.ast_to_ratio) AS avg_ast_to_ratio
        FROM game_advanced_stats gas
        JOIN games g ON g.id = gas.game_id
        WHERE gas.team_id = :team_id
        {season_filter}
        """
    )
    with repo._engine.connect() as conn:
        row = conn.execute(sql, params).mappings().first()
    return AdvancedSummary(**dict(row)) if row is not None else AdvancedSummary()


@router.get("/teams/{team_id}/summary", response_model=SummaryResponse)
def get_summary(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    season_label: str | None = Depends(season_label_param),
) -> SummaryResponse:
    """Resumen del equipo: identidad, filtros aplicados y medias avanzadas."""
    row = _team_row(repo, team_id)
    if row is None:
        raise TeamNotFound(team_id)
    games = repo.get_games_for_team(team_id, season_label=season_label)
    played = [g for g in games if g["home_score"] is not None]
    upcoming = repo.get_upcoming_matchups()
    return SummaryResponse(
        team=TeamRef(id=row["id"], name=row["name"]),
        filters={"season_label": season_label},
        advanced=_advanced_summary(repo, team_id, season_label),
        games_played=len(played),
        games_upcoming=len(upcoming),
    )


@router.get("/teams/{team_id}/games", response_model=GamesResponse)
def list_games(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    season_label: str | None = Depends(season_label_param),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> GamesResponse:
    """Partidos de un equipo (jugados y pendientes), paginados."""
    if _team_row(repo, team_id) is None:
        raise TeamNotFound(team_id)
    games = repo.get_games_for_team(team_id, season_label=season_label)
    items = [mappers.game_item(g, team_id) for g in games]
    total = len(items)
    return GamesResponse(
        items=items[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/teams/{team_id}/roster", response_model=RosterResponse)
def get_roster(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    season_label: str | None = Depends(season_label_param),
) -> RosterResponse:
    """Plantilla de un equipo para una temporada (por defecto la última)."""
    if _team_row(repo, team_id) is None:
        raise TeamNotFound(team_id)
    if season_label is None:
        with repo._engine.connect() as conn:
            season_label = conn.execute(
                text("SELECT label FROM seasons ORDER BY id DESC LIMIT 1")
            ).scalar_one_or_none()
    players = repo.get_roster(season_label) if season_label else []
    row = _team_row(repo, team_id)
    return RosterResponse(
        team=TeamRef(id=row["id"], name=row["name"]),
        season_label=season_label,
        players=[mappers.roster_player(p) for p in players],
    )


@router.get("/teams/{team_id}/rating-trend", response_model=RatingTrendResponse)
def get_rating_trend(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    last_n: int = Query(8, ge=1, le=50),
) -> RatingTrendResponse:
    """Tendencia ORtg/DRtg de un equipo en sus últimos N partidos."""
    if _team_row(repo, team_id) is None:
        raise TeamNotFound(team_id)
    rows = repo.get_rating_trend(team_id=team_id, last_n=last_n)
    return RatingTrendResponse(
        team_id=team_id,
        last_n=last_n,
        items=[mappers.rating_trend_item(r) for r in rows],
    )


@router.get("/teams/{team_id}/schedule-difficulty", response_model=ScheduleDifficultyResponse)
def get_schedule_difficulty(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
    next_n: int = Query(5, ge=1, le=20),
) -> ScheduleDifficultyResponse:
    """Dificultad del próximo tramo de calendario (de upcoming_matchups)."""
    if _team_row(repo, team_id) is None:
        raise TeamNotFound(team_id)
    matchups = repo.get_upcoming_matchups()[:next_n]
    opponents = [mappers.difficulty_opponent(m) for m in matchups]
    ratings = [o.predicted_net_rating for o in opponents if o.predicted_net_rating is not None]
    return ScheduleDifficultyResponse(
        games_considered=len(opponents),
        opponents_scouted=sum(1 for o in opponents if o.has_scouting_data),
        avg_opponent_net_rating=(
            round(sum(ratings) / len(ratings), 2) if ratings else None
        ),
        opponents=opponents,
    )


@router.get("/teams/{team_id}/narrative", response_model=NarrativeResponse)
def get_narrative(
    team_id: str = Depends(get_team_id),
    repo: ScoutingRepository = Depends(get_repository),
) -> NarrativeResponse:
    """Narrativa de scouting derivada de los últimos partidos del equipo.

    Si no hay datos suficientes, devuelve `narrative: null` (degradado, no
    inventa contenido).
    """
    if _team_row(repo, team_id) is None:
        raise TeamNotFound(team_id)
    games = repo.get_games_for_team(team_id)
    played = [g for g in games if g["home_score"] is not None]
    if not played:
        return NarrativeResponse(team_id=team_id, narrative=None)
    last = played[-1]
    is_home = last["home_team_id"] == team_id
    team_score = last["home_score"] if is_home else last["away_score"]
    opp_score = last["away_score"] if is_home else last["home_score"]
    result = "victoria" if team_score > opp_score else "derrota"
    opponent = last["away_team_name"] if is_home else last["home_team_name"]
    narrative = (
        f"Último partido ({last['game_date']}): {result} frente a {opponent} "
        f"({team_score}-{opp_score}). {len(played)} partidos registrados."
    )
    return NarrativeResponse(team_id=team_id, narrative=narrative)
