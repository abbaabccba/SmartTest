import pytest
from concurrent.futures import Future
from threading import Event
import time

from smarttest_web.jira.analytics_service import JiraAnalyticsService
from core.jira.services.filter_service import build_basic_jql
from core.async_tasks import AsyncTaskManager
from smarttest_web.jira.analytics_tasks import JiraAnalyticsTasks


def test_builds_fixed_basic_jql_deterministically():
    assert build_basic_jql({
        "project": ["SH", "TV"], "issueType": ["Bug"], "status": ["Open"],
        "resolution": ["Unresolved"], "assignee": ["coco"], "containsText": 'wifi "drop"',
    }, {}) == ('project IN ("SH", "TV") AND issuetype IN ("Bug") AND status IN ("Open") '
               'AND assignee IN ("coco") AND text ~ "wifi \\"drop\\"" AND resolution IN ("Unresolved")')


def test_builds_supported_dynamic_fields_and_rejects_advanced_only():
    fields = {"customfield_1": {"control": "number", "queryable": True},
              "customfield_2": {"control": "advanced", "queryable": False}}
    assert build_basic_jql({"more": {"customfield_1": 7}}, fields) == 'customfield_1 = 7'
    try:
        build_basic_jql({"more": {"customfield_2": "x"}}, fields)
    except ValueError as error:
        assert str(error) == "advanced_only:customfield_2"
    else:
        raise AssertionError("advanced-only field accepted")


def test_builds_dynamic_user_values_returned_by_jira_as_a_multi_value_clause():
    fields = {"reporter": {"control": "user", "queryable": True}}

    assert build_basic_jql({"more": {"reporter": ["coco", "mason"]}}, fields) == (
        'reporter IN ("coco", "mason")'
    )


class Repo:
    def __init__(self): self.calls = []
    def begin(self, *args, **kwargs): self.calls.append(("begin", args, kwargs)); return "snapshot"
    def set_task(self, *args, **_kwargs): self.calls.append(("task", args))
    def write_batch(self, *args): self.calls.append(("write", args))
    def update_statistics(self, *args): return True
    def activate(self, *args): self.calls.append(("activate", args)); return True
    def finish(self, *args): self.calls.append(("finish", args))
    def state(self, *_args, **_kwargs): return {"activeSnapshotId": "old", "activeJql": "old", "pendingSnapshotId": "", "taskId": ""}


class Tasks:
    def submit(self, session, runner, *, card_key=""):
        self.runner = runner; return "task-1"


class Filter:
    def fields(self): return []
    def validate(self, jql): return {"valid": jql != "bad", "errors": ["bad jql"] if jql == "bad" else []}


class Gateway:
    def search_all_payloads(self, _jql, *, fields=None, expand=None, page_size=None, progress=None):
        return [{"id": "1", "key": "SH-1", "fields": {"summary": "One", "project": {}, "status": {}, "issuetype": {}}}]


class Mapper:
    def from_search(self, payload): return payload


def test_annual_comment_coordination_reuses_inflight_work_and_isolates_accounts_and_queries():
    manager = AsyncTaskManager(max_workers=1)
    tasks = JiraAnalyticsTasks(manager)
    release = Event()
    def run(token, progress):
        release.wait(2)
        return 'database-snapshot'
    try:
        first = tasks.submit_annual(('alice', 'year-2026', 'scope', 'roster'), run)
        assert tasks.submit_annual(('alice', 'year-2026', 'scope', 'roster'), run) is first
        assert tasks.submit_annual(('bob', 'year-2026', 'scope', 'roster'), run) is not first
        assert tasks.submit_annual(('alice', 'year-2026', 'different', 'roster'), run) is not first
    finally:
        release.set()
        manager.close()


