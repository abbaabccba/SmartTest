from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from core.async_tasks import CancellationToken, TaskCancelled, cancellation_context
from smarttest_web import release_api
from smarttest_web.confluence_api import _with_sync_state
from smarttest_web.daily_refresh import DailyFactsRefresh


def test_release_sync_failure_keeps_server_scoped_database_payload(monkeypatch):
    from core.async_tasks import AsyncTaskSnapshot
    access = SimpleNamespace(session_hash="session", require_active=lambda: None, ids=lambda *_: {"allowed"})
    monkeypatch.setattr(release_api, "confluence_access", lambda *_: access)
    monkeypatch.setattr(release_api, "request_session_id", lambda *_: "session")
    selection = SimpleNamespace(project_ids=("allowed",), scope="scope", updated_at="revision")
    snapshots = SimpleNamespace(get=lambda _: selection)
    saved = {"releases": [{"projectId": "allowed", "releaseName": "Saved release"}]}
    releases = Mock()
    releases.dashboard.return_value = saved
    tasks = SimpleNamespace(submit_coordinator=lambda _label, runner: runner,
        task_id=lambda _future: "task", snapshot=lambda _: AsyncTaskSnapshot("task", "sync", "completed", (0, 0), "task"),
        result=lambda _: "failed")
    monkeypatch.setattr(release_api, "WEB_TASKS", tasks)
    index = SimpleNamespace(replace=lambda *_: None, owns=lambda *_: True)
    router = release_api.create_router(lambda: None, None, None, snapshots, releases, index, None, None, None, None)
    endpoints = {route.path: route.endpoint for route in router.routes}
    request = Request({"type": "http", "method": "GET"})
    credentials = SimpleNamespace(password="secret", username="account")
    started = endpoints["/api/dashboard/releases/sync"](request, credentials)
    terminal = endpoints["/api/dashboard/releases/sync/{task_id}"]("task", request, credentials)
    replay = endpoints["/api/dashboard/releases"](request, credentials)
    assert terminal["syncState"] == "failed"
    assert started["dataState"] == replay["dataState"] == "ready"
    assert replay["releases"] == saved["releases"]
    assert all(call.kwargs["project_ids"] == ("allowed",) for call in releases.dashboard.call_args_list)
    snapshots.get = lambda _: None
    with pytest.raises(release_api.HTTPException) as rejected:
        endpoints["/api/dashboard/releases"](request, credentials)
    assert rejected.value.status_code == 409


@pytest.mark.parametrize("source_state,available", [("ready", "ready"), ("no_snapshot", "none")])
def test_projects_refresh_failure_does_not_redefine_data_availability(source_state, available):
    result = _with_sync_state({"state": source_state, "projects": []}, "failed")
    assert result["state"] == "failed" and result["dataState"] == available


def test_daily_cancellation_stops_remaining_sources_and_cleans_session(monkeypatch):
    from smarttest_web import daily_refresh
    monkeypatch.setattr(daily_refresh, "REFRESH_ITEMS", [{"handler": "first"}, {"handler": "second"}])
    refresh = DailyFactsRefresh.__new__(DailyFactsRefresh)
    refresh.sessions = Mock()
    refresh.sessions.saved_credentials.return_value = ("account", "secret")
    refresh.clock = Mock()
    first = Mock(side_effect=TaskCancelled())
    second = Mock()
    refresh.handlers = {"first": first, "second": second}
    with pytest.raises(TaskCancelled):
        refresh.refresh_account("account", CancellationToken(), lambda *_: None)
    first.assert_called_once()
    second.assert_not_called()
    refresh.sessions.delete.assert_called_once()
    refresh.clock.finish.assert_called_once()


def test_cancelled_release_read_cannot_publish_mapped_rows():
    from smarttest_web.jira.cache_service import JiraIssueCacheService
    token = CancellationToken()
    repository = Mock()
    gateway = SimpleNamespace(search_release_issues=lambda *_: {"issues": [{"fields": {}}]})
    def mapped(_row):
        token.cancel()
        return SimpleNamespace(identity=SimpleNamespace(key="issue"))
    service = JiraIssueCacheService(gateway, SimpleNamespace(from_search=mapped), repository)
    with cancellation_context(token), pytest.raises(TaskCancelled):
        service.refresh_release_issues("private scope")
    repository.save_core.assert_not_called()


def test_cancelled_confluence_catalog_does_not_publish():
    from smarttest_web.confluence.cache_service import ConfluenceProjectCacheService
    token = CancellationToken()
    repository = Mock()
    def fetch(_scope):
        token.cancel()
        return {"projects": [], "complete_spaces": []}
    service = ConfluenceProjectCacheService(SimpleNamespace(refresh_project_catalogs=fetch), Mock(), repository, access=Mock())
    with cancellation_context(token), pytest.raises(TaskCancelled):
        service.refresh_projects(None)
    repository.save_core.assert_not_called()
