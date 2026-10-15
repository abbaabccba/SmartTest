from __future__ import annotations

import sqlite3

from smarttest_web.database import WebDatabase
from smarttest_web.schema import initialize_current_cache_schema


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def test_current_cache_schema_initializes_both_components_idempotently(tmp_path) -> None:
    path = tmp_path / "web.db"
    database = WebDatabase(path)

    initialize_current_cache_schema(database)
    initialize_current_cache_schema(database)

    tables = _tables(path)
    assert {
        "smarttest_schema",
        "jira_issues",
        "jira_analytics_facts",
        "jira_issue_detail_states",
        "confluence_projects",
        "confluence_project_detail_states",
    } <= tables
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute(
            "SELECT component,version FROM smarttest_schema ORDER BY component"
        ).fetchall() == [("confluence_cache", 4), ("jira_cache", 7)]


def test_component_version_mismatch_preserves_cached_and_persistent_data(tmp_path) -> None:
    path = tmp_path / "web.db"
    database = WebDatabase(path)
    initialize_current_cache_schema(database)
    with database.transaction() as connection:
        connection.execute("CREATE TABLE web_sessions(id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE user_preferences(id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE web_credentials(id TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO jira_issues(issue_id,issue_key,summary,cached_at) VALUES('1','J-1','old','now')"
        )
        connection.execute(
            "INSERT INTO confluence_projects(confluence_id,project_id,name,cached_at) VALUES('2','P-2','kept','now')"
        )
        connection.execute(
            "UPDATE smarttest_schema SET version=1 WHERE component='jira_cache'"
        )

    initialize_current_cache_schema(database)

    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM jira_issues").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM confluence_projects").fetchone()[0] == "kept"
    assert {"web_sessions", "user_preferences", "web_credentials"} <= _tables(path)


def test_legacy_confluence_schema_marker_is_directly_replaced(tmp_path) -> None:
    path = tmp_path / "web.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE smarttest_schema(name TEXT PRIMARY KEY,version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO smarttest_schema(name,version) VALUES('confluence_projects',2)"
        )
        connection.execute(
            "CREATE TABLE confluence_project_attributes(project_id TEXT PRIMARY KEY)"
        )

    initialize_current_cache_schema(WebDatabase(path))

    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(smarttest_schema)")]
        assert columns == ["component", "version"]
        assert "confluence_project_attributes" in _tables(path)
