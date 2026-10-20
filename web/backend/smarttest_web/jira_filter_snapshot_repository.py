from __future__ import annotations

from sqlalchemy import select, text, func
from .jira.schema import jira_issues

from .database import upsert, WebDatabase
from .schema import web_query_snapshots

from dataclasses import dataclass
import json
import time



JIRA_FILTER_FIELDS = ("project", "type", "status", "currentUser", "resolution")


@dataclass(frozen=True)
class JiraFilterSnapshot:
    session_hash: str
    scope: str
    filters: dict
    jql: str
    revision: float
    expires_at: float


class JiraFilterSnapshotRepository:
    scope = "jira-global-filter"

    def __init__(self, database: WebDatabase, *, now=time.time):
        self.database, self._now = database, now

    def record(self, session_hash, filters, jql, *, expires_at):
        normalized = {
            key: list(dict.fromkeys(str(value).strip() for value in filters.get(key, ()) if str(value).strip()))
            for key in JIRA_FILTER_FIELDS if filters.get(key)
        }
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM web_query_snapshots WHERE expires_at<=:expires_at"), {"expires_at": now}
            )
            upsert(
                connection,
                web_query_snapshots,
                {
                    "session_hash": str(session_hash),
                    "scope": self.scope,
                    "filters_json": json.dumps(normalized, sort_keys=True),
                    "search": str(jql or "").strip(),
                    "project_ids_json": "[]",
                    "facts_version": "",
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": float(expires_at),
                },
                conflict_columns=("session_hash", "scope"),
                updates=("filters_json", "search", "updated_at", "expires_at"),
            )

    def get(self, session_hash):
        now = self._now()
        with self.database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT filters_json,search,updated_at,expires_at\n                FROM web_query_snapshots WHERE session_hash=:session_hash AND scope=:scope AND expires_at>:expires_at"
                ),
                {"session_hash": str(session_hash), "scope": self.scope, "expires_at": now},
            ).fetchone()
        if row is None:
            return None
        return JiraFilterSnapshot(str(session_hash), self.scope, json.loads(row[0]), row[1], float(row[2]), float(row[3]))

    def delete(self, session_hash):
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash AND scope=:scope"),
                {"session_hash": str(session_hash), "scope": self.scope},
            )

    def facets(self):
        columns = {'project': 'project_key', 'type': 'issue_type_name', 'status': 'status_name', 'resolution': 'resolution_name'}
        with self.database.connect() as connection:
            values = {key: list(connection.execute(select(jira_issues.c[column]).distinct().where(
                jira_issues.c[column].is_not(None), func.trim(jira_issues.c[column]) != '').order_by(jira_issues.c[column])).scalars())
                      for key, column in columns.items()}
        values['currentUser'] = ['Current User']
        values['resolution'] = ['Unresolved', *values['resolution']]
        labels = {'project': 'Project', 'type': 'Type', 'status': 'Status', 'currentUser': 'Current User', 'resolution': 'Resolution'}
        return [{'key': key, 'label': labels[key], 'options': values[key]} for key in JIRA_FILTER_FIELDS]
