from __future__ import annotations

from collections.abc import Iterable
import json

from .database import WebDatabase
from core.product_lines import DASHBOARD_PRODUCT_LINES, product_line_name


_DASHBOARD_JIRA_TYPE_MIGRATIONS = {
    "jira-team-bugs": ("jira-statistics-self-test", "self-test"),
    "jira-customer-statistics": ("jira-statistics-customer-feedback", "customer-feedback"),
}


def _migrate_confluence_product_lines(database):
    with database.transaction() as connection:
        if connection.execute("SELECT 1 FROM smarttest_schema WHERE component='confluence_product_line_names' AND version=2").fetchone():
            return
        for line in DASHBOARD_PRODUCT_LINES:
            for old in (line.confluence_space_key, line.name + ' Business'):
                if old == line.name:
                    continue
                for column in ('product_space_key', 'product_space_name'):
                    connection.execute(f'UPDATE confluence_projects SET {column}=? WHERE {column}=?', (line.name, old))
                connection.execute("UPDATE web_resource_access SET scope=? WHERE platform LIKE 'confluence:%' AND scope=?", (line.name, old))
                connection.execute("UPDATE web_resource_access SET resource_id=? WHERE platform LIKE 'confluence:%' AND kind='catalog' AND resource_id=?", (line.name, old))
        for session_hash, filters_json in connection.execute("SELECT session_hash,filters_json FROM web_query_snapshots WHERE scope='confluence-project-facts'").fetchall():
            filters = json.loads(filters_json)
            if '__product_space__' in filters:
                filters['__product_space__'] = [product_line_name(value) for value in filters['__product_space__']]
                connection.execute("UPDATE web_query_snapshots SET filters_json=? WHERE session_hash=? AND scope='confluence-project-facts'", (json.dumps(filters, ensure_ascii=False), session_hash))
        for username, scope, value_json in connection.execute("SELECT username,scope,value_json FROM user_preferences WHERE scope IN ('projects.html','/projects.html','confluence.html','/confluence.html') AND key='field.__product_space__'").fetchall():
            values = [product_line_name(value) for value in json.loads(value_json)]
            connection.execute("UPDATE user_preferences SET value_json=? WHERE username=? AND scope=? AND key='field.__product_space__'", (json.dumps(values, ensure_ascii=False), username, scope))
        from .audit.email_history import AuditEmailHistory
        AuditEmailHistory._migrate_confluence_names(connection)
        for snapshot_id, statistics_json in connection.execute('SELECT snapshot_id,statistics_json FROM jira_analytics_snapshots WHERE statistics_json IS NOT NULL').fetchall():
            statistics = json.loads(statistics_json)
            for group in (statistics, statistics.get('current', {}), statistics.get('previous', {})):
                names = set()
                for line in group.get('productLines', []):
                    line['id'] = product_line_name(line['id'])
                    line['label'] = product_line_name(line['label'])
                    if line['id'] in names or line['id'] != line['label']:
                        raise ValueError('conflicting_jira_product_line')
                    names.add(line['id'])
            connection.execute('UPDATE jira_analytics_snapshots SET statistics_json=? WHERE snapshot_id=?', (json.dumps(statistics, ensure_ascii=False), snapshot_id))
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('jira_team_bug_lines', 'jira_team_bug_rows'):
            if table in tables:
                columns = ('product_line', 'project_key') if table == 'jira_team_bug_lines' else ('product_line',)
                for column in columns:
                    for line in DASHBOARD_PRODUCT_LINES:
                        connection.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?', (line.name, line.name + ' Business'))
        connection.execute("INSERT OR REPLACE INTO smarttest_schema VALUES('confluence_product_line_names',2)")


def _migrate_dashboard_jira_cards(connection) -> None:
    rows = connection.execute(
        "SELECT username,value_json FROM user_preferences WHERE scope='dashboard/layout' AND key='layout'"
    ).fetchall()
    for username, value_json in rows:
        layout = json.loads(value_json)
        changed = False
        for item in layout if isinstance(layout, list) else ():
            replacement = _DASHBOARD_JIRA_TYPE_MIGRATIONS.get(item.get("type")) if isinstance(item, dict) else None
            if not replacement:
                continue
            item["type"], card_key = replacement
            item["config"] = {**(item.get("config") if isinstance(item.get("config"), dict) else {}), "cardKey": card_key}
            changed = True
        if changed:
            connection.execute(
                "UPDATE user_preferences SET value_json=? WHERE username=? AND scope='dashboard/layout' AND key='layout'",
                (json.dumps(layout, ensure_ascii=False, separators=(",", ":")), username),
            )


