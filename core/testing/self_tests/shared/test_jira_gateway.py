from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock, get_ident
from time import sleep as pause

import pytest



from core.jira.commands import CreateIssueCommand
from core.jira.gateway import JiraGateway, JiraGatewayError


class RecordingApi:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def jql(self, jql, *, fields, start, limit, expand=None, validate_query=None):
        self.calls.append(("jql", jql, fields, start, limit, expand, validate_query))
        return {"issues": [], "startAt": start, "maxResults": limit, "total": 0}

    def issue_get_comments(self, issue_key):
        self.calls.append(("comments", issue_key))
        return {"comments": []}

    def get_issue(self, issue_key, *, fields=None, expand=None):
        self.calls.append(("issue", issue_key, fields, expand))
        return {"key": issue_key, "fields": {"description": "body"}}

    def create_issue(self, fields):
        self.calls.append(("create", fields))
        return {"id": "1", "key": "SH-1"}

    def get_all_fields(self):
        self.calls.append(("fields",))
        return [
            {"id": "customfield_101", "name": "Project ID"},
            {"id": "customfield_102", "name": "Software Release"},
            {"id": "customfield_103", "name": "Severity"},
        ]

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        if path == "rest/api/2/jql/autocompletedata":
            return {"visibleFieldNames": [{"value": "status", "displayName": "Status", "types": ["com.atlassian.jira.issue.status.Status"], "operators": ["=", "IN"]}]}
        if path == "rest/api/2/jql/autocompletedata/suggestions":
            return {"results": [{"value": "Open", "displayName": "Open status"}]}
        if path == "rest/api/2/filter/favourite":
            return [{"id": "7", "name": "Mine", "jql": "project = SH"}]
        return {}

    def get_filter(self, filter_id):
        self.calls.append(("filter", filter_id))
        return {"id": str(filter_id), "name": "Mine", "jql": "project = SH"}


def test_custom_fields_map_audit_names_from_response_without_changing_field_ids():
    class Api:
        def get_issue(self, key, *, fields, expand):
            assert fields == ['description', '*all']
            assert expand == 'names'
            return {'fields': {'description': 'body', 'customfield_90001': {'value': 'Self-Test'},
                               'customfield_90002': None, 'customfield_90003': 'other'},
                    'names': {'customfield_90001': 'Channel of Reporter',
                              'customfield_90002': 'Test Case ID', 'customfield_90003': 'Other'}}
    gateway = JiraGateway('https://jira.example', 'u', 'p', api=Api())
    payload = gateway.load_issue_sections('SH-1', ('description', 'custom_fields'))
    assert payload['custom_fields'] == {'customfield_90001': {'value': 'Self-Test'},
        'customfield_90002': None, 'customfield_90003': 'other',
        'Channel of Reporter': {'value': 'Self-Test'}, 'Test Case ID': None}


def test_jira_gateway_search_requests_only_lightweight_core_fields() -> None:
    api = RecordingApi()
    gateway = JiraGateway("https://jira.example", "u", "p", api=api, page_size=25)

    gateway.search_issues("project = SH", page=2)

    call = api.calls[0]
    assert call[:4] == ("jql", "project = SH", list(JiraGateway.CORE_FIELDS), 50)
    assert "comment" not in call[2]
    assert "attachment" not in call[2]
    assert "description" not in call[2]


def test_release_search_resolves_custom_field_ids_from_metadata_and_includes_fix_versions() -> None:
    api = RecordingApi()
    gateway = JiraGateway("https://jira.example", "u", "p", api=api)

    payload = gateway.search_release_issues('"Project ID" = "P100"')

    assert api.calls[0] == ("fields",)
    assert api.calls[1][0:3] == (
        "jql", '"Project ID" = "P100"',
        [*JiraGateway.CORE_FIELDS, "fixVersions", "resolutiondate", "customfield_101", "customfield_102", "customfield_103"],
    )
    assert payload["fieldMetadata"] == {
        "customfield_101": "Project ID", "customfield_102": "Software Release", "customfield_103": "Severity",
    }


def test_jira_gateway_loads_comments_without_fetching_other_sections() -> None:
    api = RecordingApi()
    gateway = JiraGateway("https://jira.example", "u", "p", api=api)

    payload = gateway.load_issue_sections("SH-1", ("comments",))

    assert payload == {"comments": []}
    assert api.calls == [("comments", "SH-1")]


