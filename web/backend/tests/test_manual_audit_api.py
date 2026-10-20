from __future__ import annotations
from sqlalchemy import text

from dataclasses import dataclass
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
from openpyxl import Workbook

from core.jira.audit.models import AuditReport, JiraAuditScope
from smarttest_web.app import create_app
from smarttest_web.audit.registry import ManualAuditRegistry
from smarttest_web.background_refresh import BackgroundFactsRefresh
from smarttest_web.downloads import DownloadArtifactService
from test_web_session import FakeAuthenticator
from smarttest_web.session import PersistentSessionStore


class _Facts:
    def query(self, _username, **_kwargs):
        return {"state": "ready", "facets": [], "projects": [{"project_id": "P1"}], "ownerHierarchy": []}
    def sync_details(self, _access, _password, **_kwargs):
        return {"state": "ready", "projects": [{"project_id": "P1"}]}
    def refresh(self, _access, _password): return None
    def invalidate_project(self, _project_id): pass


class JiraOwner:
    def __init__(self): self.runs = 0; self.exports = 0; self.last_scope = None
    def resolve(self, text):
        if not str(text).strip(): raise ValueError("invalid_input")
        return JiraAuditScope("jql", text, text)
    def resolve_filter(self, snapshot):
        from smarttest_web.audit.jira_adapter import jira_filter_jql
        return self.resolve(jira_filter_jql(snapshot.filters, snapshot.jql))
    def run(self, scope, cancellation, progress):
        self.runs += 1; self.last_scope = scope; progress("fetching_issues", 1, 1)
        return AuditReport(scope, __import__("datetime").datetime.now(), (), ())
    def export(self, _report, output_path):
        self.exports += 1; Workbook().save(output_path); return output_path


@dataclass(frozen=True)
class Batch:
    id: str = "batch"


class ConfluenceOwner:
    def resolve(self, payload):
        if payload.get("startDate") >= payload.get("endDate"): raise ValueError("invalid_input")
        return payload
    def run(self, resolved, cancellation, progress):
        progress("rule_auditing", 1, 1); return Batch()
    def export(self, _batch, output_dir):
        paths = []
        for line in ("DOPL", "TV"):
            path = Path(output_dir) / f"{line}_batch.xlsx"
            Workbook().save(path); paths.append(path)
        return paths


def _clients(tmp_path):
    jira, confluence = JiraOwner(), ConfluenceOwner()
    app = create_app(
        authenticator=FakeAuthenticator, project_facts_owner=_Facts,
        audit_registry=lambda: ManualAuditRegistry(),
        download_service=lambda: DownloadArtifactService(tmp_path / "downloads"),
        jira_audit_owner=lambda _username, _password: jira,
        confluence_audit_owner=lambda _username, _password: confluence,
        facts_refresh=lambda: BackgroundFactsRefresh(submit=lambda work: work()),
    )
    first = TestClient(app, base_url="https://testserver")
    second = TestClient(app, base_url="https://testserver")
    first.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    second.post("/api/auth/login", json={"username": "other", "password": "secret"})
    return first, second, jira


def _wait(client, source, audit_id):
    for _ in range(100):
        payload = client.get(f"/api/audits/{source}/{audit_id}").json()
        if payload["status"] not in {"queued", "running"}: return payload
        time.sleep(.01)
    raise AssertionError("audit did not finish")


def test_jira_audit_runs_and_exports_once_before_session_scoped_download(tmp_path) -> None:
    client, other, owner = _clients(tmp_path)
    client.put("/api/jira/filter-snapshot", json={"filters": {}, "jql": ""})
    assert client.post("/api/audits/jira", json={}).status_code == 422
    client.put("/api/jira/filter-snapshot", json={"filters": {"project": ["SH"]}, "jql": ""})
    created = client.post("/api/audits/jira", json={}).json()
    audit_id = created["auditId"]
    completed = _wait(client, "jira", audit_id)

    assert {key: completed[key] for key in ("auditId", "source", "status", "stage", "progress", "errorCode")} == {
        "auditId": audit_id, "source": "jira", "status": "completed",
        "stage": "exporting", "progress": {"processed": 1, "total": 1},
        "errorCode": "",
    }
    assert completed["task"] == {
        "state": "completed", "progress": {"processed": 1, "total": 1},
        "revision": completed["task"]["revision"], "visibleChild": None,
    }
    assert "id" not in completed["task"]
    assert owner.runs == 1
    assert owner.exports == 1
    assert client.post(f"/api/audits/jira/{audit_id}/confirm").status_code == 404
    artifact = client.post(f"/api/audits/jira/{audit_id}/export").json()["download"]
    repeated = client.post(f"/api/audits/jira/{audit_id}/export").json()["download"]

    assert artifact == repeated
    assert owner.exports == 1
    assert client.get(f"/api/downloads/{artifact['id']}").content[:2] == b"PK"
    assert other.get(f"/api/downloads/{artifact['id']}").status_code == 404


