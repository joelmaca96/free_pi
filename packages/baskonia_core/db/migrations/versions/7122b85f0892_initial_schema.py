"""initial schema

Revision ID: 7122b85f0892
Revises:
Create Date: 2026-08-19 10:57:59.122225

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7122b85f0892'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crea el esquema completo (5 tablas) desde cero.

    En la BD real existente esta revisión NO se ejecuta: se marca como aplicada
    con `alembic stamp head` (el esquema ya existe). Sirve para reproducir el
    esquema en una BD nueva (tests, despliegue) sin depender de
    `models._add_missing_columns()`.
    """
    op.create_table(
        'teams',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('league', sa.String(), nullable=False),
        sa.UniqueConstraint('slug', name='uq_teams_slug'),
    )
    op.create_table(
        'players',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=True),
        sa.Column('position', sa.String(), nullable=True),
        sa.Column('number', sa.String(), nullable=True),
        sa.Column('photo_url', sa.String(), nullable=True),
    )
    op.create_table(
        'games',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('home_team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=True),
        sa.Column('away_team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=True),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('boxscore_url', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.UniqueConstraint('date', 'home_team_id', 'away_team_id', name='uq_game'),
    )
    op.create_table(
        'boxscores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id'), nullable=False),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('player_name', sa.String(), nullable=False),
        sa.Column('minutes', sa.String(), nullable=True),
        sa.Column('points', sa.Integer(), nullable=True),
        sa.Column('rebounds', sa.Integer(), nullable=True),
        sa.Column('offensive_rebounds', sa.Integer(), nullable=True),
        sa.Column('defensive_rebounds', sa.Integer(), nullable=True),
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
        sa.UniqueConstraint('game_id', 'player_name', name='uq_boxscore'),
    )
    op.create_table(
        'team_game_stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id'), nullable=False),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('possessions', sa.Float(), nullable=True),
        sa.Column('pace', sa.Float(), nullable=True),
        sa.Column('off_rating', sa.Float(), nullable=True),
        sa.Column('def_rating', sa.Float(), nullable=True),
        sa.Column('net_rating', sa.Float(), nullable=True),
        sa.UniqueConstraint('game_id', 'team_id', name='uq_team_game_stats'),
    )


def downgrade() -> None:
    """Elimina el esquema (orden inverso por dependencias de FK)."""
    op.drop_table('team_game_stats')
    op.drop_table('boxscores')
    op.drop_table('games')
    op.drop_table('players')
    op.drop_table('teams')
