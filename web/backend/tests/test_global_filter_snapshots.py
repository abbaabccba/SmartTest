from fastapi.testclient import TestClient

from smarttest_web.app import create_app
from smarttest_web.jira_filter_snapshot_repository import JiraFilterSnapshotRepository
from smarttest_web.session import PersistentSessionStore
from test_manual_audit_api import ConfluenceOwner, JiraOwner, _Facts, _wait
from test_web_session import FakeAuthenticator


def test_jira_filter_snapshot_is_one_session_scoped_record(
    tmp_path, *, database_factory
):
    PersistentSessionStore(database=database_factory())
    database = database_factory()
    repository = JiraFilterSnapshotRepository(database, now=lambda: 100.0)

    repository.record("one", {"project": ["SH"], "type": ["Bug"]}, "status = Open", expires_at=200)
    repository.record("one", {"resolution": ["Done"]}, "", expires_at=200)

    assert repository.get("one").filters == {"resolution": ["Done"]}
    assert repository.get("one").jql == ""
    assert repository.get("two") is None


def test_confluence_review_requires_and_consumes_existing_snapshot(
    tmp_path, *, database_factory
):
    facts, owner = _Facts(), ConfluenceOwner()
    app = create_app(
        authenticator=FakeAuthenticator,
        session_store=lambda: PersistentSessionStore(database=database_factory()),
        project_facts_owner=lambda: facts,
        jira_audit_owner=lambda _username, _password: JiraOwner(),
        confluence_audit_owner=lambda _access, _password: owner,
    )
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    missing = client.post("/api/audits/confluence", json={
        "filters": {"fields": {"support mode": ["forged"]}},
        "startDate": "2026-08-17", "endDate": "2026-08-24",
    })
    assert missing.status_code == 409
    assert missing.json()["detail"]["state"] == "no_snapshot"

    read = client.get("/api/confluence/project-facts?field.support%20mode=forged&search=forged").json()
    assert read["querySnapshot"] is None
    applied = client.put("/api/confluence/filter-snapshot", json={
        "filters": {"project id": ["P100"], "project owner": ["Alice"], "support mode": ["A"]}, "search": "approved",
    }).json()
    assert applied["querySnapshot"]["filters"] == {
        "project id": ["P100"], "project owner": ["Alice"],
    }
    assert applied["querySnapshot"]["search"] == "approved"
    revision = applied["querySnapshot"]["revision"]
    assert client.get("/api/confluence/project-facts?field.support%20mode=forged").json()["querySnapshot"]["revision"] == revision
    created = client.post("/api/audits/confluence", json={
        "filters": {"fields": {"support mode": ["forged"]}},
        "projectIds": ["forged"],
        "startDate": "2026-08-17", "endDate": "2026-08-24",
    })
    assert created.status_code == 200
    assert _wait(client, "confluence", created.json()["auditId"])["status"] == "completed"


def test_jira_filter_api_exposes_only_approved_fields_and_runs_from_snapshot(
    tmp_path, *, database_factory
):
    owner = JiraOwner()
    app = create_app(
        authenticator=FakeAuthenticator,
        session_store=lambda: PersistentSessionStore(database=database_factory()),
        project_facts_owner=_Facts,
        jira_audit_owner=lambda _username, _password: owner,
        confluence_audit_owner=lambda _access, _password: ConfluenceOwner(),
    )
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    initial = client.get("/api/jira/filter-snapshot")
    assert initial.status_code == 200
    assert [row["key"] for row in initial.json()["facets"]] == [
        "project", "type", "status", "currentUser", "resolution",
    ]
    assert client.put("/api/jira/filter-snapshot", json={
        "filters": {"project": "SH"}, "jql": "",
    }).status_code == 422

    applied = client.put("/api/jira/filter-snapshot", json={
        "filters": {"project": ["SH"], "type": ["Bug"], "status": ["Open"],
                    "currentUser": ["Current User"], "resolution": ["Unresolved"]},
        "jql": "labels = weekly",
    })
    assert applied.status_code == 200
    assert applied.json()["snapshot"]["filters"]["project"] == ["SH"]

    created = client.post("/api/audits/jira", json={})
    assert created.status_code == 200
    assert _wait(client, "jira", created.json()["auditId"])["status"] == "completed"
    assert "project = \"SH\"" in owner.last_scope.jql
    assert "issuetype = \"Bug\"" in owner.last_scope.jql
    assert "status = \"Open\"" in owner.last_scope.jql
    assert "assignee = currentUser()" in owner.last_scope.jql
    assert "resolution IS EMPTY" in owner.last_scope.jql
    assert "labels = weekly" in owner.last_scope.jql