def test_jira_gateway_maps_create_command_to_atlassian_create_issue() -> None:
    api = RecordingApi()
    gateway = JiraGateway("https://jira.example", "u", "p", api=api)

    created = gateway.create_issue(CreateIssueCommand("SH", "Bug", "Broken", labels=("one",)))

    assert created["key"] == "SH-1"
    assert api.calls == [("create", {"project": {"key": "SH"}, "issuetype": {"name": "Bug"}, "summary": "Broken", "description": "", "labels": ["one"]})]


def test_jira_gateway_user_search_exposes_active_identity_for_reconciliation() -> None:
    class UserApi(RecordingApi):
        def get_all_assignable_users_for_project(self, project_key, *, start, limit):
            self.calls.append(("users", project_key, start, limit))
            return [{"name": "new.user", "displayName": "New User", "active": True}]

    api = UserApi()
    users = JiraGateway("https://jira.example", "u", "p", api=api).search_users("new.user")

    assert users == [{
        "account": "new.user", "display_name": "New User", "avatar_url": "", "active": True,
    }]


def test_jira_gateway_reads_user_group_names_from_authenticated_profile() -> None:
    class UserGroupApi(RecordingApi):
        def get(self, path, params=None):
            self.calls.append(("get", path, params))
            return {
                "name": "qa.one",
                "groups": {"items": [{"name": "fae-tv-qa"}, {"name": "jira-users"}]},
            }

    api = UserGroupApi()
    groups = JiraGateway("https://jira.example", "u", "p", api=api).user_groups("qa.one")

    assert groups == ("fae-tv-qa", "jira-users")
    assert api.calls == [
        ("get", "rest/api/2/user", {"username": "qa.one", "expand": "groups"}),
    ]


def test_jira_gateway_normalizes_third_party_failure() -> None:
    class BrokenApi(RecordingApi):
        def jql(self, *args, **kwargs):
            raise RuntimeError("secret transport detail")

    gateway = JiraGateway("https://jira.example", "u", "p", api=BrokenApi())

    with pytest.raises(JiraGatewayError) as error:
        gateway.search_issues("project = SH", page=0)

    assert error.value.code == "jira_search_failed"


def test_filter_read_apis_and_strict_validation_are_read_only() -> None:
    api = RecordingApi()
    gateway = JiraGateway("https://jira.example", "u", "p", api=api)

    assert gateway.fetch_query_fields()["visibleFieldNames"][0]["value"] == "status"
    assert gateway.fetch_saved_filters()[0]["id"] == "7"
    assert gateway.fetch_filter("7")["jql"] == "project = SH"
    assert gateway.validate_jql("project = SH") == {"valid": True, "errors": []}

    assert api.calls[-1] == ("jql", "project = SH", [], 0, 0, None, "strict")


def test_strict_validation_preserves_jira_error_messages() -> None:
    class Response:
        status_code = 400

        @staticmethod
        def json():
            return {"errorMessages": ["Field 'wat' does not exist"], "errors": {"jql": "Invalid JQL"}}

    class Failure(Exception):
        response = Response()

    class InvalidApi(RecordingApi):
        def jql(self, *_args, **_kwargs):
            raise Failure("private transport detail")

    result = JiraGateway("https://jira.example", "u", "p", api=InvalidApi()).validate_jql("wat = 1")

    assert result == {"valid": False, "errors": ["Field 'wat' does not exist", "Invalid JQL"]}


def test_filter_suggestions_come_from_jql_autocomplete_with_the_requested_field_and_query() -> None:
    gateway = JiraGateway("https://jira.example", "u", "p", api=RecordingApi())

    candidates = gateway.fetch_query_suggestions("status", "op")

    assert candidates == {"results": [{"value": "Open", "displayName": "Open status"}]}
    assert gateway._api.calls[-1] == (
        "get", "rest/api/2/jql/autocompletedata/suggestions",
        {"fieldName": "status", "fieldValue": "op"},
    )


