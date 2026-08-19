"""Modelos de base de datos (SQLAlchemy).

Define el esquema para almacenar equipos, jugadores, partidos y
estadísticas de box score. Diseñado para el caso de uso de scouting
de enfrentamientos (p.ej. Baskonia vs Bilbao Basket).
"""
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .. import config

Base = declarative_base()


class Team(Base):
    """Equipo de baloncesto."""

    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)  # slug de BBR
    name = Column(String, nullable=False)
    league = Column(String, nullable=False)  # 'acb' o 'euroleague'

    players = relationship("Player", back_populates="team")


class Player(Base):
    """Jugador de baloncesto."""

    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"))
    position = Column(String, nullable=True)
    number = Column(String, nullable=True)
    # Solo la rellena la plantilla oficial de baskonia.com (ver
    # scraper/baskonia_official.py); un jugador solo capturado vía BBR
    # (temporadas pasadas, ya no en el equipo) se queda sin foto.
    photo_url = Column(String, nullable=True)

    team = relationship("Team", back_populates="players")


class Game(Base):
    """Partido de baloncesto."""

    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("date", "home_team_id", "away_team_id", name="uq_game"),)

    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)  # fecha en formato ISO
    league = Column(String, nullable=False)
    season = Column(Integer, nullable=True)  # año de finalización de la temporada (p.ej. 2026)
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    boxscore_url = Column(String, nullable=True)
    notes = Column(String, nullable=True)  # p.ej. "Postponed": BBR deja la fila sin resultado para siempre

    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])


class BoxScore(Base):
    """Estadísticas de un jugador en un partido."""

    __tablename__ = "boxscores"
    __table_args__ = (UniqueConstraint("game_id", "player_name", name="uq_boxscore"),)

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    player_name = Column(String, nullable=False)
    minutes = Column(String, nullable=True)
    points = Column(Integer, nullable=True)
    rebounds = Column(Integer, nullable=True)
    offensive_rebounds = Column(Integer, nullable=True)
    defensive_rebounds = Column(Integer, nullable=True)
    assists = Column(Integer, nullable=True)
    steals = Column(Integer, nullable=True)
    blocks = Column(Integer, nullable=True)
    turnovers = Column(Integer, nullable=True)
    fg_made = Column(Integer, nullable=True)
    fg_attempted = Column(Integer, nullable=True)
    fg3_made = Column(Integer, nullable=True)
    fg3_attempted = Column(Integer, nullable=True)
    ft_made = Column(Integer, nullable=True)
    ft_attempted = Column(Integer, nullable=True)
    plus_minus = Column(Float, nullable=True)
    efg_pct = Column(Float, nullable=True)  # effective field goal %
    ts_pct = Column(Float, nullable=True)  # true shooting %
    personal_fouls = Column(Integer, nullable=True)  # PF
    games_started = Column(Integer, nullable=True)  # GS (1 si titular, 0 si no)

    game = relationship("Game")
    team = relationship("Team")


class TeamGameStats(Base):
    """Estadísticas avanzadas de un equipo en un partido (pace, ratings)."""

    __tablename__ = "team_game_stats"
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_team_game_stats"),)

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    possessions = Column(Float, nullable=True)
    pace = Column(Float, nullable=True)
    off_rating = Column(Float, nullable=True)
    def_rating = Column(Float, nullable=True)
    net_rating = Column(Float, nullable=True)
    # Totales de equipo por partido (para captura completa a nivel de equipo)
    team_points = Column(Integer, nullable=True)
    team_rebounds = Column(Integer, nullable=True)
    team_assists = Column(Integer, nullable=True)
    team_turnovers = Column(Integer, nullable=True)
    team_fg_attempted = Column(Integer, nullable=True)
    team_ft_attempted = Column(Integer, nullable=True)

    game = relationship("Game")
    team = relationship("Team")


class PlayerGameLog(Base):
    """Game log de un jugador en un partido (nivel jugador/temporada).

    Complementa `BoxScore` con el registro por partido de un jugador a lo
    largo de una temporada, incluyendo minutos, puntos, rebotes, asistencias,
    robos, tapones, pérdidas, tiros y métricas de eficiencia. Se usa para
    reconstruir la evolución de un jugador y los agregados de temporada.
    """

    __tablename__ = "player_game_logs"
    __table_args__ = (UniqueConstraint("player_id", "game_id", name="uq_player_game_log"),)

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    season = Column(Integer, nullable=True)
    minutes = Column(String, nullable=True)
    points = Column(Integer, nullable=True)
    rebounds = Column(Integer, nullable=True)
    assists = Column(Integer, nullable=True)
    steals = Column(Integer, nullable=True)
    blocks = Column(Integer, nullable=True)
    turnovers = Column(Integer, nullable=True)
    fg_made = Column(Integer, nullable=True)
    fg_attempted = Column(Integer, nullable=True)
    fg3_made = Column(Integer, nullable=True)
    fg3_attempted = Column(Integer, nullable=True)
    ft_made = Column(Integer, nullable=True)
    ft_attempted = Column(Integer, nullable=True)
    plus_minus = Column(Float, nullable=True)
    efg_pct = Column(Float, nullable=True)
    ts_pct = Column(Float, nullable=True)

    player = relationship("Player")
    game = relationship("Game")


class SeasonTeamStats(Base):
    """Agregados de un equipo por temporada (nivel temporada)."""

    __tablename__ = "season_team_stats"
    __table_args__ = (UniqueConstraint("team_id", "season", name="uq_season_team_stats"),)

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    season = Column(Integer, nullable=False)
    games_played = Column(Integer, nullable=True)
    wins = Column(Integer, nullable=True)
    losses = Column(Integer, nullable=True)
    points_per_game = Column(Float, nullable=True)
    rebounds_per_game = Column(Float, nullable=True)
    assists_per_game = Column(Float, nullable=True)
    pace = Column(Float, nullable=True)
    off_rating = Column(Float, nullable=True)
    def_rating = Column(Float, nullable=True)
    net_rating = Column(Float, nullable=True)

    team = relationship("Team")


def init_db() -> sessionmaker:
    """Inicializa la base de datos y devuelve una fábrica de sesiones.

    Returns:
        Una sessionmaker configurada.
    """
    engine = create_engine(config.DATABASE_URL)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return sessionmaker(bind=engine)


def _add_missing_columns(engine) -> None:
    """Añade columnas nuevas a tablas ya existentes (SQLite no migra solo).

    `create_all` solo crea tablas que no existen; si el esquema de un modelo
    gana columnas nuevas, las bases de datos ya creadas antes se quedan
    desactualizadas. Esta función compara el esquema real con el de los
    modelos y añade con `ALTER TABLE ... ADD COLUMN` lo que falte.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))

