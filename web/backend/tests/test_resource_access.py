from __future__ import annotations
from sqlalchemy import text

import pytest

from smarttest_web.session import PersistentSessionStore
from conftest import MemoryCredentialStore


def contexts(tmp_path, *, database_factory):
    database = database_factory()
    sessions = PersistentSessionStore(
        credential_store=MemoryCredentialStore(), database=database_factory()
    )
    first, second = sessions.create("alice", "test"), sessions.create("bob", "test")
    return database, sessions, first, second


def test_resource_access_is_account_instance_and_capability_scoped(
    tmp_path, *, database_factory
):
    database, sessions, first, second = contexts(
        tmp_path, database_factory=database_factory
    )
    alice = sessions.resource_access(first, "confluence:https://one", database)
    bob = sessions.resource_access(second, "confluence:https://one", database)
    other_server = sessions.resource_access(first, "confluence:https://two", database)

    alice.publish((("project", "P1", "catalog", "TV"),), lambda: None)

    assert alice.ids("project", "catalog") == {"P1"}
    assert bob.ids("project", "catalog") == set()
    assert other_server.ids("project", "catalog") == set()
    assert not alice.allows("page", "P1", "body")


def test_publication_is_atomic_and_rejects_only_revoked_session(
    tmp_path, *, database_factory
):
    database, sessions, first, second = contexts(
        tmp_path, database_factory=database_factory
    )
    alice = sessions.resource_access(first, "jira:https://one", database)
    bob = sessions.resource_access(second, "jira:https://one", database)
    with database.transaction() as connection:
        connection.execute(text("DROP TABLE IF EXISTS shared_probe"))
        connection.execute(
            text("CREATE TABLE shared_probe (id VARCHAR(191) PRIMARY KEY)")
        )

    try:

        def write():
            with database.transaction() as connection:
                connection.execute(text("INSERT INTO shared_probe VALUES('one')"))

        sessions.delete(first)
        with pytest.raises(PermissionError, match="reauthentication_required"):
            alice.publish((("issue", "one", "core", ""),), write)
        assert bob.ids("issue", "core") == set()
        bob.publish((("issue", "one", "core", ""),), write)
        assert bob.ids("issue", "core") == {"one"}
        with database.connect() as connection:
            assert connection.execute(
                text("SELECT * FROM shared_probe")
            ).fetchall() == [("one",)]

        def failing_write():
            with database.transaction() as connection:
                connection.execute(text("INSERT INTO shared_probe VALUES('two')"))
            raise ValueError("mapping_failed")

        with pytest.raises(ValueError):
            bob.publish((("issue", "two", "core", ""),), failing_write)
        assert bob.ids("issue", "core") == {"one"}
        with database.connect() as connection:
            assert connection.execute(
                text("SELECT * FROM shared_probe")
            ).fetchall() == [("one",)]
    finally:
        with database.transaction() as connection:
            connection.execute(text("DROP TABLE shared_probe"))


def test_complete_scope_replaces_only_current_account_and_scope(
    tmp_path, *, database_factory
):
    database, sessions, first, second = contexts(
        tmp_path, database_factory=database_factory
    )
    alice = sessions.resource_access(first, "confluence:https://one", database)
    bob = sessions.resource_access(second, "confluence:https://one", database)
    for access in (alice, bob):
        access.publish((("project", "TV1", "catalog", "TV"), ("project", "D1", "catalog", "DOPL")), lambda: None)
    bob.publish((), lambda: None, replace_scopes=("TV",))
    assert bob.ids("project", "catalog") == {"D1"}
    assert alice.ids("project", "catalog") == {"TV1", "D1"}