def test_card_tasks_are_independent_with_the_same_session_and_user_conditions():
    manager = AsyncTaskManager(max_workers=2)
    tasks = JiraAnalyticsTasks(manager)
    entered = [Event(), Event()]
    release = Event()
    try:
        def runner(index):
            def run(token, report):
                entered[index].set()
                assert release.wait(2)
                token.raise_if_cancelled()
            return run
        first = tasks.submit("session", runner(0), card_key="first")
        second = tasks.submit("session", runner(1), card_key="second")
        assert all(event.wait(2) for event in entered)
        assert tasks.status("session", first, card_key="second") is None
        assert tasks.cancel("session", second, card_key="first") is False
        release.set()
        for _ in range(100):
            states = [tasks.status("session", first, card_key="first")["state"],
                      tasks.status("session", second, card_key="second")["state"]]
            if states == ["completed", "completed"]:
                break
            time.sleep(0.01)
        assert states == ["completed", "completed"]
    finally:
        release.set()
        manager.close()


def test_search_validates_before_snapshot_and_immediately_submits_task():
    repo, tasks = Repo(), Tasks()
    service = JiraAnalyticsService(Filter(), Gateway(), Mapper(), repo, tasks)

    invalid = service.search("s", "alice", 100, {"mode": "advanced", "jql": "bad"})
    valid = service.search("s", "alice", 100, {"mode": "advanced", "jql": "project = SH"})

    assert invalid["validation"]["valid"] is False
    assert repo.calls[0][0] == "begin"
    assert valid["taskId"] == "task-1"
    tasks.runner(type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)
    assert [call[0] for call in repo.calls] == ["begin", "task", "write", "activate"]


def test_preview_returns_server_built_jql_without_creating_snapshot():
    repo = Repo()
    service = JiraAnalyticsService(Filter(), Gateway(), Mapper(), repo, Tasks())

    result = service.preview({"mode": "basic", "basic": {"project": ["SH"]}})

    assert result == {"valid": True, "errors": [], "jql": 'project IN ("SH")', "userJql": 'project IN ("SH")'}
    assert repo.calls == []


def test_background_failure_is_recorded_and_forwarded_to_auth_lifecycle():
    class BrokenGateway:
        def search_all_payloads(self, _jql, *, fields=None, expand=None, page_size=None, progress=None): raise RuntimeError("offline")
    failures = []; repo, tasks = Repo(), Tasks()
    service = JiraAnalyticsService(Filter(), BrokenGateway(), Mapper(), repo, tasks, on_error=failures.append)
    service.search("s", "alice", 100, {"mode": "advanced", "jql": "project = SH"})

    try: tasks.runner(type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)
    except RuntimeError: pass

    assert repo.calls[-1][0] == "finish"
    assert len(failures) == 1


def test_card_conditions_are_applied_once_for_preview_search_and_database_effective_scope():
    repo, tasks = Repo(), Tasks()
    class RecordingFilter(Filter):
        def validate(self, jql):
            assert jql == '(project = A OR project = B) AND (channel = "Self-Test") ORDER BY created DESC'
            return {"valid": True, "errors": []}
    service = JiraAnalyticsService(RecordingFilter(), Gateway(), Mapper(), repo, tasks,
                                   fixed_conditions='channel = "Self-Test"')
    draft = 'project = A OR project = B ORDER BY created DESC'
    preview = service.preview({"mode": "advanced", "jql": draft})
    service.search("s", "alice", 100, {"mode": "advanced", "jql": draft})
    assert preview["userJql"] == draft
    assert repo.calls[0][1][2] == preview["jql"]
    assert repo.calls[0][2]["user_jql"] == draft


def test_unselected_basic_conditions_add_only_card_fixed_conditions():
    service = JiraAnalyticsService(Filter(), Gateway(), Mapper(), Repo(), Tasks(),
                                   fixed_conditions='channel = "Self-Test"')
    assert service.preview({"mode": "basic", "basic": {}})["jql"] == 'channel = "Self-Test"'