def ensure_component_schema(
    database: WebDatabase,
    *,
    component: str,
    version: int,
    statements: Iterable[str],
    upgrade_statements: Iterable[str] = (),
) -> None:
    with database.transaction() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(smarttest_schema)")
        }
        obsolete_marker = bool(columns and "component" not in columns)
        if obsolete_marker:
            connection.execute("DROP TABLE smarttest_schema")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS smarttest_schema ("
            "component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        if obsolete_marker:
            connection.execute(
                "INSERT INTO smarttest_schema(component,version) VALUES('confluence_cache',0)"
            )
        row = connection.execute(
            "SELECT version FROM smarttest_schema WHERE component=?", (component,)
        ).fetchone()
        for statement in statements:
            connection.execute(statement)
        if row is not None and int(row[0]) < version:
            for statement in upgrade_statements:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO smarttest_schema(component,version) VALUES(?,?) "
            "ON CONFLICT(component) DO UPDATE SET version=excluded.version",
            (component, version),
        )


def initialize_current_cache_schema(database: WebDatabase) -> None:
    from .confluence.schema import initialize_confluence_schema
    from .jira.schema import initialize_jira_schema

    initialize_jira_schema(database)
    initialize_confluence_schema(database)


def initialize_web_schema(database: WebDatabase) -> None:
    """Prepare every persistent Web component before constructing its owners."""
    with database.connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    with database.transaction() as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] > 3:
            raise RuntimeError("Unsupported SmartTest Web database schema.")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS web_sessions (
                id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL, display_name TEXT NOT NULL, avatar BLOB,
                created_at REAL NOT NULL, last_seen_at REAL NOT NULL,
                expires_at REAL NOT NULL, revoked_at REAL
            );
            CREATE INDEX IF NOT EXISTS ix_web_sessions_user ON web_sessions(username);
            CREATE TABLE IF NOT EXISTS web_account_identities (
                username TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                avatar BLOB, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_preferences (
                username TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
                value_json TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL, PRIMARY KEY (username, scope, key)
            );
            CREATE TABLE IF NOT EXISTS web_query_snapshots (
                session_hash TEXT NOT NULL, scope TEXT NOT NULL,
                filters_json TEXT NOT NULL, search TEXT NOT NULL,
                project_ids_json TEXT NOT NULL, facts_version TEXT NOT NULL,
                release_names_json TEXT NOT NULL DEFAULT '[]',
                jira_cache_version TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY (session_hash, scope)
            );
            CREATE INDEX IF NOT EXISTS ix_web_query_snapshots_expiry ON web_query_snapshots(expires_at);
            CREATE TABLE IF NOT EXISTS web_credentials (
                credential_ref TEXT PRIMARY KEY, nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL, key_version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_resource_access (
                account TEXT NOT NULL, platform TEXT NOT NULL, kind TEXT NOT NULL,
                resource_id TEXT NOT NULL, capability TEXT NOT NULL, scope TEXT NOT NULL,
                confirmed_at REAL NOT NULL,
                PRIMARY KEY(account,platform,kind,resource_id,capability,scope)
            );
            CREATE TABLE IF NOT EXISTS test_suites (
                id TEXT PRIMARY KEY, owner_username TEXT NOT NULL,
                owner_display_name TEXT NOT NULL, name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL
                CHECK (visibility IN ('private','shared')),
                ordered_nodeids_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(owner_username,name)
            );
            CREATE INDEX IF NOT EXISTS ix_test_suites_visibility_updated
                ON test_suites(visibility,updated_at DESC);
            CREATE TABLE IF NOT EXISTS audit_email_runs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                account TEXT NOT NULL, label TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_email_occurrences (
                account TEXT NOT NULL, occurrence TEXT NOT NULL, claimed_at TEXT NOT NULL,
                PRIMARY KEY(account, occurrence)
            );
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(web_sessions)")}
        if "credential_ref" not in columns:
            connection.execute("ALTER TABLE web_sessions ADD COLUMN credential_ref TEXT")
        if "revoked_reason" not in columns:
            connection.execute("ALTER TABLE web_sessions ADD COLUMN revoked_reason TEXT")
        snapshot_columns = {row[1] for row in connection.execute("PRAGMA table_info(web_query_snapshots)")}
        if "release_names_json" not in snapshot_columns:
            connection.execute("ALTER TABLE web_query_snapshots ADD COLUMN release_names_json TEXT NOT NULL DEFAULT '[]'")
        if "jira_cache_version" not in snapshot_columns:
            connection.execute("ALTER TABLE web_query_snapshots ADD COLUMN jira_cache_version TEXT NOT NULL DEFAULT ''")
        connection.execute("""
            INSERT OR IGNORE INTO user_preferences(username,scope,key,value_json,schema_version,updated_at)
            SELECT username,'jira/cards/customer-feedback',key,value_json,schema_version,updated_at
            FROM user_preferences WHERE scope='jira/cards/customer'
        """)
        connection.execute("DELETE FROM user_preferences WHERE scope='jira/cards/customer'")
        _migrate_dashboard_jira_cards(connection)
        connection.execute("PRAGMA user_version=3")
    initialize_current_cache_schema(database)
    _migrate_confluence_product_lines(database)
    from .confluence.project_repository import upgrade_cached_project_names
    upgrade_cached_project_names(database)
