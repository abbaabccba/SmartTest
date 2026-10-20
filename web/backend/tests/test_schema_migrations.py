from sqlalchemy import event, text
from smarttest_web.audit.email_history import AuditEmailHistory
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.jira.issue_repository import JiraIssueRepository
from smarttest_web.resource_access import ResourceAccess
from smarttest_web.schema import verify_schema_revision
from smarttest_web.session import PersistentSessionStore
from smarttest_web.test_suite_repository import TestSuiteRepository


def test_owner_construction_executes_no_schema_ddl(database_factory):
    database = database_factory()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(database._engine, "before_cursor_execute", capture)
    try:
        JiraIssueRepository(database)
        ConfluenceProjectRepository(database)
        TestSuiteRepository(database)
        AuditEmailHistory(database)
        ResourceAccess(database, "a", "p", "s")
        PersistentSessionStore(database=database)
        assert not any(
            statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))
            for statement in statements
        )
    finally:
        event.remove(database._engine, "before_cursor_execute", capture)


def test_schema_gate_does_not_rewrite_loaded_detail_states(database_factory):
    database = database_factory()
    with database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO confluence_projects(confluence_id,project_id,cached_at) VALUES('1','P1','now')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO confluence_project_detail_states(confluence_id,section_name,state,source_revision,error_code,has_value,cached_at) VALUES('1',:section,'loaded','','',1,'now')"
            ),
            [{"section": "roles"}, {"section": "facts"}],
        )
    verify_schema_revision(database)
    verify_schema_revision(database)
    with database.connect() as connection:
        assert connection.execute(
            text(
                "SELECT section_name,state FROM confluence_project_detail_states ORDER BY section_name"
            )
        ).fetchall() == [("facts", "loaded"), ("roles", "loaded")]


def test_registered_card_period_preferences_survive_version_gate(database_factory):
    database = database_factory()
    store = PersistentSessionStore(database=database)
    store.upsert_preferences(
        "coco", "jira/cards/customer-feedback", {"period": "quarter"}, 1
    )
    verify_schema_revision(database)
    restored = PersistentSessionStore(database=database_factory())
    assert restored.get_preferences("coco", "jira/cards/customer-feedback")[
        "items"
    ] == {"period": "quarter"}
    assert (
        restored.get_preferences("other", "jira/cards/customer-feedback")["items"] == {}
    )


def test_dashboard_layout_survives_engine_reconstruction_without_mutation(
    database_factory,
):
    database = database_factory()
    layout = [
        {
            "id": "self",
            "type": "jira-statistics-self-test",
            "x": 1,
            "y": 2,
            "w": 20,
            "h": 9,
            "config": {"cardKey": "self-test", "color": "blue"},
            "unrelated": "keep",
        },
        {
            "id": "customer",
            "type": "jira-statistics-customer-feedback",
            "x": 2,
            "y": 20,
            "w": 22,
            "h": 7,
            "config": {"cardKey": "customer-feedback", "period": "quarter"},
        },
    ]
    store = PersistentSessionStore(database=database)
    store.upsert_preferences("coco", "dashboard/layout", {"layout": layout}, 1)
    verify_schema_revision(database)
    restored = PersistentSessionStore(database=database_factory())
    assert (
        restored.get_preferences("coco", "dashboard/layout")["items"]["layout"]
        == layout
    )
