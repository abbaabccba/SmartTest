import pytest
from types import SimpleNamespace


class MemoryCredentialStore:
    def __init__(self): self.values = {}
    def write(self, reference, username, password): self.values[reference] = (username, password)
    def read(self, reference):
        from smarttest_web.credentials import CredentialMissingError
        if reference not in self.values:
            raise CredentialMissingError("credential not found")
        return self.values[reference]
    def delete(self, reference): self.values.pop(reference, None)


def confirmed_access(database, projects=(), pages=(), account="coco"):
    """Seed a previously confirmed account in the real MySQL boundary."""
    from smarttest_web.session import PersistentSessionStore
    sessions = PersistentSessionStore(
        database=database, credential_store=MemoryCredentialStore()
    )
    token = sessions.create(account, "test")
    access = sessions.resource_access(token, "confluence:https://confluence.amlogic.com", database)
    from core.product_lines import PRODUCT_LINES
    grants = [("project", project_id, capability, PRODUCT_LINES[0].name) for project_id in projects
              for capability in ("catalog", "roles", "evidence")]
    grants += [("page", page_id, "metadata", "P1:evidence") for page_id in pages]
    access.publish(grants, lambda: None)
    return access


@pytest.fixture(autouse=True)
def isolate_server_credentials(monkeypatch, tmp_path, request):
    import smarttest_web.session as session_module
    import smarttest_web.app as app_module

    monkeypatch.setattr(
        app_module, "configured_reports_root", lambda environment: tmp_path / "reports"
    )
    database_path = tmp_path / "retired-live-path-must-not-be-used.db"
    stores = {}
    monkeypatch.setattr(
        session_module,
        "create_credential_store",
        lambda database: stores.setdefault(database.location, MemoryCredentialStore()),
    )
    original = session_module.PersistentSessionStore.__init__

    def initialize_test_sessions(store, *args, **kwargs):
        if not args and kwargs.get("database") is None and "path" not in kwargs:
            kwargs["database"] = request.getfixturevalue("database_factory")()
        original(store, *args, **kwargs)

    monkeypatch.setattr(
        session_module.PersistentSessionStore, "__init__", initialize_test_sessions
    )
    return SimpleNamespace(root=tmp_path, database_path=database_path, credential_stores=stores)


@pytest.fixture
def database_factory(application_database):
    from smarttest_web.config import ApplicationDatabaseSettings
    from smarttest_web.database import WebDatabase
    import os

    environment = {
        name.replace("SMARTTEST_TEST_DB_", "SMARTTEST_DB_"): value
        for name, value in os.environ.items()
        if name.startswith("SMARTTEST_TEST_DB_")
    }
    settings = ApplicationDatabaseSettings.from_environment(environment)
    databases = []

    def create():
        database = WebDatabase.from_settings(settings)
        databases.append(database)
        return database

    yield create
    for database in databases:
        database.close()


@pytest.fixture
def application_database():
    import os
    from pathlib import Path
    from sqlalchemy import delete
    from smarttest_web.config import ApplicationDatabaseSettings
    from smarttest_web.database import WebDatabase
    from smarttest_web import schema

    if not os.environ.get("SMARTTEST_TEST_DB_NAME"):
        raise pytest.UsageError(
            "Backend business tests require SMARTTEST_TEST_DB_* for smarttest_preparation_test; production databases are forbidden"
        )
    environment = {
        name.replace("SMARTTEST_TEST_DB_", "SMARTTEST_DB_"): value
        for name, value in os.environ.items()
        if name.startswith("SMARTTEST_TEST_DB_")
    }
    settings = ApplicationDatabaseSettings.from_environment(environment)
    if settings.database != "smarttest_preparation_test":
        raise RuntimeError(
            "This destructive synthetic-data fixture requires smarttest_preparation_test"
        )
    database = WebDatabase.from_settings(settings)
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with database.transaction() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    metadata = schema.application_metadata()

    def clear():
        with database.transaction() as connection:
            for table in reversed(metadata.sorted_tables):
                connection.execute(delete(table))

    from smarttest_web.jira.analytics_tasks import JIRA_ANALYTICS_TASKS

    JIRA_ANALYTICS_TASKS.close_pages()
    clear()
    JIRA_ANALYTICS_TASKS.open_pages()
    try:
        yield database
    finally:
        JIRA_ANALYTICS_TASKS.close_pages()
        clear()
        database.close()
