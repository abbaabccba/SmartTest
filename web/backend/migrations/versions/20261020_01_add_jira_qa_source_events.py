"""Persist QA source events with their analytics snapshot.

Revision ID: 20261020_01
Revises: 20261016_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '20261020_01'
down_revision = '20261016_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('jira_analytics_qa_events',
        sa.Column('sequence', sa.BigInteger(), primary_key=True, nullable=False, autoincrement=True),
        sa.Column('snapshot_id', sa.String(191), nullable=False),
        sa.Column('issue_id', sa.String(191), nullable=False),
        sa.Column('event_kind', sa.String(16), nullable=False),
        sa.Column('source_id', sa.String(191), nullable=False),
        sa.Column('author_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
        sa.Column('author_account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
        sa.Column('author_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
        sa.Column('created_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
        sa.Column('updated_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
        sa.Column('payload_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
        sa.UniqueConstraint('snapshot_id', 'issue_id', 'event_kind', 'source_id'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['jira_analytics_snapshots.snapshot_id'], ondelete='CASCADE'),
        mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
    )


def downgrade():
    op.drop_table('jira_analytics_qa_events')
