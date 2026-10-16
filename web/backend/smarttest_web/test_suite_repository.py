from __future__ import annotations

from sqlalchemy import select, text

from .schema import test_suites

from collections.abc import Sequence
from dataclasses import dataclass
import json
from sqlalchemy.exc import IntegrityError
import time
from uuid import uuid4

from .database import WebDatabase, is_unique_violation


class NameConflictError(ValueError):
    pass


class RevisionConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TestSuiteRecord:
    id: str
    owner_username: str
    owner_display_name: str
    name: str
    description: str
    visibility: str
    ordered_nodeids: tuple[str, ...]
    revision: int
    created_at: float
    updated_at: float


class TestSuiteRepository:
    __test__ = False
    def __init__(self, database: WebDatabase, *, now=time.time):
        self.database = database
        self._now = now

    @staticmethod
    def _record(row) -> TestSuiteRecord:
        return TestSuiteRecord(
            id=row[0], owner_username=row[1], owner_display_name=row[2], name=row[3],
            description=row[4], visibility=row[5],
            ordered_nodeids=tuple(json.loads(row[6])), revision=int(row[7]),
            created_at=float(row[8]), updated_at=float(row[9]),
        )

    @staticmethod
    def _values(name: str, description: str, visibility: str,
                ordered_nodeids: Sequence[str]) -> tuple[str, str, str, str]:
        clean_name = str(name or "").strip()
        if not clean_name or visibility not in {"private", "shared"}:
            raise ValueError("invalid_input")
        ordered: list[str] = []
        for value in ordered_nodeids:
            nodeid = str(value or "").strip()
            if nodeid and nodeid not in ordered:
                ordered.append(nodeid)
        if not ordered:
            raise ValueError("invalid_input")
        return clean_name, str(description or ""), visibility, json.dumps(ordered, ensure_ascii=False)

    def _query(self, statement, params=None):
        with self.database.connect() as connection:
            return [self._record(row) for row in connection.execute(statement, params or {}).fetchall()]

    def list_mine(self, username):
        return self._query(select(test_suites).where(test_suites.c.owner_username == username).order_by(test_suites.c.updated_at.desc()))

    def list_shared(self, username):
        return self._query(select(test_suites).where(test_suites.c.visibility == 'shared', test_suites.c.owner_username != username).order_by(test_suites.c.updated_at.desc()))

    def get_visible(self, suite_id, username):
        rows = self._query(select(test_suites).where(test_suites.c.id == suite_id,
            (test_suites.c.owner_username == username) | (test_suites.c.visibility == 'shared')))
        return rows[0] if rows else None

    def create(self, *, owner_username: str, owner_display_name: str, name: str,
               description: str, visibility: str, ordered_nodeids: Sequence[str]) -> TestSuiteRecord:
        clean_name, description, visibility, encoded = self._values(name, description, visibility, ordered_nodeids)
        suite_id, timestamp = str(uuid4()), float(self._now())
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    test_suites.insert().values(
                        {
                            "id": suite_id,
                            "owner_username": owner_username,
                            "owner_display_name": owner_display_name,
                            "name": clean_name,
                            "description": description,
                            "visibility": visibility,
                            "ordered_nodeids_json": encoded,
                            "revision": 1,
                            "created_at": timestamp,
                            "updated_at": timestamp,
                        }
                    )
                )
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            raise NameConflictError(clean_name) from error
        return self.get_visible(suite_id, owner_username)  # type: ignore[return-value]

    def update(self, suite_id: str, *, owner_username: str, revision: int, name: str,
               description: str, visibility: str, ordered_nodeids: Sequence[str]) -> TestSuiteRecord | None:
        clean_name, description, visibility, encoded = self._values(name, description, visibility, ordered_nodeids)
        timestamp = float(self._now())
        try:
            with self.database.transaction() as connection:
                owner = connection.execute(select(test_suites.c.revision).where(test_suites.c.id == suite_id, test_suites.c.owner_username == owner_username).with_for_update()).fetchone()
                if owner is None:
                    return None
                if int(owner[0]) != int(revision):
                    raise RevisionConflictError(suite_id)
                connection.execute(
                    text(
                        "UPDATE test_suites SET name=:name,description=:description,visibility=:visibility,ordered_nodeids_json=:ordered_nodeids_json,revision=revision+1,updated_at=:updated_at WHERE id=:id AND owner_username=:owner_username"
                    ),
                    {
                        "name": clean_name,
                        "description": description,
                        "visibility": visibility,
                        "ordered_nodeids_json": encoded,
                        "updated_at": timestamp,
                        "id": suite_id,
                        "owner_username": owner_username,
                    },
                )
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            raise NameConflictError(clean_name) from error
        return self.get_visible(suite_id, owner_username)

    def delete(self, suite_id: str, *, owner_username: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                text("DELETE FROM test_suites WHERE id=:id AND owner_username=:owner_username"),
                {"id": suite_id, "owner_username": owner_username},
            )
            return cursor.rowcount > 0

    def copy(self, suite_id: str, *, reader_username: str, owner_display_name: str,
             name: str, visibility: str = "private") -> TestSuiteRecord | None:
        source = self.get_visible(suite_id, reader_username)
        if source is None:
            return None
        return self.create(owner_username=reader_username, owner_display_name=owner_display_name,
                           name=name, description=source.description, visibility=visibility,
                           ordered_nodeids=source.ordered_nodeids)
