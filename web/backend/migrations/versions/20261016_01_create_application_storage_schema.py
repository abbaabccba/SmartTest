"""Create application storage schema

Revision ID: 20261016_01
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '20261016_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('audit_email_occurrences',
    sa.Column('account', sa.String(length=128), nullable=False),
    sa.Column('occurrence', sa.String(length=191), nullable=False),
    sa.Column('claimed_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('account', 'occurrence'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('audit_email_runs',
    sa.Column('sequence', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('label', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('state', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('payload', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('sequence'),
    sa.UniqueConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_projects',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('project_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('product_space_key', sa.String(length=191), server_default=sa.text("''"), nullable=False),
    sa.Column('product_space_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('product_space_url', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('catalog_page_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('catalog_page_title', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('catalog_page_url', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('catalog_page_version', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('status_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('status_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('stage_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('stage_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('support_mode_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('support_mode_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('customer_summary', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('source_revision', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('cached_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('confluence_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('confluence_projects_space_idx', 'confluence_projects', ['product_space_key'], unique=False)
    op.create_table('confluence_sync_state',
    sa.Column('scope_key', sa.String(length=512), nullable=False),
    sa.Column('cursor', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('last_synced_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('last_error', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.PrimaryKeyConstraint('scope_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_analytics_queries',
    sa.Column('session_hash', sa.String(length=384), nullable=False),
    sa.Column('account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('active_snapshot_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('pending_snapshot_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('task_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('expires_at', sa.Double(), nullable=False),
    sa.Column('card_key', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('user_conditions_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('task_session_hash', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('last_snapshot_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.PrimaryKeyConstraint('session_hash'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_history_scopes',
    sa.Column('account', sa.String(length=128), nullable=False),
    sa.Column('card_key', sa.String(length=191), nullable=False),
    sa.Column('user_jql', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('account', 'card_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issues',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('issue_key', sa.String(length=191), nullable=False),
    sa.Column('web_url', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('summary', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('project_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('project_key', sa.String(length=191), server_default=sa.text("''"), nullable=False),
    sa.Column('project_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('status_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('status_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('issue_type_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('issue_type_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('priority_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('priority_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('assignee_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('assignee_account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('assignee_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('reporter_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('reporter_account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('reporter_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('created_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('updated_at', sa.String(length=64), nullable=True),
    sa.Column('source_revision', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('cached_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('creator_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('creator_account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('creator_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('resolution_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('resolution_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.PrimaryKeyConstraint('issue_id'),
    sa.UniqueConstraint('issue_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('jira_issues_project_key_idx', 'jira_issues', ['project_key'], unique=False)
    op.create_index('jira_issues_updated_at_idx', 'jira_issues', ['updated_at'], unique=False)
    op.create_table('jira_release_field_metadata',
    sa.Column('field_name', sa.String(length=191), nullable=False),
    sa.Column('field_key', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('cached_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('field_name'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_sync_state',
    sa.Column('scope_key', sa.String(length=512), nullable=False),
    sa.Column('cursor', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('last_synced_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('last_error', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.PrimaryKeyConstraint('scope_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('test_suites',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('owner_username', sa.String(length=128), nullable=False),
    sa.Column('owner_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('name', sa.String(length=512), nullable=False),
    sa.Column('description', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('ordered_nodeids_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('revision', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.Double(), nullable=False),
    sa.Column('updated_at', sa.Double(), nullable=False),
    sa.CheckConstraint("visibility IN ('private','shared')", name='ck_test_suites_visibility'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_username', 'name'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_test_suites_visibility_updated', 'test_suites', ['visibility', 'updated_at'], unique=False)
    op.create_table('user_preferences',
    sa.Column('username', sa.String(length=128), nullable=False),
    sa.Column('scope', sa.String(length=256), nullable=False),
    sa.Column('key', sa.String(length=191), nullable=False),
    sa.Column('value_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('schema_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('updated_at', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('username', 'scope', 'key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('web_account_identities',
    sa.Column('username', sa.String(length=128), nullable=False),
    sa.Column('display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('avatar', sa.LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql'), nullable=True),
    sa.Column('updated_at', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('username'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('web_credentials',
    sa.Column('credential_ref', sa.String(length=72), nullable=False),
    sa.Column('nonce', sa.LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql'), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql'), nullable=False),
    sa.Column('key_version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('credential_ref'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('web_daily_refresh',
    sa.Column('account', sa.String(length=128), nullable=False),
    sa.Column('claimed_slot', sa.Double(), nullable=False),
    sa.Column('running', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_finished', sa.Double(), nullable=True),
    sa.Column('error', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.PrimaryKeyConstraint('account'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('web_query_snapshots',
    sa.Column('session_hash', sa.String(length=384), nullable=False),
    sa.Column('scope', sa.String(length=256), nullable=False),
    sa.Column('filters_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('search', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('project_ids_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('facts_version', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('release_names_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('[]')"), nullable=False),
    sa.Column('jira_cache_version', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('created_at', sa.Double(), nullable=False),
    sa.Column('updated_at', sa.Double(), nullable=False),
    sa.Column('expires_at', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('session_hash', 'scope'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_web_query_snapshots_expiry', 'web_query_snapshots', ['expires_at'], unique=False)
    op.create_table('web_resource_access',
    sa.Column('account', sa.String(length=128), nullable=False),
    sa.Column('platform', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('resource_id', sa.String(length=191), nullable=False),
    sa.Column('capability', sa.String(length=32), nullable=False),
    sa.Column('scope', sa.String(length=224), nullable=False),
    sa.Column('confirmed_at', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('account', 'platform', 'kind', 'resource_id', 'capability', 'scope'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('web_sessions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('username', sa.String(length=128), nullable=False),
    sa.Column('display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('avatar', sa.LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql'), nullable=True),
    sa.Column('created_at', sa.Double(), nullable=False),
    sa.Column('last_seen_at', sa.Double(), nullable=False),
    sa.Column('expires_at', sa.Double(), nullable=False),
    sa.Column('revoked_at', sa.Double(), nullable=True),
    sa.Column('credential_ref', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('revoked_reason', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_web_sessions_user', 'web_sessions', ['username'], unique=False)
    op.create_table('confluence_project_detail_states',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('section_name', sa.String(length=64), nullable=False),
    sa.Column('state', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('source_revision', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('error_code', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('has_value', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('cached_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'section_name'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_project_evidence',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('source', sa.String(length=191), nullable=False),
    sa.Column('page_id', sa.String(length=191), nullable=False),
    sa.Column('page_title', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('page_url', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('page_version', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'source', 'page_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_project_fields',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('section_name', sa.String(length=64), nullable=False),
    sa.Column('field_key', sa.String(length=384), nullable=False),
    sa.Column('value_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'section_name', 'field_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_project_milestones',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('milestone_key', sa.String(length=384), nullable=False),
    sa.Column('milestone_value', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'milestone_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_project_owners',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('identity', sa.String(length=191), nullable=False),
    sa.Column('account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'identity'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_project_roles',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('role_id', sa.String(length=191), nullable=False),
    sa.Column('role_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'role_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_analytics_snapshots',
    sa.Column('sequence', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('snapshot_id', sa.String(length=191), nullable=False),
    sa.Column('session_hash', sa.String(length=384), nullable=False),
    sa.Column('account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('jql', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('basic_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('source_filter_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('state', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('error', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('created_at', sa.Double(), nullable=False),
    sa.Column('user_jql', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('card_key', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('statistics_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('roster_fingerprint', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('comparison_jql', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.ForeignKeyConstraint(['session_hash'], ['jira_analytics_queries.session_hash'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sequence'),
    sa.UniqueConstraint('snapshot_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_attachments',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('attachment_id', sa.String(length=191), nullable=False),
    sa.Column('filename', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('url', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=True),
    sa.Column('author_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('author_account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('author_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'attachment_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_comments',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('comment_id', sa.String(length=191), nullable=False),
    sa.Column('body_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('author_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('author_account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('author_display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('created_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.Column('updated_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=True),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'comment_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_components',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('component_id', sa.String(length=191), nullable=False),
    sa.Column('component_name', sa.String(length=384), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'component_id', 'component_name'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_custom_fields',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('field_key', sa.String(length=384), nullable=False),
    sa.Column('value_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'field_key'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_descriptions',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('content_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_detail_states',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('section_name', sa.String(length=64), nullable=False),
    sa.Column('state', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('source_revision', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('error_code', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('has_value', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('cached_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'section_name'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_fix_versions',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('version_id', sa.String(length=191), server_default=sa.text("''"), nullable=False),
    sa.Column('version_name', sa.String(length=384), server_default=sa.text("''"), nullable=False),
    sa.Column('released', sa.Integer(), nullable=True),
    sa.Column('release_date', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'version_id', 'version_name'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_labels',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('label', sa.String(length=576), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'label'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_links',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('link_id', sa.String(length=191), nullable=False),
    sa.Column('link_type', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('direction', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('target_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('target_key', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('target_web_url', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('target_summary', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id', 'link_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_issue_release_facts',
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('project_business_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('software_release', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('severity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('compare_status', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('qa_assignee_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('manager_identity', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('resolved_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issue_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('project_current_releases',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('project_id', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('release_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('launch_time', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('mp_time', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('next_target', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('next_target_date', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('current_hw_stage', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('status_summary', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('source_revision', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('cached_at', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('confluence_project_role_people',
    sa.Column('confluence_id', sa.String(length=191), nullable=False),
    sa.Column('role_id', sa.String(length=191), nullable=False),
    sa.Column('identity', sa.String(length=191), nullable=False),
    sa.Column('account', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.Column('display_name', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), server_default=sa.text("('')"), nullable=False),
    sa.ForeignKeyConstraint(['confluence_id', 'role_id'], ['confluence_project_roles.confluence_id', 'confluence_project_roles.role_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('confluence_id', 'role_id', 'identity'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_analytics_facts',
    sa.Column('sequence', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('snapshot_id', sa.String(length=191), nullable=False),
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('fact_json', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id'], ['jira_analytics_snapshots.snapshot_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sequence'),
    sa.UniqueConstraint('snapshot_id', 'issue_id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('jira_analytics_snapshot_issues',
    sa.Column('snapshot_id', sa.String(length=191), nullable=False),
    sa.Column('issue_id', sa.String(length=191), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('period_kind', sa.String(length=64), server_default=sa.text("'current'"), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['jira_issues.issue_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['jira_analytics_snapshots.snapshot_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('snapshot_id', 'issue_id', 'period_kind'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_bin',
    mysql_engine='InnoDB'
    )


def downgrade():
    op.drop_table('jira_analytics_snapshot_issues')
    op.drop_table('jira_analytics_facts')
    op.drop_table('confluence_project_role_people')
    op.drop_table('project_current_releases')
    op.drop_table('jira_issue_release_facts')
    op.drop_table('jira_issue_links')
    op.drop_table('jira_issue_labels')
    op.drop_table('jira_issue_fix_versions')
    op.drop_table('jira_issue_detail_states')
    op.drop_table('jira_issue_descriptions')
    op.drop_table('jira_issue_custom_fields')
    op.drop_table('jira_issue_components')
    op.drop_table('jira_issue_comments')
    op.drop_table('jira_issue_attachments')
    op.drop_table('jira_analytics_snapshots')
    op.drop_table('confluence_project_roles')
    op.drop_table('confluence_project_owners')
    op.drop_table('confluence_project_milestones')
    op.drop_table('confluence_project_fields')
    op.drop_table('confluence_project_evidence')
    op.drop_table('confluence_project_detail_states')
    op.drop_index('ix_web_sessions_user', table_name='web_sessions')
    op.drop_table('web_sessions')
    op.drop_table('web_resource_access')
    op.drop_index('ix_web_query_snapshots_expiry', table_name='web_query_snapshots')
    op.drop_table('web_query_snapshots')
    op.drop_table('web_daily_refresh')
    op.drop_table('web_credentials')
    op.drop_table('web_account_identities')
    op.drop_table('user_preferences')
    op.drop_index('ix_test_suites_visibility_updated', table_name='test_suites')
    op.drop_table('test_suites')
    op.drop_table('jira_sync_state')
    op.drop_table('jira_release_field_metadata')
    op.drop_index('jira_issues_updated_at_idx', table_name='jira_issues')
    op.drop_index('jira_issues_project_key_idx', table_name='jira_issues')
    op.drop_table('jira_issues')
    op.drop_table('jira_history_scopes')
    op.drop_table('jira_analytics_queries')
    op.drop_table('confluence_sync_state')
    op.drop_index('confluence_projects_space_idx', table_name='confluence_projects')
    op.drop_table('confluence_projects')
    op.drop_table('audit_email_runs')
    op.drop_table('audit_email_occurrences')
