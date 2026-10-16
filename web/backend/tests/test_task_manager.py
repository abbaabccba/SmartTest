from core.async_tasks import AsyncTaskManager
from smarttest_web.task_manager import ScopedTaskIndex, WebTaskManager


def test_scope_index_replaces_and_cancels_only_the_previous_owned_task() -> None:
    manager = AsyncTaskManager(max_workers=1)
    index = ScopedTaskIndex(manager)
    cancelled = []
    manager.cancel = lambda task_id: cancelled.append(task_id) or True

    index.replace(("session-a", "card-a"), "first")
    index.replace(("session-a", "card-a"), "second")
    index.replace(("session-b", "card-a"), "other")

    assert cancelled == ["first"]
    assert index.owns(("session-a", "card-a"), "second")
    assert not index.owns(("session-b", "card-a"), "second")
    manager.close()


def test_scope_index_clear_cancels_all_tasks_for_one_session_only() -> None:
    manager = AsyncTaskManager(max_workers=1)
    index = ScopedTaskIndex(manager)
    cancelled = []
    manager.cancel = lambda task_id: cancelled.append(task_id) or True
    index.replace(("session-a", "one"), "a1")
    index.replace(("session-a", "two"), "a2")
    index.replace(("session-b", "one"), "b1")

    index.clear_prefix("session-a")

    assert set(cancelled) == {"a1", "a2"}
    assert index.owns(("session-b", "one"), "b1")
    manager.close()


def test_web_task_manager_reopens_with_a_fresh_owner_after_lifespan_close() -> None:
    tasks = WebTaskManager(lambda: AsyncTaskManager(max_workers=1))
    first = tasks.submit("first", lambda _token, _progress: "done")
    assert first.result(timeout=1) == "done"
    tasks.close()
    tasks.open()
    second = tasks.submit("second", lambda _token, _progress: "again")
    assert second.result(timeout=1) == "again"
    tasks.close()


def test_app_acquires_web_tasks_only_inside_lifespan(
    monkeypatch, tmp_path, *, database_factory
) -> None:
    from fastapi.testclient import TestClient
    import smarttest_web.task_manager as task_manager
    from smarttest_web.app import create_app
    from smarttest_web.session import PersistentSessionStore
    from test_web_session import FakeAuthenticator, FakeFactsOwner

    events = []
    monkeypatch.setattr(task_manager, "open_web_tasks", lambda: events.append("open"))
    monkeypatch.setattr(task_manager, "close_web_tasks", lambda: events.append("close"))

    app = create_app(
        authenticator=FakeAuthenticator,
        session_store=lambda: PersistentSessionStore(database=database_factory()),
        project_facts_owner=FakeFactsOwner,
    )
    assert events == []

    with TestClient(app):
        assert events == ["open"]
    assert events == ["open", "close"]