def test_cancelled_jira_audit_never_exports_or_enables_download(tmp_path) -> None:
    from threading import Event

    entered, release = Event(), Event()
    class BlockingJiraOwner(JiraOwner):
        def run(self, scope, cancellation, progress):
            self.runs += 1
            progress("fetching_issues", 0, 0)
            entered.set(); release.wait(2)
            cancellation.raise_if_cancelled()

    owner = BlockingJiraOwner()
    app = create_app(
        authenticator=FakeAuthenticator, project_facts_owner=_Facts,
        audit_registry=lambda: ManualAuditRegistry(),
        download_service=lambda: DownloadArtifactService(tmp_path / "downloads"),
        jira_audit_owner=lambda _username, _password: owner,
        confluence_audit_owner=lambda _username, _password: ConfluenceOwner(),
    )
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    client.put("/api/jira/filter-snapshot", json={"filters": {"project": ["SH"]}, "jql": ""})
    audit_id = client.post("/api/audits/jira", json={}).json()["auditId"]
    assert entered.wait(1)

    client.post(f"/api/audits/jira/{audit_id}/cancel")
    release.set()
    completed = _wait(client, "jira", audit_id)

    assert completed["status"] == "cancelled"
    assert owner.exports == 0
    assert client.post(f"/api/audits/jira/{audit_id}/export").status_code == 409


def test_jira_export_failure_is_a_terminal_failure_without_download(tmp_path) -> None:
    class FailedExportOwner(JiraOwner):
        def export(self, _report, _output_path):
            self.exports += 1
            raise OSError("workbook unavailable")

    owner = FailedExportOwner()
    app = create_app(
        authenticator=FakeAuthenticator, project_facts_owner=_Facts,
        audit_registry=lambda: ManualAuditRegistry(),
        download_service=lambda: DownloadArtifactService(tmp_path / "downloads"),
        jira_audit_owner=lambda _username, _password: owner,
        confluence_audit_owner=lambda _username, _password: ConfluenceOwner(),
    )
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    client.put("/api/jira/filter-snapshot", json={"filters": {"project": ["SH"]}, "jql": ""})
    audit_id = client.post("/api/audits/jira", json={}).json()["auditId"]

    completed = _wait(client, "jira", audit_id)

    assert completed["status"] == "failed"
    assert completed["errorCode"] == "export_failed"
    assert owner.exports == 1
    assert client.post(f"/api/audits/jira/{audit_id}/export").status_code == 409


def test_confluence_audit_exports_one_zip_containing_product_line_workbooks(tmp_path) -> None:
    client, _other, _owner = _clients(tmp_path)
    client.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""})
    created = client.post("/api/audits/confluence", json={
        "projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24",
    }).json()
    audit_id = created["auditId"]
    assert _wait(client, "confluence", audit_id)["status"] == "completed"

    artifact = client.post(f"/api/audits/confluence/{audit_id}/export").json()["download"]
    response = client.get(f"/api/downloads/{artifact['id']}")

    import re
    assert re.fullmatch(
        r"Confluence_Weekly_Review_2026-08-17_2026-08-24_\d{8}_\d{6}_\d{6}\.zip",
        artifact["fileName"],
    )
    repeated = client.post(f"/api/audits/confluence/{audit_id}/export").json()["download"]
    assert repeated == artifact
    next_audit = client.post("/api/audits/confluence", json={
        "projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24",
    }).json()["auditId"]
    assert _wait(client, "confluence", next_audit)["status"] == "completed"
    next_artifact = client.post(f"/api/audits/confluence/{next_audit}/export").json()["download"]
    assert next_artifact["fileName"] != artifact["fileName"]
    assert response.headers["content-type"] == "application/zip"
    import zipfile, io
    assert sorted(zipfile.ZipFile(io.BytesIO(response.content)).namelist()) == [
        "DOPL_batch.xlsx", "TV_batch.xlsx",
    ]


