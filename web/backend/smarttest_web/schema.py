from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Column, Double, Index, Integer, LargeBinary, String, Table, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT, LONGBLOB
from .database import APPLICATION_METADATA

web_sessions = Table('web_sessions', APPLICATION_METADATA,
    Column('id', BigInteger, primary_key=True, nullable=False, autoincrement=True),
    Column('token_hash', String(64), nullable=False),
    Column('username', String(128), nullable=False),
    Column('display_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('avatar', LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=True),
    Column('created_at', Double(), nullable=False),
    Column('last_seen_at', Double(), nullable=False),
    Column('expires_at', Double(), nullable=False),
    Column('revoked_at', Double(), nullable=True),
    Column('credential_ref', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('revoked_reason', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Index('ix_web_sessions_user','username'),
    UniqueConstraint('token_hash'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

web_account_identities = Table('web_account_identities', APPLICATION_METADATA,
    Column('username', String(128), primary_key=True, nullable=False),
    Column('display_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('avatar', LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=True),
    Column('updated_at', Double(), nullable=False),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

user_preferences = Table('user_preferences', APPLICATION_METADATA,
    Column('username', String(128), primary_key=True, nullable=False),
    Column('scope', String(256), primary_key=True, nullable=False),
    Column('key', String(191), primary_key=True, nullable=False),
    Column('value_json', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('schema_version', Integer, nullable=False, server_default=text('1')),
    Column('updated_at', Double(), nullable=False),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

web_query_snapshots = Table('web_query_snapshots', APPLICATION_METADATA,
    Column('session_hash', String(384), primary_key=True, nullable=False),
    Column('scope', String(256), primary_key=True, nullable=False),
    Column('filters_json', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('search', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('project_ids_json', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('facts_version', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('release_names_json', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('[]')")),
    Column('jira_cache_version', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('created_at', Double(), nullable=False),
    Column('updated_at', Double(), nullable=False),
    Column('expires_at', Double(), nullable=False),
    Index('ix_web_query_snapshots_expiry','expires_at'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

web_credentials = Table('web_credentials', APPLICATION_METADATA,
    Column('credential_ref', String(72), primary_key=True, nullable=False),
    Column('nonce', LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=False),
    Column('ciphertext', LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=False),
    Column('key_version', Integer, nullable=False),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

web_resource_access = Table('web_resource_access', APPLICATION_METADATA,
    Column('account', String(128), primary_key=True, nullable=False),
    Column('platform', String(160), primary_key=True, nullable=False),
    Column('kind', String(32), primary_key=True, nullable=False),
    Column('resource_id', String(191), primary_key=True, nullable=False),
    Column('capability', String(32), primary_key=True, nullable=False),
    Column('scope', String(224), primary_key=True, nullable=False),
    Column('confirmed_at', Double(), nullable=False),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

test_suites = Table('test_suites', APPLICATION_METADATA,
    Column('id', String(64), primary_key=True, nullable=False),
    Column('owner_username', String(128), nullable=False),
    Column('owner_display_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('name', String(512), nullable=False),
    Column('description', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('visibility', String(16), nullable=False),
    Column('ordered_nodeids_json', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('revision', Integer, nullable=False, server_default=text('1')),
    Column('created_at', Double(), nullable=False),
    Column('updated_at', Double(), nullable=False),
    Index('ix_test_suites_visibility_updated','visibility','updated_at'),
    UniqueConstraint('owner_username','name'),
    CheckConstraint("visibility IN ('private','shared')", name="ck_test_suites_visibility"),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

audit_email_runs = Table('audit_email_runs', APPLICATION_METADATA,
    Column('sequence', BigInteger, primary_key=True, nullable=False, autoincrement=True),
    Column('id', String(64), nullable=False),
    Column('account', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('label', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('state', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('payload', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    UniqueConstraint('id'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

audit_email_occurrences = Table('audit_email_occurrences', APPLICATION_METADATA,
    Column('account', String(128), primary_key=True, nullable=False),
    Column('occurrence', String(191), primary_key=True, nullable=False),
    Column('claimed_at', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

web_daily_refresh = Table('web_daily_refresh', APPLICATION_METADATA,
    Column('account', String(128), primary_key=True, nullable=False),
    Column('claimed_slot', Double(), nullable=False),
    Column('running', Integer, nullable=False, server_default=text('0')),
    Column('last_finished', Double(), nullable=True),
    Column('error', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)


CURRENT_SCHEMA_REVISION = '20261020_01'


def application_metadata():
    from .jira import schema as jira_schema
    from .confluence import schema as confluence_schema
    return APPLICATION_METADATA


def verify_schema_revision(database):
    with database.connect() as connection:
        version = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one_or_none()
    if version != CURRENT_SCHEMA_REVISION:
        raise RuntimeError('Application database schema upgrade is required')
