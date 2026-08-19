"""add season and full stats

Revision ID: a1b2c3d4e5f6
Revises: 7122b85f0892
Create Date: 2026-08-19 12:00:00.000000

Añade la columna `season` a `games`, columnas nuevas a `boxscores` y
`team_game_stats`, y crea las tablas `player_game_logs` y `season_team_stats`
para la captura completa de datos (niveles equipo/jugador/temporada/per-game).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '7122b85f0892'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Aplica los cambios de esquema de forma aditiva (no recrea tablas)."""
    # games.season (nullable: los partidos ya existentes no tienen temporada)
    op.add_column('games', sa.Column('season', sa.Integer(), nullable=True))

    # boxscores: PF y GS
    op.add_column('boxscores', sa.Column('personal_fouls', sa.Integer(), nullable=True))
    op.add_column('boxscores', sa.Column('games_started', sa.Integer(), nullable=True))

    # team_game_stats: totales de equipo por partido
    op.add_column('team_game_stats', sa.Column('team_points', sa.Integer(), nullable=True))
    op.add_column('team_game_stats', sa.Column('team_rebounds', sa.Integer(), nullable=True))
    op.add_column('team_game_stats', sa.Column('team_assists', sa.Integer(), nullable=True))
    op.add_column('team_game_stats', sa.Column('team_turnovers', sa.Integer(), nullable=True))
    op.add_column('team_game_stats', sa.Column('team_fg_attempted', sa.Integer(), nullable=True))
    op.add_column('team_game_stats', sa.Column('team_ft_attempted', sa.Integer(), nullable=True))

    # player_game_logs: game log por jugador/partido
    op.create_table(
        'player_game_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('player_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=False),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id'), nullable=False),
        sa.Column('season', sa.Integer(), nullable=True),
        sa.Column('minutes', sa.String(), nullable=True),
        sa.Column('points', sa.Integer(), nullable=True),
        sa.Column('rebounds', sa.Integer(), nullable=True),
        sa.Column('assists', sa.Integer(), nullable=True),
        sa.Column('steals', sa.Integer(), nullable=True),
        sa.Column('blocks', sa.Integer(), nullable=True),
        sa.Column('turnovers', sa.Integer(), nullable=True),
        sa.Column('fg_made', sa.Integer(), nullable=True),
        sa.Column('fg_attempted', sa.Integer(), nullable=True),
        sa.Column('fg3_made', sa.Integer(), nullable=True),
        sa.Column('fg3_attempted', sa.Integer(), nullable=True),
        sa.Column('ft_made', sa.Integer(), nullable=True),
        sa.Column('ft_attempted', sa.Integer(), nullable=True),
        sa.Column('plus_minus', sa.Float(), nullable=True),
        sa.Column('efg_pct', sa.Float(), nullable=True),
        sa.Column('ts_pct', sa.Float(), nullable=True),
        sa.UniqueConstraint('player_id', 'game_id', name='uq_player_game_log'),
    )

    # season_team_stats: agregados de equipo por temporada
    op.create_table(
        'season_team_stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('games_played', sa.Integer(), nullable=True),
        sa.Column('wins', sa.Integer(), nullable=True),
        sa.Column('losses', sa.Integer(), nullable=True),
        sa.Column('points_per_game', sa.Float(), nullable=True),
        sa.Column('rebounds_per_game', sa.Float(), nullable=True),
        sa.Column('assists_per_game', sa.Float(), nullable=True),
        sa.Column('pace', sa.Float(), nullable=True),
        sa.Column('off_rating', sa.Float(), nullable=True),
        sa.Column('def_rating', sa.Float(), nullable=True),
        sa.Column('net_rating', sa.Float(), nullable=True),
        sa.UniqueConstraint('team_id', 'season', name='uq_season_team_stats'),
    )


def downgrade() -> None:
    """Revierte los cambios (orden inverso por dependencias de FK)."""
    op.drop_table('season_team_stats')
    op.drop_table('player_game_logs')
    op.drop_column('team_game_stats', 'team_ft_attempted')
    op.drop_column('team_game_stats', 'team_fg_attempted')
    op.drop_column('team_game_stats', 'team_turnovers')
    op.drop_column('team_game_stats', 'team_assists')
    op.drop_column('team_game_stats', 'team_rebounds')
    op.drop_column('team_game_stats', 'team_points')
    op.drop_column('boxscores', 'games_started')
    op.drop_column('boxscores', 'personal_fouls')
    op.drop_column('games', 'season')
