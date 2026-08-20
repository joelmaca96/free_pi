"""Mappers dominio → contrato API.

Centraliza la conversión de objetos de dominio (`models.*`, dicts de
`insights.py`/`services`) a los schemas Pydantic del contrato. Aquí viven las
reglas de representación del §4 de `01_design.md`:

- Fecha BBR (`"Thu, Jan 15, 2026"`) → ISO (`"2026-01-15"`).
- Resultado `"88-79"` / `"pendiente"` → `"W"` / `"L"` / `null`.
- Nulos `"-"` / `"n/d"` → `null`.
- Números como números (sin formatear).

La API no formatea: estos mappers solo normalizan la representación, nunca
redondean ni convierten a cadenas de presentación.
"""
from packages.baskonia_core import config
from packages.baskonia_core.dates import parse_bbr_date
from packages.baskonia_core.db import models
from packages.baskonia_core.insights import ZSCORE_COLD_THRESHOLD, ZSCORE_HOT_THRESHOLD

from .schemas import games as games_schemas
from .schemas import jobs as jobs_schemas
from .schemas import matchups as matchups_schemas
from .schemas import players as players_schemas
from .schemas import teams as teams_schemas


def team_ref(team: models.Team) -> teams_schemas.TeamRef:
    """Convierte un `models.Team` a `TeamRef` (slug + nombre para mostrar)."""
    return teams_schemas.TeamRef(
        slug=team.slug,
        name=config.TEAM_DISPLAY_NAMES.get(team.slug, team.name),
    )


def _iso_date(date_str: str | None) -> str | None:
    """Convierte una fecha BBR a ISO-8601, o None si no se puede parsear."""
    dt = parse_bbr_date(date_str) if date_str else None
    return dt.date().isoformat() if dt else None


def _result_label(game: models.Game, team: models.Team) -> str | None:
    """Resultado del partido desde el punto de vista de `team`: W/L/null."""
    is_home = game.home_team_id == team.id
    team_score = game.home_score if is_home else game.away_score
    opp_score = game.away_score if is_home else game.home_score
    if team_score is None or opp_score is None:
        return None
    return "W" if team_score > opp_score else "L"


def game_item(game: models.Game, team: models.Team) -> games_schemas.GameItem:
    """Convierte un `models.Game` a `GameItem` desde el punto de vista de `team`."""
    is_home = game.home_team_id == team.id
    opponent = game.away_team if is_home else game.home_team
    team_score = game.home_score if is_home else game.away_score
    opp_score = game.away_score if is_home else game.home_score
    return games_schemas.GameItem(
        id=game.id,
        date=_iso_date(game.date) or game.date,
        league=game.league,
        is_home=is_home,
        opponent=team_ref(opponent),
        team_score=team_score,
        opponent_score=opp_score,
        result=_result_label(game, team),
        notes=game.notes,
        advanced=None,  # se rellena en el router con _team_stats_for_game
        has_boxscore=game.boxscore_url is not None,
    )


def streak_label(z_score_pts: float | None) -> str:
    """Etiqueta de racha a partir del z-score de PTS (regla de negocio)."""
    if z_score_pts is None:
        return "neutral"
    if z_score_pts >= ZSCORE_HOT_THRESHOLD:
        return "hot"
    if z_score_pts <= ZSCORE_COLD_THRESHOLD:
        return "cold"
    return "neutral"


def streak_item(row: dict) -> players_schemas.StreakItem:
    """Convierte una fila de `player_form_zscore` a `StreakItem`."""
    return players_schemas.StreakItem(
        player_name=row["player_name"],
        games_season=row["games_season"],
        recent_avg_pts=row["recent_avg_pts"],
        season_avg_pts=row["season_avg_pts"],
        season_std_pts=row["season_std_pts"],
        z_score_pts=row["z_score_pts"],
        recent_avg_ts_pct=row["recent_avg_ts_pct"],
        season_avg_ts_pct=row["season_avg_ts_pct"],
        season_std_ts_pct=row["season_std_ts_pct"],
        z_score_ts=row["z_score_ts"],
        label=streak_label(row["z_score_pts"]),
    )


def form_item(row: dict) -> players_schemas.PlayerFormItem:
    """Convierte una fila de `player_recent_form` a `PlayerFormItem`."""
    return players_schemas.PlayerFormItem(
        player_name=row["player_name"],
        games=row["games"],
        avg_minutes=row["avg_minutes"],
        avg_pts=row["avg_pts"],
        avg_pts_per36=row["avg_pts_per36"],
        avg_efg_pct=row["avg_efg_pct"],
        avg_ts_pct=row["avg_ts_pct"],
        avg_plus_minus=row["avg_plus_minus"],
        avg_turnovers=row["avg_turnovers"],
        fg3a_rate=row["fg3a_rate"],
        ft_rate=row["ft_rate"],
    )


def load_item(row: dict) -> players_schemas.LoadItem:
    """Convierte una fila de `player_load` a `LoadItem`."""
    return players_schemas.LoadItem(
        player_name=row["player_name"],
        games=row["games"],
        total_minutes=row["total_minutes"],
        avg_minutes=row["avg_minutes"],
    )


def difficulty_opponent(row: dict) -> matchups_schemas.DifficultyOpponent:
    """Convierte una fila de `schedule_difficulty` a `DifficultyOpponent`."""
    return matchups_schemas.DifficultyOpponent(
        opponent_name=row["opponent_name"],
        date=_iso_date(row["date"]) or row["date"],
        net_rating=row["net_rating"],
    )


def h2h_game(game: models.Game, team: models.Team) -> matchups_schemas.HeadToHeadGame:
    """Convierte un `models.Game` a `HeadToHeadGame` desde el punto de vista de `team`."""
    is_home = game.home_team_id == team.id
    team_score = game.home_score if is_home else game.away_score
    opp_score = game.away_score if is_home else game.home_score
    return matchups_schemas.HeadToHeadGame(
        id=game.id,
        date=_iso_date(game.date) or game.date,
        league=game.league,
        team_score=team_score,
        opponent_score=opp_score,
        result=_result_label(game, team),
    )


def job_ref(job: models.IngestJob) -> jobs_schemas.JobResponse:
    """Convierte un `models.IngestJob` a `JobResponse`."""
    return jobs_schemas.JobResponse(
        id=job.id,
        team=team_ref(job.team),
        last_n=job.last_n,
        status=job.status,
        error=job.error,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )
