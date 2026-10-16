import pytest
from sqlalchemy import inspect, text
from smarttest_web.schema import (
    CURRENT_SCHEMA_REVISION,
    application_metadata,
    verify_schema_revision,
)


def test_current_schema_is_complete_and_version_check_is_idempotent(database_factory):
    database = database_factory()
    verify_schema_revision(database)
    verify_schema_revision(database)
    with database.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set(
            application_metadata().tables
        ) | {"alembic_version"}
        assert connection.execute(text("SELECT @@foreign_key_checks")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == CURRENT_SCHEMA_REVISION
        )


def test_future_version_is_rejected_without_mutating_persistent_rows(database_factory):
    database = database_factory()
    with database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO confluence_projects(confluence_id,project_id,name,cached_at) VALUES('2','P-2','kept','now')"
            )
        )
        connection.execute(text("UPDATE alembic_version SET version_num='future'"))
    try:
        with pytest.raises(RuntimeError, match="schema upgrade"):
            verify_schema_revision(database)
        with database.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM confluence_projects")
                ).scalar_one()
                == "kept"
            )
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "future"
            )
    finally:
        with database.transaction() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:version"),
                {"version": CURRENT_SCHEMA_REVISION},
            )


def test_missing_version_is_not_automatically_stamped(database_factory):
    database = database_factory()
    with database.transaction() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        with pytest.raises(RuntimeError, match="schema upgrade"):
            verify_schema_revision(database)
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 0
        )
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES(:version)"),
            {"version": CURRENT_SCHEMA_REVISION},
        )
