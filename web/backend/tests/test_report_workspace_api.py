from __future__ import annotations

from fastapi.testclient import TestClient
from openpyxl import Workbook

from smarttest_web.app import create_app
from smarttest_web.background_refresh import BackgroundFactsRefresh
from smarttest_web.downloads import DownloadArtifactService
from smarttest_web.report_workspace import ClientAuditReportOwner
from smarttest_web.session import PersistentSessionStore
from test_web_session import FakeAuthenticator


def _authenticated_client(app, username="coco"):
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": username, "password": "secret"})
    return client


class ReadyFactsOwner:
    def __init__(self, result=None):
        self.result = result or {
            "state": "ready", "facets": [],
            "projects": [], "ownerHierarchy": [],
        }
        self.sync_calls = []
        self.refresh_calls = []

    def query(self, _username, *, filters=None, search=""):
        return self.result

    def sync_details(self, _username, _password, *, filters=None, search="", cancelled=None, progress=None):
        self.sync_calls.append((filters, search))
        return self.result

    def refresh_and_sync_details(self, username, password, **kwargs):
        self.refresh(username, password)
        return self.sync_details(username, password, **kwargs)

    def refresh(self, username, password):
        self.refresh_calls.append((username.account, password))
        return self.result


def _workbook(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_confluence_project_facts_requires_login() -> None:
    app = create_app(project_facts_owner=ReadyFactsOwner, authenticator=FakeAuthenticator)

    assert TestClient(app, base_url="https://testserver").get(
        "/api/confluence/project-facts"
    ).status_code == 401


def test_confluence_details_are_loaded_only_for_explicit_apply() -> None:
    owner = ReadyFactsOwner()
    owner.result["detailState"] = "missing"
    client = _authenticated_client(create_app(
        project_facts_owner=lambda: owner,
        authenticator=FakeAuthenticator,
        facts_refresh=lambda: BackgroundFactsRefresh(submit=lambda work: work()),
    ))

    client.get("/api/confluence/project-facts?field.project%20id=P100")
    assert owner.sync_calls == []
    response = client.put("/api/confluence/filter-snapshot", json={
        "filters": {"project id": ["P100"]}, "search": "",
    })

    assert response.status_code == 200
    assert owner.sync_calls == [({"project id": ("P100",)}, "")]

    client.get("/api/confluence/project-facts?field.project%20id=P100")
    assert owner.sync_calls == [({"project id": ("P100",)}, "")]


def test_project_page_entry_replays_the_current_session_query_snapshot(
    tmp_path, *, database_factory
) -> None:
    class Facts(ReadyFactsOwner):
        def __init__(self):
            super().__init__()
            self.query_calls = []

        def query(self, _access, *, filters=None, search="", **_kwargs):
            self.query_calls.append((filters, search))
            project_id = "FILTERED" if filters else "ALL"
            return {
                "state": "ready", "facets": [],
                "projects": [{"project_id": project_id}], "ownerHierarchy": [],
            }

    facts = Facts()
    client = _authenticated_client(
        create_app(
            session_store=lambda: PersistentSessionStore(database=database_factory()),
            project_facts_owner=lambda: facts,
            authenticator=FakeAuthenticator,
        )
    )

    client.put("/api/confluence/filter-snapshot", json={
        "filters": {"project id": ["P100"], "current stage": ["EVT"]}, "search": "Apollo",
    })
    facts.query_calls.clear()
    response = client.get("/api/confluence/project-facts", params={"snapshot": "1"})

    assert response.json()["projects"] == [{"project_id": "FILTERED"}]
    assert facts.query_calls == [({"project id": ["P100"]}, "Apollo")]


def test_project_owner_is_kept_while_unsupported_filters_are_ignored(
    tmp_path, *, database_factory
) -> None:
    class Facts(ReadyFactsOwner):
        def __init__(self):
            super().__init__()
            self.query_calls = []

        def query(self, _access, *, filters=None, search="", **_kwargs):
            self.query_calls.append((filters, search))
            return {"state": "ready", "facets": [], "projects": [], "ownerHierarchy": []}

    facts = Facts()
    client = _authenticated_client(
        create_app(
            session_store=lambda: PersistentSessionStore(database=database_factory()),
            project_facts_owner=lambda: facts,
            authenticator=FakeAuthenticator,
        )
    )

    response = client.put("/api/confluence/filter-snapshot", json={"filters": {
        "project id": ["P100"], "project status": ["WARNING"], "current stage": ["EVT"],
        "project owner": ["Alice"], "support mode": ["A"], "odm": ["ODM-X"],
    }, "search": "Apollo"})
    assert response.status_code == 200
    assert response.json()["querySnapshot"]["filters"] == {
        "project id": ["P100"], "project owner": ["Alice"],
    }
    assert facts.query_calls[-1] == ({"project id": ("P100",), "project owner": ("Alice",)}, "Apollo")

    client.get("/api/confluence/project-facts?field.project%20id=P200&field.project%20owner=Bob&field.support%20mode=B&field.odm=ODM-Y")
    assert facts.query_calls[-1] == ({"project id": ("P200",), "project owner": ("Bob",)}, "")


def test_project_reset_replaces_session_snapshot_with_authorized_catalog_scope(
    tmp_path, *, database_factory
) -> None:
    class Facts(ReadyFactsOwner):
        def query(self, _access, *, filters=None, search="", **_kwargs):
            project_id = "FILTERED" if filters or search else "ALL"
            return {
                "state": "ready", "facets": [],
                "projects": [{"project_id": project_id}], "ownerHierarchy": [],
            }

    client = _authenticated_client(
        create_app(
            session_store=lambda: PersistentSessionStore(database=database_factory()),
            project_facts_owner=Facts,
            authenticator=FakeAuthenticator,
        )
    )
    client.get("/api/confluence/project-facts", params={"field.current stage": "EVT", "details": "1"})

    reset = client.get("/api/confluence/project-facts", params={"reset": "1"})
    replayed = client.get("/api/confluence/project-facts", params={"snapshot": "1"})

    assert reset.json()["projects"] == [{"project_id": "ALL"}]
    assert replayed.json()["projects"] == [{"project_id": "ALL"}]


def test_login_does_not_prefetch_confluence_catalog() -> None:
    owner = ReadyFactsOwner({
        "state": "no_snapshot", "facets": [], "projects": [], "ownerHierarchy": [],
    })
    submitted = []
    app = create_app(
        project_facts_owner=lambda: owner,
        authenticator=FakeAuthenticator,
        facts_refresh=lambda: BackgroundFactsRefresh(submit=submitted.append),
    )

    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    assert response.status_code == 200
    assert submitted == []
    assert owner.refresh_calls == []


def test_first_apply_without_snapshot_starts_the_shared_acquisition_pipeline() -> None:
    class CatalogFactsOwner(ReadyFactsOwner):
        def refresh(self, username, password):
            super().refresh(username, password)
            self.result = {
                "state": "ready", "projects": [], "ownerHierarchy": [],
                "facets": [{"key": "support mode", "options": ["A", "B"]}],
            }
            return self.result

    owner = CatalogFactsOwner({
        "state": "no_snapshot", "facets": [], "projects": [], "ownerHierarchy": [],
    })
    submitted = []
    refresh = BackgroundFactsRefresh(submit=submitted.append)
    client = _authenticated_client(create_app(
        project_facts_owner=lambda: owner,
        authenticator=FakeAuthenticator,
        facts_refresh=lambda: refresh,
    ))

    first = client.get("/api/confluence/project-facts")
    second = client.get("/api/confluence/project-facts/status")

    assert first.json()["state"] == "no_snapshot"
    assert second.json()["state"] == "idle"
    applied = client.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""})
    assert applied.json()["state"] == "loading"
    assert len(submitted) == 1
    submitted.pop()()

    assert client.get("/api/confluence/project-facts/status").json()["state"] == "ready"
    completed = client.get("/api/confluence/project-facts")
    repeated = client.get("/api/confluence/project-facts")
    assert completed.json()["state"] == "ready"
    assert completed.json()["facets"] == [{"key": "support mode", "options": ["A", "B"]}]
    assert completed.json()["querySnapshot"] is not None
    assert repeated.json()["state"] == "ready"
    assert owner.refresh_calls == [("coco", "secret")]
    assert owner.sync_calls == [({}, "")]
    assert submitted == []