def test_remote_pagination_progress_is_published_before_database_writes():
    repo, tasks = Repo(), Tasks(); events = []
    class PagedGateway:
        def search_all_payloads(self, _jql, *, fields=None, expand=None, page_size=None, progress):
            progress(1, 2)
            assert repo.calls[-1][0] == "task"
            return []
    service = JiraAnalyticsService(Filter(), PagedGateway(), Mapper(), repo, tasks)
    service.search("s", "alice", 100, {"mode": "advanced", "jql": "project = A"})
    tasks.runner(type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *args: events.append(args))
    assert events == [(1, 2)]


def test_card_search_fetches_both_periods_and_activates_one_combined_result():
    repo, tasks = Repo(), Tasks()
    class PairGateway:
        def __init__(self): self.queries = []
        def search_all_payloads(self, jql, *, fields=None, expand=None, page_size=None, progress=None):
            self.queries.append(jql)
            return [{"id": str((len(self.queries) - 1) % 2), "key": f"SH-{(len(self.queries) - 1) % 2}",
                     "fields": {"summary": "One", "project": {}, "status": {}, "issuetype": {}}}]
    gateway = PairGateway()
    service = JiraAnalyticsService(
        Filter(), gateway, Mapper(), repo, tasks,
        fixed_conditions='created >= "2026-09-01" AND created <= now()',
        comparison_conditions='created >= "2026-08-01" AND created < "2026-09-01"',
        statistics_builder=lambda current, previous: {"currentTotal": len(current), "previousTotal": len(previous)},
    )

    service.search("s", "alice", 100, {"mode": "advanced", "jql": "project = SH"})
    tasks.runner(type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)

    assert gateway.queries == [
        '(project = SH) AND (created >= "2026-09-01" AND created <= now())',
        '(project = SH) AND (created >= "2026-08-01" AND created < "2026-09-01")',
    ]
    assert repo.calls[0][2]["comparison_jql"] == gateway.queries[1]
    assert [call[1][2] for call in repo.calls if call[0] == "write"] == ["current", "previous"]
    assert repo.calls[-1] == ("activate", ("snapshot", {"currentTotal": 1, "previousTotal": 1,
        "availability": {period: {"basic": True} for period in ("current", "previous")}}))


def test_comparison_search_reports_monotonic_combined_progress_across_both_queries():
    repo, tasks, events = Repo(), Tasks(), []
    class ProgressGateway:
        def __init__(self): self.call = 0
        def search_all_payloads(self, _jql, *, fields=None, expand=None, page_size=None, progress):
            self.call += 1
            if self.call == 1:
                progress(1, 2); progress(2, 2)
                return [{"id": "1", "key": "A-1", "fields": {}}, {"id": "2", "key": "A-2", "fields": {}}]
            progress(1, 3); progress(3, 3)
            return [{"id": str(index), "key": f"B-{index}", "fields": {}} for index in range(3)]
    service = JiraAnalyticsService(Filter(), ProgressGateway(), Mapper(), repo, tasks,
                                   fixed_conditions="current", comparison_conditions="previous")
    service.search("s", "alice", 100, {"mode": "advanced", "jql": "project = SH"})

    tasks.runner(type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *value: events.append(value))

    assert events == [(1, 2), (2, 2), (3, 5), (5, 5)]


