import pytest
from requests.exceptions import HTTPError, ProxyError, ReadTimeout, SSLError
from types import SimpleNamespace

from core import read_requests as owner
from core.async_tasks import CancellationToken, TaskCancelled, cancellation_context


@pytest.mark.parametrize("error", [ProxyError("private"), ReadTimeout("private"), HTTPError(response=SimpleNamespace(status_code=503))])
def test_read_budget_and_fixed_delays_preserve_error(monkeypatch, error):
    waits, calls = [], []
    monkeypatch.setattr(owner, "sleep", waits.append)
    def operation():
        calls.append(1)
        raise error
    with pytest.raises(type(error)) as result:
        owner.read_request(operation)
    assert result.value is error
    assert len(calls) == 4
    assert waits == [1, 2, 4]


@pytest.mark.parametrize("error", [SSLError("certificate"), ValueError("invalid query"), HTTPError(response=SimpleNamespace(status_code=401))])
def test_deterministic_errors_are_not_retried(monkeypatch, error):
    monkeypatch.setattr(owner, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(type(error)) as caught:
        owner.read_request(lambda: (_ for _ in ()).throw(error))
    assert caught.value is error


def test_429_header_wait_still_has_four_request_limit(monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr(owner, "sleep", waits.append)
    def operation():
        calls.append(1)
        raise HTTPError(response=SimpleNamespace(status_code=429, headers={"Retry-After": "7"}))
    with pytest.raises(HTTPError):
        owner.read_request(operation)
    assert len(calls) == 4 and waits == [7, 7, 7]


def test_cancel_during_backoff_and_after_inflight_return_stops_processing():
    token = CancellationToken()
    calls = []
    def operation():
        calls.append(1)
        raise ProxyError()
    token.wait = lambda _: (token.cancel(), token.raise_if_cancelled())
    with cancellation_context(token), pytest.raises(TaskCancelled):
        owner.read_request(operation)
    assert len(calls) == 1
    token = CancellationToken()
    def inflight():
        token.cancel()
        return "must not publish"
    with cancellation_context(token), pytest.raises(TaskCancelled):
        owner.read_request(inflight)


@pytest.mark.parametrize("header", ["bad-date", "-1", ""])
def test_invalid_retry_after_uses_fixed_policy(monkeypatch, header):
    waits = []
    monkeypatch.setattr(owner, "sleep", waits.append)
    error = HTTPError(response=SimpleNamespace(status_code=429, headers={"Retry-After": header}))
    with pytest.raises(HTTPError):
        owner.read_request(lambda: (_ for _ in ()).throw(error))
    assert waits == [1, 2, 4]


def test_full_search_thread_pool_receives_task_token_before_more_pages_are_read():
    from core.jira.gateway import JiraGateway
    token = CancellationToken("task-cancel")
    calls = []
    class Api:
        def jql(self, _query, **kwargs):
            calls.append(kwargs["start"])
            token.cancel()
            return {"issues": [], "total": 1000}
    gateway = JiraGateway("https://jira.example", "u", "p", api=Api())
    try:
        with cancellation_context(token), pytest.raises(TaskCancelled):
            gateway.search_all_payloads("private query")
        assert calls == [0]
    finally:
        gateway._full_search_executor.shutdown(wait=True)


def test_confluence_read_cancellation_is_not_swallowed_by_role_name_fallback():
    # The public role resolver must not convert cooperative cancellation to an empty user name.
    token = CancellationToken()
    token.cancel()
    from core.confluence.gateway import ConfluenceGateway, ConfluenceGatewayConfig
    gateway = ConfluenceGateway(ConfluenceGatewayConfig("https://confluence.example"), "u", "p",
                                api=SimpleNamespace(get_user_details_by_userkey=lambda _: pytest.fail("cancelled request")))
    from core.confluence.role_parser import resolve_role_display_names
    with cancellation_context(token), pytest.raises(TaskCancelled):
        resolve_role_display_names(gateway, {"QA": [{"identity": "private identity", "name": ""}]}, {})


def test_retry_after_http_date_is_honored(monkeypatch):
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone
    waits = []
    monkeypatch.setattr(owner, "sleep", waits.append)
    error = HTTPError(response=SimpleNamespace(status_code=429, headers={
        "Retry-After": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)}))
    with pytest.raises(HTTPError):
        owner.read_request(lambda: (_ for _ in ()).throw(error))
    assert len(waits) == 3 and all(18 <= seconds <= 20 for seconds in waits)


def test_task_cancellation_wakes_actual_backoff_and_never_reruns_runner():
    from threading import Event
    from core.async_tasks import AsyncTaskManager
    entered = Event()
    manager = AsyncTaskManager(max_workers=1)
    calls = []
    def operation():
        calls.append(1)
        entered.set()
        raise ProxyError()
    def runner(_token, _progress):
        return owner.read_request(operation)
    future = manager.submit("read", runner)
    try:
        assert entered.wait(2)
        manager.cancel(manager.task_id(future))
        with pytest.raises(TaskCancelled):
            future.result(timeout=2)
        assert calls == [1]
    finally:
        manager.close()


def test_jira_create_action_is_never_retried(monkeypatch):
    from unittest.mock import Mock
    from core.jira.gateway import JiraGateway, JiraGatewayError
    from core.jira.commands import CreateIssueCommand
    api = Mock()
    api.create_issue.side_effect = ProxyError("private")
    gateway = JiraGateway("https://jira.example", "u", "p", api=api)
    with pytest.raises(JiraGatewayError):
        gateway.create_issue(CreateIssueCommand("SH", "Bug", "example"))
    api.create_issue.assert_called_once()
    gateway._full_search_executor.shutdown(wait=True)
