from __future__ import annotations

from fastapi.testclient import TestClient
from time import sleep

from core.domain.detail import DetailSection
from core.domain.values import NamedValue
from core.jira.domain import Issue, IssueIdentity, JiraProjectRef

from smarttest_web.app import create_app
from test_web_session import FakeAuthenticator


class _Facts:
    synced = []
    def query(self, _access, **_kwargs):
        return {"state": "ready", "facets": [], "projects": [
            {"identity": "P100"}, {"identity": "P200"},
        ], "ownerHierarchy": []}
    def facts_version(self): return "c-v1"
    def sync_details(self, _access, _password, **kwargs): self.synced.append(kwargs)


class _ReleaseQueries:
    def __init__(self):
        self.dashboard_calls = []
        self.issue_calls = []
        self.detail_calls = []

    def dashboard(self, **kwargs):
        self.dashboard_calls.append(kwargs)
        rows = [
            {"projectId": "P100", "releaseName": "Android 16", "issueCounts": {"open": 2}},
            {"projectId": "P200", "releaseName": "Release, EVT", "issueCounts": {"open": 1}},
        ]
        if kwargs.get("project_ids"):
            rows = [row for row in rows if row["projectId"] in kwargs["project_ids"]]
        return {
            "state": "ready", "facets": [], "summary": {},
            "releases": rows,
            "sourceFreshness": {"confluence": "c-v1", "jira": "j-v1"},
        }

    def issues(self, **kwargs):
        self.issue_calls.append(kwargs)
        total = 2 if kwargs.get("project_ids") == ("P100",) else 0
        return {
            "state": "ready", "selectedRelease": {"projectId": "P100"}, "facets": [],
            "issues": [], "counts": {"exact": 0, "versionPending": 0},
            "pagination": {"page": 0, "pageSize": 50, "total": total},
            "sourceFreshness": {"confluence": "c-v1", "jira": "j-v1"},
        }

    def issue_detail(self, issue_key, **kwargs):
        self.detail_calls.append(kwargs)
        if issue_key != "SH-1": return None
        return {"key": "SH-1", "projectId": "P100", "releaseAssociation": "exact"}
    def jira_cache_version(self): return "j-v1"


class _NoRemoteOnRead:
    refreshes = []
    details = []
    def list_issues(self, *_args, **_kwargs):
        raise AssertionError("release page entry must not call Jira")
    def refresh_release_issues(self, query, page=0):
        self.refreshes.append((query, page))
        return {"total": 0, "page_size": 100}
    def get_issue(self, issue_key, details):
        self.details.append((issue_key, details.sections()))
        return Issue(
            IssueIdentity("1", issue_key, "https://jira/" + issue_key), "Issue",
            JiraProjectRef("SH"), NamedValue("1", "Open"), NamedValue("2", "Bug"),
            comments=DetailSection.loaded(()),
        )


def _client(releases):
    app = create_app(
        authenticator=FakeAuthenticator,
        project_facts_owner=_Facts,
        jira_cache_owner=lambda *_: _NoRemoteOnRead(),
        release_query_owner=lambda _database: releases,
    )
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    assert client.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""}).status_code == 200
    assert client.put("/api/jira/filter-snapshot", json={"filters": {}, "jql": ""}).status_code == 200
    return client


def test_dashboard_entry_reads_release_database_owner_and_records_server_scope():
    releases = _ReleaseQueries()
    client = _client(releases)

    response = client.get("/api/dashboard/releases")

    assert response.status_code == 200
    assert response.json()["querySnapshot"]["scope"] == "confluence-project-facts"
    assert releases.dashboard_calls[0]["project_ids"] == ("P100", "P200")


def test_dashboard_drilldown_derives_one_project_release_scope_and_rejects_forged_selection():
    releases = _ReleaseQueries()
    client = _client(releases)
    dashboard = client.get("/api/dashboard/releases").json()

    response = client.get(
        "/api/jira/release-issues",
        params={"snapshot": "dashboard", "projectId": "P100", "page": 0, "pageSize": 50},
    )

    assert response.status_code == 200
    assert releases.issue_calls[-1]["project_ids"] == ("P100",)
    assert releases.issue_calls[-1]["filters"]["_scopeRelease"] == ("Android 16",)
    assert releases.issue_calls[-1]["filters"]["_openOnly"] is True
    assert dashboard["releases"][0]["issueCounts"]["open"] == response.json()["pagination"]["total"]
    assert response.json()["querySnapshot"]["scope"] == "jira-global-filter"

    calls_before_forgery = len(releases.issue_calls)
    forged = client.get(
        "/api/jira/release-issues",
        params={"snapshot": "dashboard", "projectId": "FORGED", "page": 0, "pageSize": 50},
    )

    assert forged.status_code == 404
    assert len(releases.issue_calls) == calls_before_forgery


def test_jira_apply_and_detail_keep_the_derived_server_scope():
    releases = _ReleaseQueries()
    client = _client(releases)
    client.get("/api/dashboard/releases")
    client.get("/api/jira/release-issues", params={"snapshot": "dashboard", "projectId": "P100"})

    response = client.get("/api/jira/release-issues", params={"priority": "P0", "page": 1})
    detail = client.get("/api/jira/release-issues/SH-1")

    assert response.status_code == 200
    assert releases.issue_calls[-1]["project_ids"] == ("P100", "P200")
    assert releases.issue_calls[-1]["filters"] == {}
    assert detail.status_code == 200
    assert releases.detail_calls[-1]["project_ids"] == ("P100", "P200")
    assert releases.detail_calls[-1]["filters"] == {}


def test_release_api_rejects_invalid_pagination_without_querying_sqlite():
    releases = _ReleaseQueries()
    client = _client(releases)

    response = client.get("/api/jira/release-issues", params={"page": "bad"})

    assert response.status_code == 422
    assert releases.issue_calls == []


def test_explicit_dashboard_sync_refreshes_only_server_snapshot_project_range():
    _Facts.synced.clear(); _NoRemoteOnRead.refreshes.clear()
    releases = _ReleaseQueries()
    client = _client(releases)
    client.get("/api/dashboard/releases")

    response = client.post("/api/dashboard/releases/sync")

    assert response.status_code == 200
    assert response.json()["syncState"] == "loading"
    task_id = response.json()["taskId"]
    for _ in range(100):
        task = client.get(f"/api/dashboard/releases/sync/{task_id}").json()
        if task["state"] not in {"queued", "running"}:
            break
        sleep(.01)
    assert task["state"] == "completed"
    assert task["syncState"] == "ready"
    assert _Facts.synced[-1]["filters"] == {"project id": ("P100", "P200")}
    assert _NoRemoteOnRead.refreshes == [('"Project ID" in ("P100","P200")', 0)]


def test_issue_detail_loads_only_explicitly_requested_sections_after_snapshot_scope_check():
    _NoRemoteOnRead.details.clear()
    releases = _ReleaseQueries()
    client = _client(releases)
    client.get("/api/dashboard/releases")
    client.get(
        "/api/jira/release-issues", params={"snapshot": "dashboard", "projectId": "P100"},
    )

    response = client.get("/api/jira/release-issues/SH-1", params=[("details", "comments")])

    assert response.status_code == 200
    assert response.json()["details"]["comments"]["state"] == "loaded"
    assert _NoRemoteOnRead.details == [("SH-1", ("comments",))]