@pytest.mark.parametrize("include_comments", [False, True])
def test_full_search_uses_1000_item_pages_with_bounded_independent_clients_and_stable_deduplication(include_comments) -> None:
    lock = Lock()
    active = 0
    peak = 0
    clients = []
    calls = []

    class PagedApi:
        def __init__(self):
            clients.append(self)

        def jql(self, query, *, fields, start, limit, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append((self, get_ident(), query, tuple(fields), start, limit))
            try:
                if start:
                    pause(0.01)
                pages = {
                    0: ({"key": "SH-2"}, {"key": "SH-1"}),
                    1000: ({"key": "SH-3"}, {"key": "SH-2"}),
                    2000: ({"key": "SH-4"},),
                    3000: ({"key": "SH-5"},),
                    4000: ({"key": "SH-6"},),
                }
                # The first page reports 2500; a later page observes growth to 4500.
                total = 4500 if start >= 1000 else 2500
                return {"issues": list(pages.get(start, ())), "startAt": start, "total": total, "maxResults": limit}
            finally:
                with lock:
                    active -= 1

    gateway = JiraGateway(
        "https://jira.example", "u", "p", api=PagedApi(), api_factory=PagedApi,
    )
    progress = []
    fields = [*JiraGateway.CORE_FIELDS, "comment"] if include_comments else None
    rows = gateway.search_all_payloads("project = SH", fields=fields, progress=lambda completed, total: progress.append((completed, total)))
    assert all(requested == tuple(fields or JiraGateway.CORE_FIELDS) for _, _, _, requested, _, _ in calls)

    assert [row["key"] for row in rows] == ["SH-2", "SH-1", "SH-3", "SH-4", "SH-5", "SH-6"]
    assert sorted(start for _, _, _, _, start, _ in calls) == [0, 1000, 2000, 3000, 4000]
    assert all(limit == 1000 for *_, limit in calls)
    assert 1 < peak <= 4
    assert progress[-1] == (6, 4500)
    clients_by_thread = {}
    for client, thread_id, *_ in calls:
        clients_by_thread.setdefault(thread_id, set()).add(id(client))
    assert all(len(thread_clients) == 1 for thread_clients in clients_by_thread.values())
    assert len({next(iter(thread_clients)) for thread_clients in clients_by_thread.values()}) == len(clients_by_thread)


def test_concurrent_full_searches_share_one_four_request_gateway_limit() -> None:
    lock = Lock()
    active = 0
    peak = 0

    class SlowApi:
        def jql(self, query, *, start, limit, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                pause(0.03)
                return {
                    "issues": [{"key": f"{query}-{start}"}],
                    "startAt": start,
                    "total": 5000,
                    "maxResults": limit,
                }
            finally:
                with lock:
                    active -= 1

    gateway = JiraGateway("https://jira.example", "u", "p", api=SlowApi(), api_factory=SlowApi)
    with ThreadPoolExecutor(max_workers=2) as callers:
        results = list(callers.map(gateway.search_all_payloads, ("A", "B")))

    assert all(len(rows) == 5 for rows in results)
    assert 1 < peak <= 4


def test_full_search_uses_shared_four_attempt_budget(monkeypatch) -> None:
    import core.jira.gateway as gateway_module

    delays = []
    from core import read_requests
    monkeypatch.setattr(read_requests, "sleep", delays.append)

    class Response:
        status_code = 502

    class Failure(Exception):
        response = Response()

    class RetryingApi:
        attempts = 0

        def jql(self, _query, **_kwargs):
            type(self).attempts += 1
            if type(self).attempts <= 3:
                raise Failure("temporary")
            return {"issues": [], "startAt": 0, "total": 0, "maxResults": 1000}

    gateway = JiraGateway("https://jira.example", "u", "p", api=RetryingApi(), api_factory=RetryingApi)

    assert gateway.search_all_payloads("project = SH") == []
    assert RetryingApi.attempts == 4
    assert delays == [1, 2, 4]


def test_full_search_does_not_retry_permission_error(monkeypatch) -> None:
    import core.jira.gateway as gateway_module

    delays = []
    from core import read_requests
    monkeypatch.setattr(read_requests, "sleep", delays.append)

    class Response:
        status_code = 403

    class Failure(Exception):
        response = Response()

    class BrokenApi:
        attempts = 0

        def jql(self, _query, **_kwargs):
            type(self).attempts += 1
            raise Failure("unavailable")

    gateway = JiraGateway("https://jira.example", "u", "p", api=BrokenApi(), api_factory=BrokenApi)
    with pytest.raises(JiraGatewayError) as error:
        gateway.search_all_payloads("project = SH")
    assert error.value.code == "jira_search_failed"
    assert BrokenApi.attempts == 1
    assert delays == []


def test_jira_gateway_logs_issue_request_operations_without_response_content(monkeypatch) -> None:
    import core.jira.gateway as gateway_module

    records = []
    monkeypatch.setattr(
        gateway_module,
        "smart_log",
        lambda message, **kwargs: records.append((message, kwargs)),
        raising=False,
    )
    gateway = JiraGateway("https://jira.example", "u", "p", api=RecordingApi())

    gateway.get_issue("SH-1")
    gateway.load_issue_sections("SH-1", ("description",))

    requests = [kwargs["extra"] for message, kwargs in records
                if message == "Jira gateway issue request"]
    assert [item["operation"] for item in requests] == ["get_issue", "load_issue_sections"]
    assert all(item["issue_key"] == "SH-1" for item in requests)
    assert all(item["outcome"] == "success" for item in requests)
    assert requests[1]["sections"] == ["description"]
    assert all(item["duration_ms"] >= 0 for item in requests)
    assert all("body" not in item for item in requests)


@pytest.mark.parametrize("status", [None, 503])
def test_search_failure_logs_safe_page_context_and_preserves_cause(monkeypatch, status):
    import core.jira.gateway as owner
    from types import SimpleNamespace
    from requests.exceptions import ReadTimeout
    records = []
    monkeypatch.setattr(owner, "smart_log", lambda message, **kwargs: records.append(kwargs))
    from core import read_requests
    monkeypatch.setattr(read_requests, "sleep", lambda _: None)
    error = ReadTimeout('secret password and raw response')
    error.response = SimpleNamespace(status_code=status)
    class Api:
        def jql(self, *_args, **_kwargs):
            raise error
    gateway = JiraGateway('https://jira.example', 'u', 'private', api=Api())
    with pytest.raises(JiraGatewayError) as caught:
        gateway._search_full_page('reporter = private', 2000, ['comment','created'], ['changelog'], 1000)
    assert caught.value.__cause__ is error
    extra = records[-1]['extra']
    assert extra['http_status'] == status and extra['cause_type'] == 'ReadTimeout'
    assert extra['start_at'] == 2000 and extra['page_size'] == 1000
    assert extra['duration_ms'] >= 0
    assert 'private' not in str(records) and 'secret' not in str(records)


def test_explicit_card_page_size_keeps_four_workers_and_default_unchanged():
    lock = Lock()
    active = peak = 0
    calls = []
    class Api:
        def jql(self, query, *, start, limit, fields, expand, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append((query, start, limit, fields, expand))
            try:
                pause(.01)
                return {"total": 550, "issues": [{"key": f"TV-{start}"}]}
            finally:
                with lock: active -= 1
    gateway = JiraGateway("https://jira.example", "u", "p", api=Api(), api_factory=Api)
    fields = [*gateway.CORE_FIELDS, "comment"]
    gateway.search_all_payloads("card", fields=fields, expand=["changelog"], page_size=100)
    assert sorted(call[1] for call in calls) == [0, 100, 200, 300, 400, 500]
    assert all(call[2:] == (100, fields, "changelog") for call in calls)
    assert 1 < peak <= 4
    calls.clear()
    gateway.search_all_payloads("default")
    assert len(calls) == 1 and calls[0][2] == 1000


def test_comment_search_has_its_own_twelve_workers_and_completes_truncated_comments():
    lock = Lock()
    active = peak = 0
    calls = []
    class Api:
        def jql(self, query, *, start, limit, fields, **kwargs):
            nonlocal active, peak
            assert kwargs.get('expand') == 'changelog'
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append((start, limit, fields))
            try:
                pause(.01)
                return {"total": 25000, "maxResults": 1000, "issues": [{"id": str(start), "key": f"TV-{start}", "fields": {
                    "created": "2026-01-01", "comment": {"total": 3 if start == 0 else 0,
                        "comments": [{"id": "old"}] if start == 0 else []}},
                    "changelog": {"startAt": 0, "total": 0, "histories": []}}]}
            finally:
                with lock:
                    active -= 1
        def get(self, path, *, params):
            assert path == 'rest/api/2/issue/TV-0/comment'
            assert params['maxResults'] == 1000
            start = params['startAt']
            return {'total': 3, 'comments': [{'id': str(i)} for i in range(start, min(start+2,3))]}
    gateway = JiraGateway('https://jira.example', 'u', 'p', api=Api(), api_factory=Api)
    rows = gateway.search_activity_payloads('created >= "2026-01-01"')
    assert 4 < peak <= 12
    assert all(limit == 1000 and fields == ['comment', 'created'] for _,limit,fields in calls)
    assert [c['id'] for c in rows[0]['fields']['comment']['comments']] == ['0','1','2']
    assert gateway._full_search_executor._max_workers == 4


def test_activity_history_truncation_uses_supported_issue_expand_and_fails_if_still_incomplete():
    class Api:
        def jql(self, *_args, **_kwargs):
            return {'total':1,'issues':[{'id':'1','key':'SH-1','fields':{'created':'2026-01-01','comment':{'comments':[]}},
                'changelog':{'startAt':0,'total':2,'histories':[{'id':'1'}]}}]}
        def get_issue(self, key, *, fields, expand):
            assert key=='SH-1' and fields==['created'] and expand=='changelog'
            return {'changelog':{'startAt':0,'total':2,'histories':[{'id':'1'},{'id':'2'}]}}
    api=Api()
    gateway=JiraGateway('https://jira.example','u','p',api=api)
    assert len(gateway.search_activity_payloads('year')[0]['changelog']['histories'])==2
    api.get_issue=lambda *_args, **_kwargs: {'changelog':{'total':2,'histories':[]}}
    with pytest.raises(JiraGatewayError) as error:
        gateway.search_activity_payloads('year')
    assert error.value.code=='jira_changelog_incomplete'

def test_failed_search_cancels_unstarted_pages_without_replacing_error():
    from concurrent.futures import Future
    failure = JiraGatewayError('jira_search_failed')
    class Executor:
        def __init__(self): self.futures = []
        def submit(self, *args):
            future = Future()
            self.futures.append(future)
            if len(self.futures) == 1:
                future.set_result({'total': 4000, 'issues': []})
            elif len(self.futures) == 2:
                future.set_exception(failure)
            elif len(self.futures) == 3:
                future.set_running_or_notify_cancel()
            return future
    executor = Executor()
    gateway = JiraGateway('https://jira.example', 'u', 'p', api=object())
    with pytest.raises(JiraGatewayError) as caught:
        gateway._collect_search_pages('private query', ['comment'], None, None, 1000, executor)
    assert caught.value is failure
    assert executor.futures[2].running()
    assert executor.futures[3].cancelled()


def test_search_failure_logs_safe_nested_connection_cause_without_exception_text(
    monkeypatch,
):
    from requests.exceptions import ConnectionError
    import core.jira.gateway as owner

    from core import read_requests
    monkeypatch.setattr(read_requests, "sleep", lambda _: None)
    records = []
    root = ConnectionResetError(
        10054, "secret URL https://private:token@example.test?jql=private"
    )
    error = ConnectionError("secret response body", root)

    class Api:
        def jql(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(
        owner, "smart_log", lambda message, **kwargs: records.append(kwargs)
    )
    gateway = JiraGateway("https://jira.example", "private", "secret", api=Api())
    with pytest.raises(JiraGatewayError) as caught:
        gateway._search_full_page(
            "private query", 2300, ["comment", "created"], None, 100
        )
    assert caught.value.__cause__ is error
    extra = records[-1]["extra"]
    assert extra["exception_chain"] == [
        {"type": "ConnectionError"},
        {"type": "ConnectionResetError", "errno": 10054},
    ]
    assert extra["phase"] == "search_page"
    assert extra["field_count"] == 2 and extra["expand_changelog"] is False
    assert extra["start_at"] == 2300
    assert all(
        secret not in str(records)
        for secret in ("private", "secret", "token", "example.test")
    )


def test_successful_search_page_does_not_emit_per_page_logs(monkeypatch):
    import core.jira.gateway as owner
    from unittest.mock import Mock
    logger = Mock()
    monkeypatch.setattr(owner, 'smart_log', logger)
    api = Mock()
    api.jql.return_value = {'issues': [], 'total': 0}
    gateway = JiraGateway('https://jira.example', '', '', api=api)
    assert gateway._search_full_page('private query', 0, ['created'], None, 100) == api.jql.return_value
    logger.assert_not_called()
