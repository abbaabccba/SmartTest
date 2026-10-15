from __future__ import annotations

from ..database import WebDatabase
from ..schema import ensure_component_schema


JIRA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS jira_history_scopes (
        account TEXT NOT NULL, card_key TEXT NOT NULL, user_jql TEXT NOT NULL,
        PRIMARY KEY(account,card_key))""",
    """CREATE TABLE IF NOT EXISTS jira_analytics_queries (
        session_hash TEXT PRIMARY KEY, account TEXT NOT NULL,
        active_snapshot_id TEXT NOT NULL DEFAULT '', pending_snapshot_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '', expires_at REAL NOT NULL,
        card_key TEXT NOT NULL DEFAULT '', user_conditions_json TEXT,
        task_session_hash TEXT NOT NULL DEFAULT '', last_snapshot_id TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS jira_analytics_snapshots (
        snapshot_id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, account TEXT NOT NULL,
        jql TEXT NOT NULL, basic_json TEXT NOT NULL, source_filter_id TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, user_jql TEXT,
        card_key TEXT NOT NULL DEFAULT '', statistics_json TEXT,
        roster_fingerprint TEXT NOT NULL DEFAULT '', comparison_jql TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(session_hash) REFERENCES jira_analytics_queries(session_hash) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS jira_analytics_snapshot_issues (
        snapshot_id TEXT NOT NULL REFERENCES jira_analytics_snapshots(snapshot_id) ON DELETE CASCADE,
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, period_kind TEXT NOT NULL DEFAULT 'current', PRIMARY KEY(snapshot_id,issue_id,period_kind)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_analytics_facts (
        snapshot_id TEXT NOT NULL REFERENCES jira_analytics_snapshots(snapshot_id) ON DELETE CASCADE,
        issue_id TEXT NOT NULL, fact_json TEXT NOT NULL, PRIMARY KEY(snapshot_id,issue_id)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issues (
        issue_id TEXT PRIMARY KEY, issue_key TEXT NOT NULL UNIQUE,
        web_url TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL DEFAULT '', project_key TEXT NOT NULL DEFAULT '',
        project_name TEXT NOT NULL DEFAULT '', status_id TEXT NOT NULL DEFAULT '',
        status_name TEXT NOT NULL DEFAULT '', issue_type_id TEXT NOT NULL DEFAULT '',
        issue_type_name TEXT NOT NULL DEFAULT '', priority_id TEXT, priority_name TEXT,
        assignee_identity TEXT, assignee_account TEXT, assignee_display_name TEXT,
        reporter_identity TEXT, reporter_account TEXT, reporter_display_name TEXT,
        created_at TEXT, updated_at TEXT, source_revision TEXT NOT NULL DEFAULT '',
        cached_at TEXT NOT NULL,
        creator_identity TEXT, creator_account TEXT, creator_display_name TEXT,
        resolution_id TEXT, resolution_name TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS jira_issues_project_key_idx ON jira_issues(project_key)",
    "CREATE INDEX IF NOT EXISTS jira_issues_updated_at_idx ON jira_issues(updated_at)",
    """CREATE TABLE IF NOT EXISTS jira_issue_release_facts (
        issue_id TEXT PRIMARY KEY REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        project_business_id TEXT NOT NULL DEFAULT '', software_release TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT '', compare_status TEXT NOT NULL DEFAULT '',
        qa_assignee_identity TEXT NOT NULL DEFAULT '', manager_identity TEXT NOT NULL DEFAULT '',
        resolved_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_fix_versions (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        version_id TEXT NOT NULL DEFAULT '', version_name TEXT NOT NULL DEFAULT '',
        released INTEGER, release_date TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(issue_id,version_id,version_name)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_release_field_metadata (
        field_name TEXT PRIMARY KEY, field_key TEXT NOT NULL,
        cached_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_labels (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        label TEXT NOT NULL, PRIMARY KEY(issue_id,label)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_components (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        component_id TEXT NOT NULL, component_name TEXT NOT NULL,
        PRIMARY KEY(issue_id,component_id,component_name)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_detail_states (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        section_name TEXT NOT NULL, state TEXT NOT NULL,
        source_revision TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
        has_value INTEGER NOT NULL DEFAULT 0, cached_at TEXT NOT NULL,
        PRIMARY KEY(issue_id,section_name)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_descriptions (
        issue_id TEXT PRIMARY KEY REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        content_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_comments (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        comment_id TEXT NOT NULL, body_json TEXT NOT NULL,
        author_identity TEXT, author_account TEXT, author_display_name TEXT,
        created_at TEXT, updated_at TEXT, PRIMARY KEY(issue_id,comment_id)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_attachments (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        attachment_id TEXT NOT NULL, filename TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
        size INTEGER, author_identity TEXT, author_account TEXT,
        author_display_name TEXT, PRIMARY KEY(issue_id,attachment_id)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_links (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        link_id TEXT NOT NULL, link_type TEXT NOT NULL, direction TEXT NOT NULL,
        target_id TEXT NOT NULL DEFAULT '', target_key TEXT NOT NULL DEFAULT '',
        target_web_url TEXT NOT NULL DEFAULT '', target_summary TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(issue_id,link_id)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_issue_custom_fields (
        issue_id TEXT NOT NULL REFERENCES jira_issues(issue_id) ON DELETE CASCADE,
        field_key TEXT NOT NULL, value_json TEXT NOT NULL,
        PRIMARY KEY(issue_id,field_key)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_sync_state (
        scope_key TEXT PRIMARY KEY, cursor TEXT, last_synced_at TEXT, last_error TEXT
    )""",
)


def initialize_jira_schema(database: WebDatabase) -> None:
    ensure_component_schema(
        database,
        component="jira_cache",
        version=7,
        statements=JIRA_STATEMENTS,
        upgrade_statements=("DROP TABLE IF EXISTS jira_history_schedule",),
    )
    with database.transaction() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jira_issue_release_facts)")}
        if "resolved_at" not in columns:
            connection.execute("ALTER TABLE jira_issue_release_facts ADD COLUMN resolved_at TEXT NOT NULL DEFAULT ''")
        analytics_columns = {row[1] for row in connection.execute("PRAGMA table_info(jira_analytics_snapshots)")}
        if "user_jql" not in analytics_columns:
            connection.execute("ALTER TABLE jira_analytics_snapshots ADD COLUMN user_jql TEXT")
        if "card_key" not in analytics_columns:
            connection.execute("ALTER TABLE jira_analytics_snapshots ADD COLUMN card_key TEXT NOT NULL DEFAULT ''")
        for column, definition in (("statistics_json", "TEXT"), ("roster_fingerprint", "TEXT NOT NULL DEFAULT ''")):
            if column not in analytics_columns:
                connection.execute(f"ALTER TABLE jira_analytics_snapshots ADD COLUMN {column} {definition}")
        if "comparison_jql" not in analytics_columns:
            connection.execute("ALTER TABLE jira_analytics_snapshots ADD COLUMN comparison_jql TEXT NOT NULL DEFAULT ''")
        membership_columns = {row[1] for row in connection.execute("PRAGMA table_info(jira_analytics_snapshot_issues)")}
        if "period_kind" not in membership_columns:
            connection.execute("ALTER TABLE jira_analytics_snapshot_issues ADD COLUMN period_kind TEXT NOT NULL DEFAULT 'current'")
        query_columns = {row[1] for row in connection.execute("PRAGMA table_info(jira_analytics_queries)")}
        if "card_key" not in query_columns:
            connection.execute("ALTER TABLE jira_analytics_queries ADD COLUMN card_key TEXT NOT NULL DEFAULT ''")
        if "user_conditions_json" not in query_columns:
            connection.execute("ALTER TABLE jira_analytics_queries ADD COLUMN user_conditions_json TEXT")
        if "task_session_hash" not in query_columns:
            connection.execute("ALTER TABLE jira_analytics_queries ADD COLUMN task_session_hash TEXT NOT NULL DEFAULT ''")
        if "last_snapshot_id" not in query_columns:
            connection.execute("ALTER TABLE jira_analytics_queries ADD COLUMN last_snapshot_id TEXT NOT NULL DEFAULT ''")