def test_card_load_logs_safe_basic_stage_timings(monkeypatch):
    import smarttest_web.jira.analytics_service as module
    logs, requests = [], []
    monkeypatch.setattr(module, "smart_log", lambda message, **kwargs: logs.append((message, kwargs)), raising=False)
    class Gateway:
        def search_all_payloads(self, _query, **kwargs):
            requests.append(kwargs)
            kwargs["progress"](1, 1)
            kwargs["progress"](1, 1)
            return [{"id": "1", "fields": {"summary": "private-content"}}]
    tasks = Tasks()
    service = JiraAnalyticsService(Filter(), Gateway(), Mapper(), Repo(), tasks, card_key="task",
                                   comparison_conditions="previous", statistics_builder=lambda *_: {})
    service.search("session-secret", "account-secret", 100, {"mode": "advanced", "jql": "private-query"})
    tasks.runner(type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)
    assert all(request["page_size"] == 100 for request in requests)
    assert [request.get("expand") for request in requests] == [None, None]
    stages = {kwargs["extra"]["stage"] for _, kwargs in logs if "stage" in kwargs.get("extra", {})}
    assert stages == {"queue", "search", "persist", "aggregate", "activate", "complete"}
    stage_records = [(message, kwargs) for message, kwargs in logs if "stage" in kwargs.get("extra", {})]
    search_logs = [kwargs["extra"] for _, kwargs in stage_records if kwargs["extra"]["stage"] == "search" and kwargs["extra"]["outcome"] == "completed"]
    assert [item["period"] for item in search_logs] == ["current", "previous"]
    assert [item["layer"] for item in search_logs] == ["basic", "basic"]
    assert all(item["page_count"] == 1 and item["processed"] == 1 for item in search_logs)
    assert all(kwargs["extra"]["duration_ms"] >= 0 and kwargs["extra"]["card_key"] == "task" for _, kwargs in stage_records)
    assert any("stage=activate" in message and "outcome=completed" in message for message, _ in stage_records)
    assert all(kwargs["platform"] == "web" and kwargs["domain"] == "jira" for _, kwargs in logs)
    assert not any(value in str(logs) for value in ["session-secret", "account-secret", "private-query", "private-content"])


@pytest.mark.parametrize("card_key", ("self-test", "task", "customer-feedback"))
def test_runtime_basic_and_activity_queries_share_their_current_jql_owners(monkeypatch, card_key):
    from core.async_tasks import CancellationToken
    from core.jira.jql import customer_feedback_jql, product_line_jql
    from core.jira.services.jql_statistics_cards import (
        current_card_jql_by_product_line_and_metric, effective_card_jql_pair, activity_card_jql_pair, card_definition,
    )
    from core.product_lines import DASHBOARD_PRODUCT_LINES
    from smarttest_web.jira.analytics_service import card_analytics_service
    from smarttest_web.jira import analytics_refresh
    requests, activities = [], []
    class RecordingGateway(Gateway):
        def search_all_payloads(self, jql, **kwargs):
            requests.append(jql)
            return []
    class Repository(Repo):
        def register_history_scope(self, *_args): pass
        def statistics_preview(self, *_args, **_kwargs): return None
        def update_statistics(self, *_args, **_kwargs): return True
    def refresh_metric(_repository, _pages, _gateway, _account, _card, query, *_args, **_kwargs):
        activities.append(query)
        return "source"
    monkeypatch.setattr(analytics_refresh, 'refresh_metric', refresh_metric)
    repo, tasks = Repository(), Tasks()
    tasks.pages = object()
    service = card_analytics_service(Filter(), RecordingGateway(), repo, tasks, card_key)
    service.statistics_builder = lambda *_args, **_kwargs: {}
    service.search('session', 'test', 0, {'mode': 'advanced', 'jql': 'status = Open'})
    tasks.runner(CancellationToken(), lambda *_: None)
    actual = effective_card_jql_pair(card_key, 'status = Open', 'year')
    activity = activity_card_jql_pair(card_key, 'status = Open')
    assert requests == [actual[period]['jql'] for period in ('current', 'previous')]
    assert activities == [activity[period]['jql'] for period in ('current', 'previous')]
    display = current_card_jql_by_product_line_and_metric(card_key, 'status = Open', 'year')
    for line in DASHBOARD_PRODUCT_LINES:
        source = customer_feedback_jql(line) if card_key == 'customer-feedback' else card_definition(card_key).custom_jql
        branch = f'({product_line_jql(line, card_key=card_key)}) AND ({source})'
        assert all(branch in query for query in requests)
        assert source in display[line.name]['bugCount']
        assert display[line.name]['commentCount'] == display[line.name]['verifyCount'] == activities[0]
    assert all('labels NOT IN' not in query for query in requests)
