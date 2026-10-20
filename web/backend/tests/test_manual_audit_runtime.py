from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from smarttest_web.audit.registry import (
    AuditConflictError,
    AuditNotFoundError,
    ManualAuditRegistry,
)
from smarttest_web.downloads import DownloadArtifactService, DownloadNotFoundError
from smarttest_web.audit_http import audit_task_payload as _audit_task_payload
import smarttest_web.app as web_app
from core.async_tasks import AsyncTaskSnapshot



def test_audit_status_includes_its_manager_root_snapshot_without_internal_id(monkeypatch) -> None:
    registry = ManualAuditRegistry()
    task = registry.create("confluence", "session-a", lambda *_: "ok")
    task.manager_task_id = "audit-root"

    class Tasks:
        def snapshot(self, task_id):
            assert task_id == "audit-root"
            return AsyncTaskSnapshot("audit-root", "Weekly review", "running", (3, 12), "audit-root", revision=5)

    task.manager = Tasks()
    data = _audit_task_payload(task)
    assert data["task"] == {
        "state": "running", "progress": {"processed": 3, "total": 12},
        "revision": 5, "visibleChild": None,
    }
    assert "id" not in data["task"]


def test_registry_isolates_sessions_and_rejects_only_same_source_active_task() -> None:
    release = Event()
    registry = ManualAuditRegistry()
    first = registry.create("jira", "session-a", lambda _token, _progress: release.wait(2))

    with pytest.raises(AuditConflictError):
        registry.create("jira", "session-a", lambda *_: None)
    confluence = registry.create("confluence", "session-a", lambda *_: "ok")
    other = registry.create("jira", "session-b", lambda *_: "ok")
    with pytest.raises(AuditNotFoundError):
        registry.get(first.id, "session-b")

    release.set()
    assert registry.wait(first.id, "session-a").status == "completed"
    assert registry.wait(confluence.id, "session-a").status == "completed"
    assert registry.wait(other.id, "session-b").status == "completed"


def test_registry_finalizes_a_successful_task_once_and_publishes_download() -> None:
    finalized = []
    registry = ManualAuditRegistry()
    task = registry.create(
        "jira", "session-a", lambda _token, progress: (
            progress("rule_auditing", 2, 2), "report"
        )[1],
        finalizer=lambda owned, result: finalized.append((owned.id, result)) or "download-1",
    )

    completed = registry.wait(task.id, "session-a")

    assert completed.status == "completed"
    assert completed.stage == "exporting"
    assert completed.processed == 2
    assert completed.total == 2
    assert completed.download_id == "download-1"
    assert finalized == [(task.id, "report")]


def test_registry_cancel_stops_runner_at_next_token_boundary() -> None:
    entered = Event()
    proceed = Event()
    calls = []

    def run(token, _progress):
        calls.append("first")
        entered.set()
        proceed.wait(2)
        token.raise_if_cancelled()
        calls.append("second")

    registry = ManualAuditRegistry()
    finalized = []
    task = registry.create(
        "confluence", "session-a", run,
        finalizer=lambda *_: finalized.append(True) or "download",
    )
    assert entered.wait(1)
    registry.cancel(task.id, "session-a")
    proceed.set()

    assert registry.wait(task.id, "session-a").status == "cancelled"
    assert calls == ["first"]
    assert finalized == []
    assert task.download_id == ""


def test_download_service_enforces_session_and_cleans_owned_files(tmp_path) -> None:
    service = DownloadArtifactService(tmp_path / "downloads")
    path = service.root / "result.xlsx"
    path.write_bytes(b"xlsx")
    artifact = service.register(
        "session-a", path, "result.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert service.get(artifact.id, "session-a").file_path == path
    with pytest.raises(DownloadNotFoundError):
        service.get(artifact.id, "session-b")
    service.clear_session("session-a")

    assert not path.exists()
    with pytest.raises(DownloadNotFoundError):
        service.get(artifact.id, "session-a")


def test_download_service_removes_all_artifacts_on_normal_close(tmp_path) -> None:
    service = DownloadArtifactService(tmp_path / "downloads")
    artifact_path = service.task_dir("audit") / "result.xlsx"
    artifact_path.write_bytes(b"xlsx")
    service.register("session-a", artifact_path, "result.xlsx", "application/xlsx")

    service.close()

    assert service.root.exists()
    assert list(service.root.iterdir()) == []