def test_confluence_review_queries_current_filters_records_snapshot_and_ignores_client_ids(
    tmp_path, isolate_server_credentials, *, database_factory
) -> None:
    class Facts:
        def __init__(self): self.query_calls = []; self.refresh_calls = 0
        def refresh(self, _access, _password): self.refresh_calls += 1
        def query(self, _access, *, filters=None, search="", **_kwargs):
            self.query_calls.append((filters, search))
            project_id = "P652" if filters == {"project id": ("P652",)} else "P156"
            return {"state": "ready", "facets": [], "projects": [{
                "identity": f"DOPL:{project_id}", "project_id": project_id,
            }], "ownerHierarchy": []}

    class Owner(ConfluenceOwner):
        def __init__(self): self.resolved = []
        def resolve(self, payload):
            self.resolved.append(payload)
            return super().resolve(payload)

    facts, owner = Facts(), Owner()
    app = create_app(
        authenticator=FakeAuthenticator, project_facts_owner=lambda: facts,
        facts_refresh=lambda: BackgroundFactsRefresh(submit=lambda work: work()),
        audit_registry=lambda: ManualAuditRegistry(),
        download_service=lambda: DownloadArtifactService(tmp_path / "downloads"),
        jira_audit_owner=lambda _username, _password: JiraOwner(),
        confluence_audit_owner=lambda _access, _password: owner,
    )
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    review = {
        "projectIds": ["forged"],
        "filters": {"fields": {"project id": ["forged"]}, "search": "mode review"},
        "startDate": "2026-08-17", "endDate": "2026-08-24",
    }

    client.put("/api/confluence/filter-snapshot", json={
        "filters": {"project id": ["P652"]}, "search": "mode review",
    })
    first = client.post("/api/audits/confluence", json=review)
    assert first.status_code == 200
    assert _wait(client, "confluence", first.json()["auditId"])["status"] == "completed"
    assert facts.query_calls[-1] == ({"project id": ("P652",)}, "mode review")
    assert facts.refresh_calls == 0
    assert owner.resolved == [{"projectIds": ["DOPL:P652"], "startDate": "2026-08-17", "endDate": "2026-08-24"}]
    with database_factory().connect() as connection:
        snapshot = connection.execute(
            text(
                "SELECT filters_json, search, project_ids_json FROM web_query_snapshots"
            )
        ).fetchone()
    assert json.loads(snapshot[0]) == {"project id": ["P652"]}
    assert snapshot[1] == "mode review"
    assert json.loads(snapshot[2]) == ["DOPL:P652"]

    second = client.post("/api/audits/confluence", json={**review, "projectIds": ["P156"]})
    assert second.status_code == 200
    assert _wait(client, "confluence", second.json()["auditId"])["status"] == "completed"
    assert facts.refresh_calls == 0
    assert owner.resolved[-1] == {"projectIds": ["DOPL:P652"], "startDate": "2026-08-17", "endDate": "2026-08-24"}


def test_confluence_query_snapshot_survives_app_restart_for_same_valid_session(
    tmp_path, *, database_factory
) -> None:
    class Facts:
        def refresh(self, _access, _password): return None
        def query(self, _access, **_kwargs):
            return {"state": "ready", "facets": [], "projects": [{"project_id": "P156"}], "ownerHierarchy": []}

    class Owner(ConfluenceOwner):
        def __init__(self): self.resolved = []
        def resolve(self, payload): self.resolved.append(payload); return super().resolve(payload)

    database = tmp_path / "web.db"
    store = PersistentSessionStore(database=database_factory())
    owner = Owner()
    def app():
        return create_app(
            authenticator=FakeAuthenticator,
            session_store=lambda: PersistentSessionStore(database=database_factory()),
            project_facts_owner=Facts,
            audit_registry=lambda: ManualAuditRegistry(),
            download_service=lambda: DownloadArtifactService(tmp_path / "downloads"),
            jira_audit_owner=lambda _username, _password: JiraOwner(),
            confluence_audit_owner=lambda _access, _password: owner,
        )

    first = TestClient(app(), base_url="https://testserver")
    first.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    token = first.cookies.get("smarttest_session")
    first.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""})
    restarted = TestClient(app(), base_url="https://testserver")
    restarted.cookies.set("smarttest_session", token)
    response = restarted.post("/api/audits/confluence", json={
        "projectIds": ["forged"], "startDate": "2026-08-17", "endDate": "2026-08-24",
    })

    assert response.status_code == 200
    assert _wait(restarted, "confluence", response.json()["auditId"])["status"] == "completed"
    assert owner.resolved[-1]["projectIds"] == ["P156"]