def test_project_facts_polling_reads_only_background_status() -> None:
    class Facts(ReadyFactsOwner):
        def __init__(self):
            super().__init__()
            self.query_calls = 0

        def query(self, *_args, **_kwargs):
            self.query_calls += 1
            return self.result

    facts = Facts()
    refresh = BackgroundFactsRefresh(submit=lambda _work: None)
    client = _authenticated_client(create_app(
        project_facts_owner=lambda: facts,
        authenticator=FakeAuthenticator,
        facts_refresh=lambda: refresh,
    ))

    response = client.get("/api/confluence/project-facts/status")

    assert response.json() == {"state": "idle", "completed": 0, "total": 0}
    assert facts.query_calls == 0


def test_failed_apply_stops_without_retry() -> None:
    class FailedOwner(ReadyFactsOwner):
        def refresh(self, username, password):
            super().refresh(username, password)
            raise RuntimeError("remote unavailable")

    owner = FailedOwner({
        "state": "no_snapshot", "facets": [], "projects": [], "ownerHierarchy": [],
    })
    submitted = []
    refresh = BackgroundFactsRefresh(submit=submitted.append)
    client = _authenticated_client(create_app(
        project_facts_owner=lambda: owner,
        authenticator=FakeAuthenticator,
        facts_refresh=lambda: refresh,
    ))

    assert client.get("/api/confluence/project-facts").json()["state"] == "no_snapshot"
    assert client.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""}).json()["state"] == "loading"
    submitted.pop()()
    assert client.get("/api/confluence/project-facts").json()["state"] == "failed"
    assert client.get("/api/confluence/project-facts").json()["state"] == "failed"
    assert owner.refresh_calls == [("coco", "secret")]
    assert submitted == []


