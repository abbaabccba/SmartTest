from conftest import confirmed_access
from smarttest_web.database import WebDatabase
from core.async_tasks import AsyncTaskSnapshot
from smarttest_web.background_refresh import BackgroundFactsRefresh
import smarttest_web.background_refresh as background_refresh


def test_failed_refresh_logs_account_and_http_status_without_exception_secrets(tmp_path, monkeypatch):
    from requests import HTTPError
    from types import SimpleNamespace
    access = confirmed_access(WebDatabase(tmp_path / 'access.db'))
    records = []
    monkeypatch.setattr(background_refresh, 'smart_log', lambda message, **kwargs: records.append(kwargs), raising=False)
    submitted = []
    refresh = BackgroundFactsRefresh(submit=submitted.append)

    class Owner:
        def refresh_and_sync_details(self, *_args, **_kwargs):
            raise HTTPError('password=secret cookie=private', response=SimpleNamespace(status_code=503))

    refresh.start_details(Owner(), access, 'secret')
    submitted.pop()()
    assert refresh.state_for(access.session_hash) == 'failed'
    assert len(records) == 1
    assert records[0]['extra']['account'] == access.account
    assert records[0]['extra']['http_status'] == 503
    assert records[0]['extra']['exception_type'] == 'HTTPError'
    assert 'secret' not in repr(records) and 'private' not in repr(records)


def test_scoped_detail_job_is_single_flight_reports_progress_and_can_cancel(tmp_path):
    access = confirmed_access(WebDatabase(tmp_path / 'access.db'))
    submitted = []
    refresh = BackgroundFactsRefresh(submit=submitted.append)

    class Owner:
        def refresh_and_sync_details(self, _username, _password, *, cancelled=None, progress=None, **_kwargs):
            progress(1, 2); assert not cancelled()
            return {"state": "ready", "projects": [{"project_id": "P156"}]}

    published = []
    assert refresh.start_details(Owner(), access, "secret", on_success=lambda: published.append(True))
    assert not refresh.start_details(Owner(), access, "secret")
    submitted.pop()()
    assert refresh.status_for(access.session_hash) == {"state": "ready", "completed": 1, "total": 2}
    assert published == [True]

    assert refresh.start_details(Owner(), access, "secret")
    assert refresh.cancel(access.session_hash)



def test_detail_status_exposes_only_the_session_owned_root_snapshot(tmp_path, monkeypatch):
    access = confirmed_access(WebDatabase(tmp_path / "access.db"))

    class Tasks:
        def task_id(self, _future): return "root-task"
        def snapshot(self, task_id):
            assert task_id == "root-task"
            child = AsyncTaskSnapshot("child", "Fetching project A", "running", (99, 100), "root-task")
            return AsyncTaskSnapshot("root-task", "Confluence details", "running", (2, 10), "root-task", visible_child=child, revision=3)

    monkeypatch.setattr(background_refresh, "WEB_TASKS", Tasks())
    refresh = BackgroundFactsRefresh(submit=lambda _work: object())

    class Owner:
        def refresh_and_sync_details(self, *_args, **_kwargs): return None

    assert refresh.start_details(Owner(), access, "secret")
    status = refresh.status_for(access.session_hash)
    assert status["task"] == {
        "state": "running", "progress": {"processed": 2, "total": 10},
        "revision": 3, "visibleChild": {"label": "Fetching project A", "state": "running"},
    }
    assert "id" not in status["task"]


def test_query_snapshot_persists_across_repository_restart_and_isolates_sessions(tmp_path):
    from smarttest_web.query_snapshot_repository import ConfluenceQuerySnapshotRepository

    from smarttest_web.session import PersistentSessionStore

    database = WebDatabase(tmp_path / "web.db")
    PersistentSessionStore(database.path)
    repository = ConfluenceQuerySnapshotRepository(database)
    repository.record("session-a", {"project id": ("P156",), "support mode": ("A",)}, "", ("P156",), "facts-1", expires_at=100)

    restarted = ConfluenceQuerySnapshotRepository(database)
    assert restarted.get("session-a", expires_at=1).project_ids == ("P156",)
    assert restarted.get("session-a", expires_at=1).filters == {"project id": ["P156"]}
    assert restarted.get("session-b", expires_at=1) is None
    assert restarted.get("session-a", expires_at=101) is None
