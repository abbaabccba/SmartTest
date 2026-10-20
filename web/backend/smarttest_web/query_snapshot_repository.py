from __future__ import annotations

from sqlalchemy import text
from .database import upsert, WebDatabase
from .schema import web_query_snapshots

from dataclasses import dataclass
import json
import time

from core.product_lines import product_line_name, confluence_space_key

PROJECT_FILTER_KEYS = frozenset({
    "__product_space__", "date of commercial approval", "project id", "project owner",
})


def normalize_project_filters(filters):
    return {str(key): values for key, values in (filters or {}).items() if str(key) in PROJECT_FILTER_KEYS}


@dataclass(frozen=True)
class ConfluenceQuerySnapshot:
    session_hash: str
    scope: str
    filters: dict
    search: str
    project_ids: tuple[str, ...]
    facts_version: str
    created_at: float
    updated_at: float
    expires_at: float


class ConfluenceQuerySnapshotRepository:
    scope = "confluence-project-facts"

    def __init__(self, database: WebDatabase, *, now=time.time):
        self.database, self._now = database, now

    def record(self, session_hash, filters, search, project_ids, facts_version, *, expires_at):
        now = self._now()
        stored_filters = normalize_project_filters(filters)
        if '__product_space__' in stored_filters:
            stored_filters['__product_space__'] = [product_line_name(value) for value in stored_filters['__product_space__']]
        values = (str(session_hash), self.scope, json.dumps(stored_filters, sort_keys=True), str(search or ""),
                  json.dumps(list(dict.fromkeys(str(value) for value in project_ids if str(value))), separators=(",", ":")),
                  str(facts_version or ""), now, now, float(expires_at))
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM web_query_snapshots WHERE expires_at<=:expires_at"), {"expires_at": now}
            )
            upsert(
                connection,
                web_query_snapshots,
                dict(
                    zip(
                        (
                            "session_hash",
                            "scope",
                            "filters_json",
                            "search",
                            "project_ids_json",
                            "facts_version",
                            "created_at",
                            "updated_at",
                            "expires_at",
                        ),
                        values,
                    )
                ),
                conflict_columns=("session_hash", "scope"),
                updates=(
                    "filters_json",
                    "search",
                    "project_ids_json",
                    "facts_version",
                    "updated_at",
                    "expires_at",
                ),
            )

    def get(self, session_hash, *, expires_at=None):
        now = self._now() if expires_at is None else float(expires_at)
        with self.database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT filters_json,search,project_ids_json,facts_version,created_at,updated_at,expires_at\n                FROM web_query_snapshots WHERE session_hash=:session_hash AND scope=:scope AND expires_at>:expires_at"
                ),
                {"session_hash": str(session_hash), "scope": self.scope, "expires_at": now},
            ).fetchone()
        if row is None:
            return None
        filters = normalize_project_filters(json.loads(row[0]))
        if '__product_space__' in filters:
            filters['__product_space__'] = [confluence_space_key(value) for value in filters['__product_space__']]
        return ConfluenceQuerySnapshot(str(session_hash), self.scope, filters, row[1],
                                       tuple(json.loads(row[2])), row[3], *map(float, row[4:]))

    def delete_session(self, session_hash):
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash"),
                {"session_hash": str(session_hash)},
            )

    def cleanup(self):
        with self.database.transaction() as connection:
            return connection.execute(
                text("DELETE FROM web_query_snapshots WHERE expires_at<=:expires_at"),
                {"expires_at": self._now()},
            ).rowcount