def test_confluence_request_logs_one_safe_request_record(monkeypatch) -> None:
    import smarttest_web.app as app_module

    records = []
    monkeypatch.setattr(
        app_module, "smart_log", lambda message, **kwargs: records.append((message, kwargs)),
    )
    owner = ReadyFactsOwner({
        "state": "ready", "facets": [], "projects": [], "ownerHierarchy": [],
    })
    client = _authenticated_client(app_module.create_app(
        project_facts_owner=lambda: owner, authenticator=FakeAuthenticator,
    ))
    records.clear()

    response = client.get(
        "/api/confluence/project-facts?field.support%20mode=A&"
        "field.project%20owner=Private%20Person&field.project%20id=SECRET-PROJECT&"
        "search=Private%20Person"
    )

    assert response.status_code == 200
    request_records = [item for item in records if item[1].get("source") == "request"]
    assert len(request_records) == 1
    message, record = request_records[0]
    assert record["source"] == "request"
    assert record["extra"]["path"] == "/api/confluence/project-facts"
    assert record["extra"]["status"] == 200
    assert any(item[1].get("source") == "session_resolve" for item in records)
    timing = next(item for item in records if item[0] == "Confluence filter API timing")
    assert timing[1]["extra"] == {
        "stage": "filter.api_total", "duration_ms": timing[1]["extra"]["duration_ms"],
        "request_state": "ready", "refresh_state": "idle", "credential_present": True,
        "project_count": 0,
    }
    assert "Private Person" not in str(records) and "SECRET-PROJECT" not in str(records)


def test_jira_reports_are_listed_previewed_and_downloaded(tmp_path) -> None:
    report = tmp_path / "jira_format_audit_20260826_143000.xlsx"
    _workbook(report, [["Metric", "Value"], ["Issue count", 3]])
    owner = ClientAuditReportOwner(jira_dir=tmp_path, confluence_dir=None)
    client = _authenticated_client(create_app(
        report_owner=lambda: owner, project_facts_owner=ReadyFactsOwner,
        authenticator=FakeAuthenticator,
        download_service=lambda: DownloadArtifactService(tmp_path / "downloads"),
    ))

    listed = client.get("/api/report-workspaces/jira")
    report_id = listed.json()["reports"][0]["id"]

    assert listed.status_code == 200
    assert client.get(f"/api/report-workspaces/jira/{report_id}").status_code == 200
    prepared = client.post(f"/api/report-workspaces/jira/{report_id}/export")
    assert prepared.status_code == 200
    download = client.get(f"/api/downloads/{prepared.json()['download']['id']}")
    assert download.content == report.read_bytes()
    assert client.get(f"/api/report-workspaces/jira/{report_id}/download").status_code == 404


def test_report_workspace_exposes_empty_partial_and_not_found_states(tmp_path) -> None:
    owner = ClientAuditReportOwner(jira_dir=tmp_path, confluence_dir=None)
    client = TestClient(create_app(report_owner=lambda: owner, project_facts_owner=ReadyFactsOwner))
    assert client.get("/api/report-workspaces/jira").json()["state"] == "empty"
    assert client.get("/api/report-workspaces/confluence").status_code == 404

    _workbook(tmp_path / "jira_format_audit_20260826_143000.xlsx", [["Metric", "Value"]])
    (tmp_path / "jira_format_audit_20260825_143000.xlsx").write_bytes(b"not an xlsx")
    assert client.get("/api/report-workspaces/jira").json()["state"] == "partial_success"
    assert client.get("/api/report-workspaces/jira/not-a-report").status_code == 404


def test_jira_generated_reports_are_filtered_by_exported_jql(tmp_path) -> None:
    _workbook(tmp_path / "jira_format_audit_20260826_143000.xlsx", [
        ["Metric", "Value"], ["JQL 查询条件", "project = TV AND issuetype = Bug"],
    ])
    owner = ClientAuditReportOwner(jira_dir=tmp_path, confluence_dir=None)
    client = TestClient(create_app(report_owner=lambda: owner, project_facts_owner=ReadyFactsOwner))

    matched = client.get(
        "/api/report-workspaces/jira", params={"jql": "project = TV AND issuetype = Bug"},
    ).json()
    missing = client.get("/api/report-workspaces/jira", params={"jql": "project = OTT"}).json()

    assert matched["state"] == "ready"
    assert missing["state"] == "empty"


def test_report_access_denial_is_an_explicit_safe_state() -> None:
    class DeniedOwner:
        def list_reports(self, source, filters):
            raise PermissionError("private personnel scope")

    response = TestClient(create_app(
        report_owner=lambda: DeniedOwner(), project_facts_owner=ReadyFactsOwner,
    )).get("/api/report-workspaces/jira")

    assert response.status_code == 403
    assert response.json() == {"detail": {"state": "unauthorized"}}
