from __future__ import annotations

from sqlalchemy import Column, ForeignKeyConstraint, Index, Integer, String, Table, Text, text
from sqlalchemy.dialects.mysql import LONGTEXT
from ..database import APPLICATION_METADATA

confluence_projects = Table('confluence_projects', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('project_id', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('product_space_key', String(191), nullable=False, server_default=text("''")),
    Column('product_space_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('product_space_url', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('catalog_page_id', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('catalog_page_title', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('catalog_page_url', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('catalog_page_version', Integer, nullable=False, server_default=text('0')),
    Column('status_id', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('status_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('stage_id', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('stage_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('support_mode_id', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('support_mode_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('customer_summary', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('source_revision', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('cached_at', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Index('confluence_projects_space_idx','product_space_key'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

project_current_releases = Table('project_current_releases', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('project_id', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('release_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('launch_time', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('mp_time', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('next_target', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('next_target_date', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('current_hw_stage', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('status_summary', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('source_revision', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('cached_at', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_owners = Table('confluence_project_owners', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('identity', String(191), primary_key=True, nullable=False),
    Column('account', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('display_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_detail_states = Table('confluence_project_detail_states', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('section_name', String(64), primary_key=True, nullable=False),
    Column('state', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column('source_revision', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('error_code', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('has_value', Integer, nullable=False, server_default=text('0')),
    Column('cached_at', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_roles = Table('confluence_project_roles', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('role_id', String(191), primary_key=True, nullable=False),
    Column('role_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_role_people = Table('confluence_project_role_people', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('role_id', String(191), primary_key=True, nullable=False),
    Column('identity', String(191), primary_key=True, nullable=False),
    Column('account', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('display_name', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    ForeignKeyConstraint(['confluence_id', 'role_id'], ['confluence_project_roles.confluence_id', 'confluence_project_roles.role_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_milestones = Table('confluence_project_milestones', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('milestone_key', String(384), primary_key=True, nullable=False),
    Column('milestone_value', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_fields = Table('confluence_project_fields', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('section_name', String(64), primary_key=True, nullable=False),
    Column('field_key', String(384), primary_key=True, nullable=False),
    Column('value_json', Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_project_evidence = Table('confluence_project_evidence', APPLICATION_METADATA,
    Column('confluence_id', String(191), primary_key=True, nullable=False),
    Column('source', String(191), primary_key=True, nullable=False),
    Column('page_id', String(191), primary_key=True, nullable=False),
    Column('page_title', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('page_url', Text().with_variant(LONGTEXT(), "mysql"), nullable=False, server_default=text("('')")),
    Column('page_version', Integer, nullable=False, server_default=text('0')),
    ForeignKeyConstraint(['confluence_id'], ['confluence_projects.confluence_id'], ondelete='CASCADE'),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)

confluence_sync_state = Table('confluence_sync_state', APPLICATION_METADATA,
    Column('scope_key', String(512), primary_key=True, nullable=False),
    Column('cursor', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('last_synced_at', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column('last_error', Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    mysql_charset='utf8mb4', mysql_collate='utf8mb4_0900_bin', mysql_engine='InnoDB',
)
