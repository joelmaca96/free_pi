"""Almacenamiento de datos en la base de datos.

Proporciona funciones para insertar/actualizar (upsert) los datos
scrapeados de forma idempotente.
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from .models import BoxScore, Game, Player, PlayerGameLog, SeasonTeamStats, Team, TeamGameStats
from ..stats import effective_fg_pct, true_shooting_pct

logger = logging.getLogger(__name__)


def upsert_team(session: Session, slug: str, name: str, league: str) -> Team:
    """Inserta o actualiza un equipo.

    Args:
        session: Sesión de base de datos.
        slug: Slug del equipo en BBR.
        name: Nombre del equipo.
        league: Liga ('acb' o 'euroleague').

    Returns:
        El objeto Team.
    """
    team = session.query(Team).filter_by(slug=slug).first()
    if team is None:
        team = Team(slug=slug, name=name, league=league)
        session.add(team)
    else:
        team.name = name
        team.league = league
    session.flush()
    return team


def upsert_player(
    session: Session,
    name: str,
    team: Team,
    position: Optional[str] = None,
    number: Optional[str] = None,
    photo_url: Optional[str] = None,
) -> Player:
    """Inserta o actualiza un jugador.

    Args:
        session: Sesión de base de datos.
        name: Nombre del jugador.
        team: Equipo al que pertenece.
        position: Posición (opcional).
        number: Dorsal (opcional).
        photo_url: URL de la foto del jugador (opcional; solo la da la
            plantilla oficial de baskonia.com, no BBR).

    Returns:
        El objeto Player.
    """
    player = session.query(Player).filter_by(name=name, team_id=team.id).first()
    if player is None:
        player = Player(name=name, team_id=team.id, position=position, number=number, photo_url=photo_url)
        session.add(player)
    else:
        player.position = position or player.position
        player.number = number or player.number
        player.photo_url = photo_url or player.photo_url
    session.flush()
    return player


def upsert_game(
    session: Session,
    date: str,
    league: str,
    home_team: Team,
    away_team: Team,
    home_score: Optional[int] = None,
    away_score: Optional[int] = None,
    boxscore_url: Optional[str] = None,
    notes: Optional[str] = None,
    season: Optional[int] = None,
) -> Game:
    """Inserta o actualiza un partido.

    Args:
        session: Sesión de base de datos.
        date: Fecha del partido (ISO).
        league: Liga.
        home_team: Equipo local.
        away_team: Equipo visitante.
        home_score: Puntos del local (opcional).
        away_score: Puntos del visitante (opcional).
        boxscore_url: URL del box score (opcional).
        notes: Nota de BBR sobre el partido, p.ej. "Postponed" (opcional).
        season: Año de inicio de la temporada (p.ej. 2025 para 2025-26), opcional.

    Returns:
        El objeto Game.
    """
    game = (
        session.query(Game)
        .filter_by(date=date, home_team_id=home_team.id, away_team_id=away_team.id)
        .first()
    )
    if game is None:
        game = Game(
            date=date,
            league=league,
            season=season,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            home_score=home_score,
            away_score=away_score,
            boxscore_url=boxscore_url,
            notes=notes,
        )
        session.add(game)
    else:
        # La liga también se actualiza (no solo se fija al crear): un partido
        # persistido con la liga de reserva del equipo de origen debe poder
        # corregirse con la competición real cuando la fuente sí la aporta.
        game.league = league or game.league
        game.season = season or game.season
        game.home_score = home_score or game.home_score
        game.away_score = away_score or game.away_score
        game.boxscore_url = boxscore_url or game.boxscore_url
        game.notes = notes or game.notes
    session.flush()
    return game


def upsert_boxscore(
    session: Session,
    game: Game,
    team: Team,
    player_name: str,
    stats: Dict[str, object],
) -> BoxScore:
    """Inserta o actualiza las estadísticas de un jugador en un partido.

    Args:
        session: Sesión de base de datos.
        game: Partido.
        team: Equipo del jugador.
        player_name: Nombre del jugador.
        stats: Diccionario con las estadísticas.

    Returns:
        El objeto BoxScore.
    """
    box = (
        session.query(BoxScore)
        .filter_by(game_id=game.id, player_name=player_name)
        .first()
    )
    if box is None:
        box = BoxScore(game_id=game.id, team_id=team.id, player_name=player_name)
        session.add(box)

    # Mapear columnas de BBR a campos del modelo
    box.minutes = stats.get("MP") or stats.get("Min") or box.minutes
    box.points = _to_int(stats.get("PTS")) or box.points
    box.rebounds = _to_int(stats.get("TRB") or stats.get("REB")) or box.rebounds
    box.offensive_rebounds = _to_int(stats.get("ORB")) or box.offensive_rebounds
    box.defensive_rebounds = _to_int(stats.get("DRB")) or box.defensive_rebounds
    box.assists = _to_int(stats.get("AST")) or box.assists
    box.steals = _to_int(stats.get("STL")) or box.steals
    box.blocks = _to_int(stats.get("BLK")) or box.blocks
    box.turnovers = _to_int(stats.get("TOV")) or box.turnovers
    box.fg_made = _to_int(stats.get("FG")) or box.fg_made
    box.fg_attempted = _to_int(stats.get("FGA")) or box.fg_attempted
    box.fg3_made = _to_int(stats.get("3P")) or box.fg3_made
    box.fg3_attempted = _to_int(stats.get("3PA")) or box.fg3_attempted
    box.ft_made = _to_int(stats.get("FT")) or box.ft_made
    box.ft_attempted = _to_int(stats.get("FTA")) or box.ft_attempted
    box.plus_minus = _to_float(stats.get("+/-")) or box.plus_minus
    box.personal_fouls = _to_int(stats.get("PF")) or box.personal_fouls
    box.games_started = _to_int(stats.get("GS")) or box.games_started
    box.efg_pct = effective_fg_pct(box.fg_made, box.fg_attempted, box.fg3_made)
    box.ts_pct = true_shooting_pct(box.points, box.fg_attempted, box.ft_attempted)
    session.flush()
    return box


def upsert_team_game_stats(
    session: Session,
    game: Game,
    team: Team,
    ratings: Dict[str, Optional[float]],
) -> TeamGameStats:
    """Inserta o actualiza las estadísticas avanzadas de un equipo en un partido.

    Args:
        session: Sesión de base de datos.
        game: Partido.
        team: Equipo.
        ratings: Diccionario con 'possessions', 'pace', 'off_rating', 'def_rating', 'net_rating'
            (ver `stats.team_game_ratings`).

    Returns:
        El objeto TeamGameStats.
    """
    row = (
        session.query(TeamGameStats)
        .filter_by(game_id=game.id, team_id=team.id)
        .first()
    )
    if row is None:
        row = TeamGameStats(game_id=game.id, team_id=team.id)
        session.add(row)

    row.possessions = ratings.get("possessions")
    row.pace = ratings.get("pace")
    row.off_rating = ratings.get("off_rating")
    row.def_rating = ratings.get("def_rating")
    row.net_rating = ratings.get("net_rating")
    # Totales de equipo por partido (captura completa a nivel de equipo)
    row.team_points = ratings.get("team_points")
    row.team_rebounds = ratings.get("team_rebounds")
    row.team_assists = ratings.get("team_assists")
    row.team_turnovers = ratings.get("team_turnovers")
    row.team_fg_attempted = ratings.get("team_fg_attempted")
    row.team_ft_attempted = ratings.get("team_ft_attempted")
    session.flush()
    return row


def upsert_player_game_log(
    session: Session,
    player: Player,
    game: Game,
    stats: Dict[str, object],
    season: Optional[int] = None,
) -> PlayerGameLog:
    """Inserta o actualiza el game log de un jugador en un partido.

    Args:
        session: Sesión de base de datos.
        player: Jugador.
        game: Partido.
        stats: Diccionario con las estadísticas del jugador en el partido.
        season: Año de inicio de la temporada (p.ej. 2025 para 2025-26), opcional.

    Returns:
        El objeto PlayerGameLog.
    """
    row = (
        session.query(PlayerGameLog)
        .filter_by(player_id=player.id, game_id=game.id)
        .first()
    )
    if row is None:
        row = PlayerGameLog(player_id=player.id, game_id=game.id, season=season)
        session.add(row)
    else:
        row.season = season or row.season

    row.minutes = stats.get("MP") or stats.get("Min") or row.minutes
    row.points = _to_int(stats.get("PTS")) or row.points
    row.rebounds = _to_int(stats.get("TRB") or stats.get("REB")) or row.rebounds
    row.assists = _to_int(stats.get("AST")) or row.assists
    row.steals = _to_int(stats.get("STL")) or row.steals
    row.blocks = _to_int(stats.get("BLK")) or row.blocks
    row.turnovers = _to_int(stats.get("TOV")) or row.turnovers
    row.fg_made = _to_int(stats.get("FG")) or row.fg_made
    row.fg_attempted = _to_int(stats.get("FGA")) or row.fg_attempted
    row.fg3_made = _to_int(stats.get("3P")) or row.fg3_made
    row.fg3_attempted = _to_int(stats.get("3PA")) or row.fg3_attempted
    row.ft_made = _to_int(stats.get("FT")) or row.ft_made
    row.ft_attempted = _to_int(stats.get("FTA")) or row.ft_attempted
    row.plus_minus = _to_float(stats.get("+/-")) or row.plus_minus
    row.efg_pct = effective_fg_pct(row.fg_made, row.fg_attempted, row.fg3_made)
    row.ts_pct = true_shooting_pct(row.points, row.fg_attempted, row.ft_attempted)
    session.flush()
    return row


def upsert_season_team_stats(
    session: Session,
    team: Team,
    season: int,
    stats: Dict[str, object],
) -> SeasonTeamStats:
    """Inserta o actualiza los agregados de un equipo por temporada.

    Args:
        session: Sesión de base de datos.
        team: Equipo.
        season: Año de inicio de la temporada (p.ej. 2025 para 2025-26).
        stats: Diccionario con 'games_played', 'wins', 'losses',
            'points_per_game', 'rebounds_per_game', 'assists_per_game',
            'pace', 'off_rating', 'def_rating', 'net_rating'.

    Returns:
        El objeto SeasonTeamStats.
    """
    row = (
        session.query(SeasonTeamStats)
        .filter_by(team_id=team.id, season=season)
        .first()
    )
    if row is None:
        row = SeasonTeamStats(team_id=team.id, season=season)
        session.add(row)

    row.games_played = stats.get("games_played")
    row.wins = stats.get("wins")
    row.losses = stats.get("losses")
    row.points_per_game = stats.get("points_per_game")
    row.rebounds_per_game = stats.get("rebounds_per_game")
    row.assists_per_game = stats.get("assists_per_game")
    row.pace = stats.get("pace")
    row.off_rating = stats.get("off_rating")
    row.def_rating = stats.get("def_rating")
    row.net_rating = stats.get("net_rating")
    session.flush()
    return row


def _to_int(value) -> Optional[int]:
    """Convierte un valor a entero de forma segura."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _to_float(value) -> Optional[float]:
    """Convierte un valor a float de forma segura."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
