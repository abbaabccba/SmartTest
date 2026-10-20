from sqlalchemy import text
from fastapi.testclient import TestClient
import time
from datetime import date
from copy import deepcopy
import pytest
from threading import Event
from core.async_tasks import TaskCancelled
from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository

from core.product_lines import PRODUCT_LINES
from core.jira.services.jql_statistics_cards import card_definition


def self_test_jira_conditions(period="year"):
    return card_definition("self-test").fixed_jql(period)

from smarttest_web.app import create_app
from smarttest_web.session import PersistentSessionStore
from test_web_session import FakeAuthenticator


class FilterOwner:
    def fields(self): return [{"id": "customfield_1", "name": "Team", "control": "advanced", "queryable": False, "options": [], "schema": {}}]
    def saved_filters(self): return [{"id": "7", "name": "Mine", "owner": "Coco"}]
    def saved_filter(self, value): return {"id": value, "name": "Mine", "jql": "project = SH"}
    def validate(self, jql):
        valid = jql != "bad" and not jql.startswith("(bad)")
        return {"valid": valid, "errors": [] if valid else ["Bad JQL"]}
    def suggestions(self, field_name, query):
        return [{"value": f"{field_name}:1", "displayName": query or "All"}]


class Gateway:
    def search_metric_page(self, query, start, metric):
        rows = self.search_activity_payloads(query)
        if metric == 'comment':
            rows = [{key: value for key, value in row.items() if key != 'changelog'} for row in rows]
        else:
            rows = [{**row, 'fields': {key: value for key, value in row.get('fields', {}).items() if key != 'comment'}} for row in rows]
        return {'total': len(rows), 'issues': rows[start:start + 100]}
    def search_all_payloads(self, _jql, *, fields=None, expand=None, page_size=None, progress=None): return []
    def search_activity_payloads(self, query, *, progress=None):
        return []
    def user_groups(self, account):
        return {
            "meng.wang1": ("fae-wifi-qa",),
            "fan.xu": ("fae-iptv-qa",),
        }[account]


def client(tmp_path, *, database_factory):
    class AccountAuthenticator(FakeAuthenticator):
        def authenticate(self, username, password):
            return {**super().authenticate(username, password), 'username': username}
    app = create_app(
        authenticator=AccountAuthenticator,
        session_store=lambda: PersistentSessionStore(database=database_factory()),
        jira_filter_owner=lambda _u, _p: (FilterOwner(), Gateway()),
    )
    result = TestClient(app, base_url="https://testserver")
    result.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    return result


@pytest.mark.parametrize("same_scope", [False, True])
def test_three_cards_reuse_only_identical_business_activity_scopes_and_keep_cancellation_independent(
    tmp_path, monkeypatch, same_scope, *, database_factory
):
    if same_scope:
        from dataclasses import replace
        from core.jira.services.jql_statistics_cards import JIRA_STATISTICS_CARDS
        for key, definition in tuple(JIRA_STATISTICS_CARDS.items()):
            monkeypatch.setitem(JIRA_STATISTICS_CARDS, key, replace(definition, custom_jql="issuetype = Task"))
    initial_queries = 5  # cancelled Self-Test does not schedule its previous year
    release, entered = Event(), Event()
    queries = []
    copies = [1]
    def comments(_self, query, *, progress=None):
        queries.append(query)
        entered.set()
        assert release.wait(5)
        return dated_rows([{'id':'comment-only','key':'SH-999','fields':{'comment':{'comments':[
            {'id': 'action-84-12', 'author':{'name':'YIMIN.ZHOU'}}, {'id': 'action-84-46', 'author':{'name':'meng.wang1'}}] * copies[0]}}}], query)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', comments)
    api = client(tmp_path, database_factory=database_factory)
    api.post('/api/jira/analytics/search', json={'mode':'advanced','jql':'status = Open OR project = SH ORDER BY key'})
    started = {}
    try:
        for card in ('self-test','task','customer-feedback'):
            started[card] = api.post(f'/api/jira/cards/{card}/query',json={'period':'year'}).json()
            if card == "self-test":
                assert entered.wait(2)
        assert entered.wait(2)
        api.delete(f"/api/jira/cards/self-test/tasks/{started['self-test']['taskId']}")
    finally:
        release.set()
    for card in ('task','customer-feedback'):
        for _ in range(200):
            state = api.get(f"/api/jira/cards/{card}/tasks/{started[card]['taskId']}").json()['state']
            if state not in ('queued','running'): break
            time.sleep(.01)
        assert state == 'completed', api.get(f"/api/jira/cards/{card}/tasks/{started[card]['taskId']}").json()
        payload = api.get(f'/api/jira/cards/{card}/statistics').json()
        assert sum(person['commentCount'] for line in payload['periods']['year']['productLines'] for person in line['people']) == 2
    assert len(queries) == initial_queries
    assert all(query.startswith('(status = Open OR project = SH) AND ((created >=') for query in queries)
    assert all(query.endswith('ORDER BY key') for query in queries)
    assert all('creator' not in query for query in queries)
    assert len(set(queries)) == (2 if same_scope else initial_queries)
    for card in ('self-test', 'task', 'customer-feedback'):
        assert sum(card_definition(card).custom_jql in query for query in queries) == (5 if same_scope else 1 if card == 'self-test' else 2)
    assert any(query != queries[0] for query in queries)
    assert tuple(api.get('/api/jira/cards/customer-feedback/statistics').json()['periods']) == ('week', 'month', 'quarter', 'year')
    assert len(queries) == initial_queries
    reopened = JiraAnalyticsRepository(database_factory())
    assert reopened.state('new-session','coco',card_key='task')['activeSnapshotId']
    with reopened.database.connect() as connection:
        assert (
            connection.execute(
                text(
                    """SELECT count(*) FROM jira_analytics_facts f
            JOIN jira_analytics_snapshots s ON s.snapshot_id=f.snapshot_id WHERE s.card_key='task' """
                )
            ).fetchone()[0]
            == 0
        )
    copies[0] = 2
    api.post('/api/jira/analytics/search', json={'mode':'advanced','jql':'status = Open OR project = SH ORDER BY key'})
    refreshed = api.post('/api/jira/cards/task/query',json={'period':'year'}).json()
    for _ in range(200):
        state = api.get(f"/api/jira/cards/task/tasks/{refreshed['taskId']}").json()['state']
        if state not in ('queued','running'): break
        time.sleep(.01)
    assert state == 'completed'
    fresh = api.get('/api/jira/cards/task/statistics').json()
    old = api.get('/api/jira/cards/customer-feedback/statistics').json()
    assert sum(p['commentCount'] for line in fresh['periods']['year']['productLines'] for p in line['people']) == 4
    assert sum(p['commentCount'] for line in old['periods']['year']['productLines'] for p in line['people']) == 2
    assert len(queries) == initial_queries + 2


def test_each_explicit_apply_refreshes_activities_and_failure_keeps_prior_result(
    tmp_path, monkeypatch, *, database_factory
):
    queries, fail, copies = [], [False], [1]
    def activity(_self, query, **_kwargs):
        queries.append(query)
        if fail[0]:
            raise RuntimeError('offline')
        return dated_rows([{'id':'one','key':'SH-1','fields':{'comment':{'comments':[
            {'id': 'action-152-12', 'author':{'name':'yimin.zhou'}}] * copies[0]}}}], query)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', activity)
    api = client(tmp_path, database_factory=database_factory)
    first = query_card(api, {'mode':'advanced','jql':'status = Open'}).json()
    assert wait_terminal(api, first['taskId']) == 'completed'
    fail[0] = True
    second = api.post('/api/jira/cards/self-test/query',json={'period':'year'}).json()
    assert wait_terminal(api, second['taskId']) == 'failed'
    failed = api.get('/api/jira/cards/self-test/statistics').json()
    assert failed['state'] == 'failed' and failed['error'] == 'RuntimeError'
    assert failed['query']['activeSnapshotId'] == first['snapshotId']
    assert sum(p['commentCount'] for line in failed['periods']['year']['productLines'] for p in line['people']) == 1
    fail[0], copies[0] = False, 2
    for _ in range(2):
        started = api.post('/api/jira/cards/self-test/query',json={'period':'year'}).json()
        assert wait_terminal(api, started['taskId']) == 'completed'
    assert len(queries) == 7
    before = len(queries)
    ready = api.get('/api/jira/cards/self-test/statistics').json()
    assert sum(p['commentCount'] for line in ready['periods']['year']['productLines'] for p in line['people']) == 2
    assert len(queries) == before


def test_failed_comment_source_can_retry_and_new_user_query_does_not_reuse_old_source(
    tmp_path, monkeypatch, *, database_factory
):
    queries = []
    records = []
    import smarttest_web.jira.analytics_service as owner
    monkeypatch.setattr(owner, 'smart_log', lambda message, **kwargs: records.append((message, kwargs)))
    def comments(_self, query, *, progress=None):
        queries.append(query)
        if len(queries) == 1:
            raise RuntimeError('private credential text must not be logged')
        return dated_rows([{'id':'comment','key':'SH-9','fields':{'comment':{'comments':[
            {'id': 'action-187-12', 'author':{'name':'yimin.zhou'}}]}}}], query)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', comments)
    api = client(tmp_path, database_factory=database_factory)
    first = query_card(api, {'mode':'advanced','jql':'status = Open'}).json()
    assert wait_terminal(api, first['taskId']) == 'failed'
    failure = next(kwargs['extra'] for message, kwargs in records if message == 'Jira card failed')
    assert failure['stage'] == 'search' and failure['layer'] == 'comment'
    assert failure['cause_type'] == 'RuntimeError' and failure['http_status'] is None
    assert failure['duration_ms'] >= 0 and 'private' not in str(records)
    retry = api.post('/api/jira/cards/self-test/query',json={'period':'year'}).json()
    assert wait_terminal(api, retry['taskId']) == 'completed'
    completed = len(queries)
    api.get('/api/jira/cards/self-test/statistics')
    assert len(queries) == completed
    updated = query_card(api, {'mode':'advanced','jql':'status = Closed'}).json()
    assert wait_terminal(api, updated['taskId']) == 'completed'
    assert len(queries) == completed+2
    assert all(query.startswith('(status = Closed)') for query in queries[-2:])


def test_completed_current_activity_year_is_visible_while_previous_year_and_basic_are_pending(
    tmp_path, monkeypatch, *, database_factory
):
    basic_entered, basic_release, previous_release = Event(), Event(), Event()
    activity_queries = []
    def search(_self, query, **kwargs):
        if f'created >= "{date.today().year-1}-01-01"' in query:
            basic_entered.set()
            assert basic_release.wait(5)
        else:
            assert 'comment' not in kwargs['fields']
            assert kwargs['page_size'] == 100
        return []
    def comments(_self, query, **kwargs):
        activity_queries.append(query)
        if f'created >= "{date.today().year-1}-01-01"' in query:
            assert previous_release.wait(5)
        return dated_rows([{'id':'qa-comment','key':'SH-9','fields':{'comment':{'comments':[
            {'id': 'action-225-12', 'author':{'name':'yimin.zhou'}}]}}, 'changelog': {'histories': [
                {'id': 'action-226-16', 'author': {'name': 'yimin.zhou'}, 'created': '2020-01-01T00:00:00Z',
                 'items': [{'field': 'status', 'fromString': 'Resolved', 'toString': 'Verified'}]}]}}], query)
    monkeypatch.setattr(Gateway,'search_all_payloads',search)
    monkeypatch.setattr(Gateway,'search_activity_payloads',comments)
    api = client(tmp_path, database_factory=database_factory)
    started = query_card(api, {'mode':'basic','basic':{}}).json()
    try:
        assert basic_entered.wait(2)
        basic_release.set()
        for _ in range(100):
            payload = api.get('/api/jira/cards/self-test/statistics').json()
            if payload.get('periods',{}).get('year',{}).get('availability',{}).get('current',{}).get('comment'): break
            time.sleep(.01)
        payload = payload['periods']['year']
        assert payload['availability']['current']['comment'] is True
        assert payload['availability']['current']['basic'] is True
        assert payload['availability']['previous']['comment'] is False
        assert sum(p['commentCount'] for line in payload['current']['productLines'] for p in line['people']) == 1
        assert payload['availability']['current']['verify'] is False
        assert 1 <= len(activity_queries) <= 2
    finally:
        basic_release.set()
        previous_release.set()
    assert wait_terminal(api, started['taskId']) == 'completed'


def query_card(api, payload):
    published = api.post("/api/jira/analytics/search", json=payload)
    return api.post("/api/jira/cards/self-test/query", json={"period": "year"}) if published.json().get("applied") else published


def dated_rows(rows, query):
    year = date.today().year - (1 if f'created >= "{date.today().year - 1}-01-01"' in query else 0)
    result = deepcopy(rows)
    for row in result:
        row['id'] = f"{row['id']}-{year}"
        row['key'] = f"{row['key']}-{year}"
        row.setdefault('fields', {})['created'] = f"{year}-01-01T00:00:00+08:00"
    return result


def wait_terminal(api, task_id):
    for _ in range(100):
        task = api.get(f"/api/jira/cards/self-test/tasks/{task_id}").json()
        if task["state"] not in {"queued", "running"}:
            return task["state"]
        time.sleep(0.01)
    raise AssertionError("query did not finish")


def test_public_search_only_publishes_conditions_and_card_queries_its_own_effective_scope(
    tmp_path, monkeypatch, *, database_factory
):
    queries = []
    monkeypatch.setattr(Gateway, "search_all_payloads", lambda _self, jql, **_kw: queries.append(jql) or [])
    api = client(tmp_path, database_factory=database_factory)
    published = api.post("/api/jira/analytics/search", json={"mode": "basic", "basic": {}}).json()
    assert published["applied"] is True
    assert "taskId" not in published
    assert queries == []
    started = api.post("/api/jira/cards/self-test/query", json={"jql": "", "fixed_conditions": ""}).json()
    assert started["validation"]["valid"] is True
    assert wait_terminal(api, started["taskId"]) == "completed"
    assert queries[0] == self_test_jira_conditions()
    assert len(queries) == 2
    assert 'creator IN membersOf("fae-wifi-qa")' in queries[0]
    assert 'reporter IN (' not in queries[0]
    assert 'assignee IN (' not in queries[0]
    assert api.post("/api/jira/cards/unknown/query").status_code == 404
    replay = api.get("/api/jira/cards/self-test/statistics").json()
    assert replay["state"] == "ready"
    assert len(queries) == 2


@pytest.mark.parametrize("card_key,expected", [
    ("self-test", "issuetype = Bug"),
    ("task", "issuetype = Task"),
    ("customer-feedback", '"Channel of Reporter" = "Customer-Feedback"'),
])
def test_registered_cards_share_query_task_and_snapshot_flow(
    tmp_path, monkeypatch, card_key, expected, *, database_factory
):
    queries = []
    monkeypatch.setattr(Gateway, "search_all_payloads", lambda _self, jql, **_kw: queries.append(jql) or [])
    api = client(tmp_path, database_factory=database_factory)
    api.post("/api/jira/analytics/search", json={"mode": "basic", "basic": {}})
    assert api.get(f"/api/jira/cards/{card_key}/statistics").json()["state"] == "no_snapshot"
    started = api.post(f"/api/jira/cards/{card_key}/query", json={"period": "year"}).json()
    for _ in range(100):
        task = api.get(f"/api/jira/cards/{card_key}/tasks/{started['taskId']}").json()
        if task["state"] not in {"queued", "running"}:
            break
        time.sleep(.01)
    assert task["state"] == "completed"
    assert expected in queries[0]
    replay = api.get(f"/api/jira/cards/{card_key}/statistics").json()
    assert replay["state"] == "ready"
    replay = replay['periods']['month']
    assert set(replay["currentJqlByProductLineAndMetric"]) == {
        "China Operator", "Smart Device", "TV", "Global Operator & STB", "Wireless Connection",
    }
    metrics = replay["currentJqlByProductLineAndMetric"]["TV"]
    assert expected in metrics["bugCount"]
    assert metrics["commentCount"] == metrics["verifyCount"] == metrics["bugCount"]
    assert 'resolution = "Invalid"' in metrics["invalidCount"]
    assert not {"activeJql", "activeComparisonJql", "userJql"} & replay.keys()
    assert not any('created < "' in query for metrics in replay["currentJqlByProductLineAndMetric"].values()
                   for query in metrics.values())


def test_creator_group_failure_is_explicit_and_preserves_previous_snapshot(
    tmp_path, monkeypatch, *, database_factory
):
    api = client(tmp_path, database_factory=database_factory)
    previous = query_card(api, {"mode": "basic", "basic": {}}).json()
    assert wait_terminal(api, previous["taskId"]) == "completed"
    monkeypatch.setattr(Gateway, 'user_groups', lambda _self, _account: (_ for _ in ()).throw(RuntimeError('offline')))
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, _jql, **_kwargs: [{
        "id": "1", "key": "TV-1", "fields": {"project": {"key": "TV"}, "issuetype": {"name": "Bug"},
        "creator": {"name": "fan.xu", "displayName": "Fan Xu"},
    }}])
    started = query_card(api, {"mode": "basic", "basic": {}}).json()
    assert wait_terminal(api, started["taskId"]) == "failed"
    response = api.get("/api/jira/cards/self-test/statistics").json()
    assert response["state"] == "failed"
    assert response["error"] == "RuntimeError"
    assert response["periods"]["month"]["teamTotal"] == 0


def test_old_snapshot_without_qa_creator_group_boundary_is_not_replayed_or_requeried(
    tmp_path, monkeypatch, *, database_factory
):
    queries = []
    api = client(tmp_path, database_factory=database_factory)
    api.post('/api/jira/analytics/search', json={'mode': 'basic', 'basic': {}})
    database = database_factory()
    with database.connect() as connection:
        session_hash = connection.execute(
            text("SELECT session_hash FROM jira_analytics_queries WHERE card_key=''")
        ).fetchone()[0]
    repository = JiraAnalyticsRepository(database)
    old = repository.begin(session_hash, 'coco', '"Channel of Reporter" = "Self-Test"', {}, '',
                           expires_at=time.time() + 100, user_jql='', card_key='self-test')
    repository.activate(old)
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kwargs: queries.append(jql) or [])
    replay = api.get('/api/jira/cards/self-test/statistics').json()
    assert replay['state'] == 'no_snapshot'
    assert 'productLines' not in replay
    assert queries == []


def test_effective_bug_query_validation_execution_database_and_explicit_unmapped_total_match(
    tmp_path, monkeypatch, *, database_factory
):
    validated, executed = [], []
    def validate(_self, jql):
        validated.append(jql)
        return {"valid": True, "errors": []}
    candidates = []
    for index, (kind, project, reporter) in enumerate([
        ('Bug', PRODUCT_LINES[0].jira_project_keys[0], 'fan.xu'),
        ('Bug', 'unknown', 'meng.wang1'),
        ('Task', PRODUCT_LINES[2].jira_project_keys[0], 'meng.wang1'),
        ('Epic', PRODUCT_LINES[2].jira_project_keys[0], 'meng.wang1'),
    ]):
        candidates.append({'id': str(index), 'key': f'ISSUE-{index}', 'fields': {
            'summary': kind, 'issuetype': {'name': kind}, 'project': {'key': project},
            'reporter': {'name': reporter, 'displayName': reporter},
            'creator': {'name': reporter, 'displayName': reporter},
        }})
    def collect(_self, jql, *, fields=None, expand=None, page_size=None, progress=None):
        executed.append(jql)
        assert 'issuetype = Bug' in jql
        rows = [row for row in candidates if row['fields']['issuetype']['name'] == 'Bug']
        if progress: progress(len(rows), len(rows))
        return dated_rows(rows, jql)
    monkeypatch.setattr(FilterOwner, 'validate', validate)
    monkeypatch.setattr(Gateway, 'search_all_payloads', collect)
    api = client(tmp_path, database_factory=database_factory)
    user = 'reporter = "meng.wang1" OR reporter = "fan.xu" ORDER BY created DESC'
    started = query_card(api, {'mode': 'advanced', 'jql': user}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    statistics = api.get('/api/jira/cards/self-test/statistics').json()
    with database_factory().connect() as connection:
        saved = connection.execute(
            text("SELECT count(*) FROM jira_analytics_snapshot_issues")
        ).fetchone()[0]
        effective = connection.execute(
            text("SELECT jql FROM jira_analytics_snapshots WHERE card_key='self-test'")
        ).fetchone()[0]
    statistics = statistics['periods']['year']
    displayed = sum(person['bugCount'] for line in statistics['productLines'] for person in line['people'])
    assert saved == 4
    assert statistics['teamTotal'] == 2
    assert statistics['teamTotal'] == displayed + statistics['unmappedCount'] + statistics['unassignedCount']
    assert statistics['unmappedCount'] == 0
    assert statistics['productLines'][4]['people'][0]['identity'] == 'meng.wang1'
    assert statistics['productLines'][4]['people'][0]['bugCount'] == 1
    assert effective == executed[0] == validated[1]
    assert effective.startswith('(reporter = "meng.wang1" OR reporter = "fan.xu") AND (')
    assert 'issuetype = Bug' in effective
    assert effective.endswith('ORDER BY created DESC')


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
@pytest.mark.parametrize("previous", [False, True])
def test_statistics_exposes_query_terminal_state_and_retains_previous_snapshot(
    tmp_path, monkeypatch, terminal, previous, *, database_factory
):
    api = client(tmp_path, database_factory=database_factory)
    if previous:
        initial = query_card(api, {"mode": "basic", "basic": {}}).json()
        assert wait_terminal(api, initial["taskId"]) == "completed"
    def broken(_self, _jql, *, fields=None, expand=None, page_size=None, progress=None):
        raise TaskCancelled() if terminal == "cancelled" else RuntimeError("offline")
    monkeypatch.setattr(Gateway, "search_all_payloads", broken)
    started = query_card(api, {"mode": "basic", "basic": {}}).json()
    assert wait_terminal(api, started["taskId"]) == terminal
    payload = api.get("/api/jira/cards/self-test/statistics").json()
    assert payload["state"] == terminal
    assert payload["error"]
    assert ("periods" in payload) is previous


def test_lost_task_is_interrupted_without_requery_and_previous_progress_does_not_finish_new_task(
    tmp_path, monkeypatch, *, database_factory
):
    api = client(tmp_path, database_factory=database_factory)
    first = query_card(api, {"mode": "basic", "basic": {}}).json()
    assert wait_terminal(api, first["taskId"]) == "completed"
    statistics = api.get("/api/jira/cards/self-test/statistics").json()
    assert statistics["state"] == "ready"
    assert statistics["periods"]["month"]["teamTotal"] == 0
    restored = api.get("/api/jira/cards/self-test/statistics").json()["query"]
    assert restored["activeSnapshotId"] == first["snapshotId"]
    entered, release = Event(), Event()
    def blocked(_self, _jql, *, fields=None, expand=None, page_size=None, progress=None):
        entered.set()
        assert release.wait(3)
        return []
    monkeypatch.setattr(Gateway, "search_all_payloads", blocked)
    current = query_card(api, {"mode": "advanced", "jql": "project = A"}).json()
    try:
        assert entered.wait(1)
        assert api.get(f"/api/jira/cards/self-test/tasks/{first['taskId']}").status_code == 404
        assert api.get("/api/jira/cards/self-test/statistics").json()["state"] == "loading"
        loading = api.get("/api/jira/cards/self-test/statistics").json()
        assert loading["query"]["pendingSnapshotId"] == current["snapshotId"]
        assert loading["query"]["activeSnapshotId"] == first["snapshotId"]
        from core.product_lines import DASHBOARD_PRODUCT_LINES
        loading = loading["periods"]["month"]
        assert [line["id"] for line in loading["productLines"]] == [line.name for line in DASHBOARD_PRODUCT_LINES]
        assert all(not line["people"] for line in loading["productLines"])
        assert loading["availability"]["current"] == {"basic": False, "comment": False, "verify": False}
    finally:
        release.set()
    assert wait_terminal(api, current["taskId"]) == "completed"
    completed = api.get("/api/jira/cards/self-test/statistics").json()["query"]
    assert completed["activeSnapshotId"] == current["snapshotId"]
    database = database_factory()
    with database.connect() as connection:
        session_hash, account, expires = connection.execute(
            text(
                "SELECT session_hash,account,expires_at FROM jira_analytics_queries WHERE card_key=''"
            )
        ).fetchone()
    repo = JiraAnalyticsRepository(database)
    active = repo.state(session_hash, account, card_key="self-test")
    snapshot = repo.begin(session_hash, account, active["activeJql"], {}, "", expires_at=expires,
                          user_jql=active["userJql"], card_key="self-test", roster_fingerprint=active["rosterFingerprint"],
                          comparison_jql=active["activeComparisonJql"])
    from smarttest_web.app import _session_owner
    repo.set_task(snapshot, "lost-process-task", session_hash=_session_owner(api.cookies.get('smarttest_session')))
    payload = api.get("/api/jira/cards/self-test/statistics").json()
    assert payload["state"] == "failed"
    assert payload["error"] == "query_interrupted"
    assert "periods" in payload
    assert api.get("/api/jira/cards/self-test/statistics").json()["query"]["pendingSnapshotId"] == ""


def test_app_restart_interrupts_legacy_pending_without_task_id_and_preserves_active_snapshot(
    tmp_path, *, database_factory
):
    api = client(tmp_path, database_factory=database_factory)
    first = query_card(api, {"mode": "basic", "basic": {}}).json()
    assert wait_terminal(api, first["taskId"]) == "completed"
    database = database_factory()
    with database.connect() as connection:
        session_hash, account, expires = connection.execute(
            text("SELECT session_hash,account,expires_at FROM jira_analytics_queries")
        ).fetchone()
    repo = JiraAnalyticsRepository(database)
    repo.begin(session_hash, account, "unfinished", {}, "", expires_at=expires, card_key="self-test")
    assert repo.state(session_hash, account, card_key="self-test")["taskId"] == ""
    create_app(
        authenticator=FakeAuthenticator,
        session_store=lambda: PersistentSessionStore(database=database_factory()),
    )
    restored = repo.state(session_hash, account, card_key="self-test")
    assert restored["pendingSnapshotId"] == ""
    assert restored["activeSnapshotId"] == first["snapshotId"]
    assert restored["latestState"] == "failed"
    assert restored["error"] == "query_interrupted"


def test_analytics_endpoints_are_independent_and_search_starts_task(
    tmp_path, *, database_factory
):
    api = client(tmp_path, database_factory=database_factory)

    assert api.get("/api/jira/analytics/fields").json()["more"][0]["id"] == "customfield_1"
    assert api.get("/api/jira/analytics/suggestions", params={"fieldName": "status", "query": "op"}).json() == [
        {"value": "status:1", "displayName": "op"},
    ]
    assert api.get("/api/jira/analytics/saved-filters").json()[0]["id"] == "7"
    assert api.get("/api/jira/analytics/saved-filters/7").json()["jql"] == "project = SH"
    invalid = query_card(api, {"mode": "advanced", "jql": "bad"})
    assert invalid.json()["validation"]["valid"] is False
    started = query_card(api, {"mode": "advanced", "jql": "project = SH"})
    assert started.status_code == 200
    task_id = started.json()["taskId"]
    assert api.get(f"/api/jira/cards/self-test/tasks/{task_id}").status_code == 200
    assert api.delete(f"/api/jira/cards/self-test/tasks/{task_id}").json() == {"cancelled": True}
    assert api.get("/api/jira/filter-snapshot").json()["snapshot"] is None


def test_analytics_state_is_not_visible_to_another_account(
    tmp_path, *, database_factory
):
    first = client(tmp_path, database_factory=database_factory)
    query_card(first, {"mode": "advanced", "jql": "project = SH"})
    first.post("/api/auth/logout")
    first.post("/api/auth/login", json={"username": "other", "password": "secret"})

    assert first.get("/api/jira/analytics/state").json()["conditions"] is None
    assert first.get("/api/jira/cards/self-test/statistics").json() == {"state": "no_snapshot"}


def test_statistics_replays_only_the_applied_jql_collection_without_extra_scope(
    tmp_path, monkeypatch, *, database_factory
):
    queries = []
    project = PRODUCT_LINES[0].jira_project_keys[0]
    def search(_self, jql, *, fields=None, expand=None, page_size=None, progress=None):
        queries.append(jql)
        return dated_rows([{"id": "1", "key": "ONE-1", "fields": {
            "summary": "One", "project": {"id": "1", "key": project, "name": "One"},
            "issuetype": {"id": "2", "name": "Bug"}, "status": {"id": "1", "name": "Open"},
            "reporter": {"name": "outside", "displayName": "Outside"},
            "creator": {"name": "fan.xu", "displayName": "Fan Xu"},
            "assignee": {"name": "outside.qa", "displayName": "Outside QA"},
            "priority": {"id": "1", "name": "P0"}, "resolution": {"id": "1", "name": "Resolved"},
        }}], jql) if jql.startswith("(first)") else []
    monkeypatch.setattr(Gateway, "search_all_payloads", search)
    api = client(tmp_path, database_factory=database_factory)
    assert api.get("/api/jira/cards/self-test/statistics").json() == {"state": "no_snapshot"}
    def apply(jql):
        task_id = query_card(api, {"mode": "advanced", "jql": jql}).json()["taskId"]
        assert wait_terminal(api, task_id) == "completed"
    apply("first")
    payload = api.get("/api/jira/cards/self-test/statistics").json()
    assert payload["state"] == "ready"
    assert payload["periods"]["year"]["teamTotal"] == 1
    assert payload["periods"]["year"]["productLines"][0]["people"] == [{"identity": "fan.xu", "displayName": "Fan Xu",
        "bugCount": 1, "commentCount": 0, "invalidCount": 0}]
    monkeypatch.setattr(Gateway, "user_groups", lambda _self, _account: (_ for _ in ()).throw(AssertionError("reuse queried Jira")))
    reused = api.get("/api/jira/cards/self-test/statistics").json()
    assert reused["state"] == "ready"
    monkeypatch.setattr(Gateway, "user_groups", lambda self, account: {
        "meng.wang1": ("fae-wifi-qa",), "fan.xu": ("fae-iptv-qa",),
    }[account])
    apply("second")
    assert api.get("/api/jira/cards/self-test/statistics").json()["periods"]["year"]["teamTotal"] == 0
    fixed = self_test_jira_conditions()
    assert queries[0] == f'(first) AND ({fixed})'
    assert queries[2] == f'(second) AND ({fixed})'
    assert len(queries) == 4


def test_suggestions_resolve_the_current_sessions_jira_credentials_on_every_request(
    tmp_path, *, database_factory
):
    calls = []

    class AccountAuthenticator:
        def authenticate(self, username, _password):
            return {"success": True, "username": username, "display_name": username, "avatar_bytes": b""}

    def owner(username, password):
        calls.append((username, password))
        return FilterOwner(), Gateway()

    app = create_app(
        authenticator=AccountAuthenticator,
        session_store=lambda: PersistentSessionStore(database=database_factory()),
        jira_filter_owner=owner,
    )
    api = TestClient(app, base_url="https://testserver")
    api.post("/api/auth/login", json={"username": "alice", "password": "first"})
    api.get("/api/jira/analytics/suggestions", params={"fieldName": "status"})
    api.post("/api/auth/logout")
    api.post("/api/auth/login", json={"username": "bob", "password": "second"})
    api.get("/api/jira/analytics/suggestions", params={"fieldName": "status"})

    assert calls == [("alice", "first"), ("bob", "second")]


@pytest.mark.parametrize("card_key", ["self-test", "task", "customer-feedback"])
def test_cards_exclude_non_qa_at_task_aggregation_and_reject_old_aggregation_snapshot(
    tmp_path, monkeypatch, card_key, *, database_factory
):
    executed = []
    rows = [{"id": str(index), "key": f"TV-{index}", "fields": {
        "project": {"key": "TV"}, "issuetype": {"name": "Bug"},
        "creator": {"name": account, "displayName": account}, "labels": ["Customer_W1"],
    }} for index, account in enumerate(["qa", "outside", "wifi"], 1)]
    monkeypatch.setattr(Gateway, "search_all_payloads", lambda _self, jql, **_kw: executed.append(jql) or dated_rows(rows, jql))
    monkeypatch.setattr(Gateway, "user_groups", lambda _self, account: {
        "qa": ("fae-tv-qa",), "outside": ("jira-users",), "wifi": ("fae-wifi-qa",),
    }[account])
    api = client(tmp_path, database_factory=database_factory)
    api.post("/api/jira/analytics/search", json={"mode": "advanced", "jql": "status = Open ORDER BY created DESC"})
    started = api.post(f"/api/jira/cards/{card_key}/query", json={"period": "year"}).json()
    for _ in range(100):
        task = api.get(f"/api/jira/cards/{card_key}/tasks/{started['taskId']}").json()
        if task["state"] not in {"queued", "running"}: break
        time.sleep(.01)
    assert task["state"] == "completed"
    assert len(executed) == 2
    from core.jira.services.jql_statistics_cards import FAE_QA_GROUPS
    for index, query in enumerate(executed):
        assert all((f'creator IN membersOf("{group}")' in query) is True for group in FAE_QA_GROUPS)
        assert query.endswith("ORDER BY created DESC")
    replay = api.get(f"/api/jira/cards/{card_key}/statistics").json()
    for period in ["current", "previous"]:
        summary = replay["periods"]["year"][period]
        assert {person["identity"] for line in summary["productLines"] for person in line["people"]} == {"qa", "wifi"}
        assert summary["teamTotal"] == 2
        assert summary["productLines"][2]["people"][0]["identity"] == "qa"
        assert summary["productLines"][4]["people"][0]["identity"] == "wifi"
    with database_factory().transaction() as connection:
        connection.execute(
            text(
                "UPDATE jira_analytics_snapshots SET roster_fingerprint='old-aggregation' WHERE snapshot_id=:snapshot_id"
            ),
            {"snapshot_id": started["snapshotId"]},
        )
    stale = api.get(f"/api/jira/cards/{card_key}/statistics").json()
    assert stale["state"] == "no_snapshot"
    assert "productLines" not in stale
    assert len(executed) == 2
    missing = api.get(f"/api/jira/cards/{card_key}/statistics").json()
    assert missing["state"] == "no_snapshot" and "taskId" not in missing
    replacement = api.post(f"/api/jira/cards/{card_key}/query").json()
    assert replacement["snapshotId"] != started["snapshotId"]
    for _ in range(100):
        task = api.get(f"/api/jira/cards/{card_key}/tasks/{replacement['taskId']}").json()
        if task["state"] not in {"queued", "running"}: break
        time.sleep(.01)
    assert task["state"] == "completed"
    reused = api.get(f"/api/jira/cards/{card_key}/statistics").json()
    assert reused["query"]["activeSnapshotId"] == replacement["snapshotId"]
    assert len(executed) == 4


@pytest.mark.parametrize("card_key", ["self-test", "task", "customer-feedback"])
def test_card_bulk_query_requests_comments_and_replays_comment_authors(
    tmp_path, monkeypatch, card_key, *, database_factory
):
    from core.jira.gateway import JiraGateway
    requested = []
    def search(_self, _query, *, fields=None, expand=None, page_size=None, progress=None):
        requested.append((fields, expand, page_size))
        return dated_rows([{"id": "1", "key": "TV-1", "fields": {
            "project": {"key": "TV"}, "creator": {"name": "fan.xu"},
            "comment": {"comments": [{"id": "c1", "author": {"name": "meng.wang1"}, "created": "2020-01-01T00:00:00Z"}]},
        }, "changelog": {"histories": [{'id': 'action-702-39', "author": {"name": "meng.wang1"},
            "created": "2026-09-01T00:00:00Z", "items": [{"field": "status", "fromString": "Resolved", "toString": "Verified"}]}]}}], _query)
    monkeypatch.setattr(Gateway, "search_all_payloads", search)
    def comments(_self, query, *, progress=None):
        return dated_rows([{"id": "1", "key": "TV-1", "fields": {
            "creator": {"name": "not-qa"}, "project": {"key": "SH"},
            "comment": {"comments": [{"id": "c1", "author": {"name": "meng.wang1"}}]}}, "changelog": {"histories": [{'id': 'action-708-116', "author": {"name": "meng.wang1"}, "created": "2020-01-01T00:00:00Z", "items": [{"field": "status", "fromString": "Resolved", "toString": "Verified"}]}]}}], query)
    monkeypatch.setattr(Gateway, "search_activity_payloads", comments)
    api = client(tmp_path, database_factory=database_factory)
    api.post("/api/jira/analytics/search", json={"mode": "basic", "basic": {}})
    started = api.post(f"/api/jira/cards/{card_key}/query", json={"period": "year"}).json()
    for _ in range(100):
        task = api.get(f"/api/jira/cards/{card_key}/tasks/{started['taskId']}").json()
        if task["state"] not in {"queued", "running"}: break
        time.sleep(.01)
    assert task["state"] == "completed"
    assert requested == [(list(JiraGateway.CORE_FIELDS), None, 100)] * 2
    from test_jira_history_schedule import refresh_test_history
    refresh_test_history(api)
    replay = api.get(f"/api/jira/cards/{card_key}/statistics").json()
    for period in ["current", "previous"]:
        people = replay["periods"]["year"][period]["productLines"][4]["people"]
        verifier = next(person for person in people if person["identity"] == "meng.wang1")
        assert verifier["verifyCount"] == 1
        commenter = next(person for person in replay["periods"]["year"][period]["productLines"][4]["people"] if person["identity"] == "meng.wang1")
        assert commenter["commentCount"] == 1
        assert all("p0Count" not in person for person in people)
        assert all("resolvedCount" not in person for person in people)
    assert len(requested) == 2


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
@pytest.mark.parametrize("has_active", [False, True])
def test_interrupted_layers_restore_complete_active_or_keep_first_basic_preview(
    tmp_path, monkeypatch, terminal, has_active, *, database_factory
):
    calls = []
    def search(_self, _query, **kwargs):
        calls.append(kwargs)
        if len(calls) == (4 if has_active else 2):
            raise TaskCancelled() if terminal == "cancelled" else RuntimeError("offline")
        return dated_rows([{"id": "1", "key": "TV-1", "fields": {
            "project": {"key": "TV"}, "creator": {"name": "fan.xu"}}}], _query)
    monkeypatch.setattr(Gateway, "search_all_payloads", search)
    api = client(tmp_path, database_factory=database_factory)
    if has_active:
        first = query_card(api, {"mode": "basic", "basic": {}}).json()
        assert wait_terminal(api, first["taskId"]) == "completed"
    started = query_card(api, {"mode": "basic", "basic": {}}).json()
    assert wait_terminal(api, started["taskId"]) == terminal
    payload = api.get("/api/jira/cards/self-test/statistics").json()
    assert payload["state"] == terminal
    payload = payload["periods"]["year"] | {"query": payload["query"]}
    assert payload["teamTotal"] == 1
    assert payload["availability"]["current"]["basic"] is True
    assert payload["availability"]["current"]["verify"] is False
    assert payload["availability"]["previous"]["basic"] == has_active
    assert payload["availability"]["previous"]["verify"] is False
    people = [person for line in payload["productLines"] for person in line["people"]]
    assert all("bugCount" in person for person in people)
    assert bool(payload["query"]["activeSnapshotId"]) == has_active


def test_incremental_card_aggregation_loads_each_account_groups_once_per_task(
    tmp_path, monkeypatch, *, database_factory
):
    groups = []
    def user_groups(_self, account):
        groups.append(account)
        return ("fae-tv-qa",) if account != "outside" else ()
    monkeypatch.setattr(Gateway, "user_groups", user_groups)
    monkeypatch.setattr(Gateway, "search_all_payloads", lambda _self, _query, **kwargs: [{
        "id": "1", "key": "TV-1", "fields": {"project": {"key": "TV"}, "creator": {"name": "creator"},
            "comment": {"comments": [{'id': 'action-775-37', "author": {"name": "outside"}, "created": "2026-09-01T00:00:00Z"}]}},
        "changelog": {"histories": [{'id': 'action-776-36', "author": {"name": "verifier"}, "created": "2026-09-01T00:00:00Z",
            "items": [{"field": "status", "fromString": "Resolved", "toString": "Verified"}]}]}
            if kwargs.get("expand") else {},
    }])
    api = client(tmp_path, database_factory=database_factory)
    for completed_tasks in (1, 2):
        started = query_card(api, {"mode": "basic", "basic": {}}).json()
        assert wait_terminal(api, started["taskId"]) == "completed"
        assert {account: groups.count(account) for account in groups} == {
            "creator": completed_tasks}


@pytest.mark.parametrize("card_key", ["self-test", "task", "customer-feedback"])
def test_comments_and_verify_share_user_annual_scope_and_personnel_lines(
    tmp_path, monkeypatch, card_key, *, database_factory
):
    from core.jira.services.jql_statistics_cards import effective_card_jql_pair, activity_card_jql_pair
    basic_queries, activity_queries = [], []
    def basic(_self, query, **kwargs):
        basic_queries.append(query)
        assert 'creator IN membersOf(' in query
        assert 'comment' not in kwargs['fields'] and not kwargs.get('expand')
        return dated_rows([{"id":"1", "key":"TV-1", "fields":{
            "project":{"key":"TV"}, "creator":{"name":"fan.xu"}}}], query)
    def activity(_self, query, **kwargs):
        activity_queries.append(query)
        assert 'creator' not in query
        assert card_definition(card_key).custom_jql in query
        return dated_rows([{"id":str(index), "key":f"SH-{index}", "fields":{
            "project":{"key":"SH"}, "creator":{"name":"outside"},
            "comment":{"comments":[{'id': 'action-806-35', "author":{"name":"meng.wang1"}}]}},
            "changelog":{"histories":[{'id': 'action-807-38', "author":{"name":"meng.wang1"}, "created":"2019-01-01T00:00:00Z",
                "items":[{"field":"status", "fromString":"Resolved", "toString":"Verified"}]},
                {'id': 'action-809-16', "author":{"name":actor}, "created":"2020-01-01T00:00:00Z",
                "items":[{"field":"status", "fromString":"Resolved", "toString":"Verified"}]}]}}
            for index, actor in [(2,"meng.wang1"),(3,"outside")]], query)
    monkeypatch.setattr(Gateway,'search_all_payloads',basic)
    monkeypatch.setattr(Gateway,'search_activity_payloads',activity)
    api = client(tmp_path, database_factory=database_factory)
    user='status = Closed ORDER BY created DESC'
    api.post('/api/jira/analytics/search',json={'mode':'advanced','jql':user})
    started=api.post(f'/api/jira/cards/{card_key}/query',json={'period':'year'}).json()
    for _ in range(200):
        state=api.get(f"/api/jira/cards/{card_key}/tasks/{started['taskId']}").json()['state']
        if state not in {'queued','running'}: break
        time.sleep(.01)
    assert state=='completed'
    assert basic_queries == [item['jql'] for item in effective_card_jql_pair(card_key,user,'year').values()]
    assert len(activity_queries)==2 and activity_queries[0]!=activity_queries[1]
    assert activity_queries == [item['jql'] for item in activity_card_jql_pair(card_key,user).values()]
    from test_jira_history_schedule import refresh_test_history
    refresh_test_history(api)
    replay=api.get(f'/api/jira/cards/{card_key}/statistics').json()
    for period in ('current','previous'):
        people=[person for line in replay['periods']['year'][period]['productLines'] for person in line['people']]
        assert replay['periods']['year'][period]['teamTotal']==1
        assert sum(p['bugCount'] for p in people)==1
        assert sum(p['commentCount'] for p in people)==2
        assert sum(p['verifyCount'] for p in people)==1
        actor=next(p for p in replay['periods']['year'][period]['productLines'][4]['people'] if p['identity']=='meng.wang1')
        assert actor['commentCount']==2 and actor['verifyCount']==1
        assert 'outside' not in {p['identity'] for p in people}
    for period in ('week','month','quarter'):
        assert replay['periods'][period]
    assert len(basic_queries)==2 and len(activity_queries)==4


def test_comment_publication_preserves_unchanged_basic_facts_and_merges_independent_sources(
    tmp_path, monkeypatch, database_factory
):
    import json
    from core.jira.services import jql_statistics_cards as cards

    computed, writes = [], []
    build = cards.build_annual_facts

    def record_build(*args, **kwargs):
        result = build(*args, **kwargs)
        if "activity_facts" in kwargs:
            computed.append(deepcopy(result["_facts"]))
        return result

    monkeypatch.setattr(cards, "build_annual_facts", record_build)
    original_update = JiraAnalyticsRepository.update_statistics

    def record_update(repository, snapshot_id, statistics, **kwargs):
        if statistics.get("metricSources"):
            writes.append("facts" in kwargs)
        return original_update(repository, snapshot_id, statistics, **kwargs)

    monkeypatch.setattr(JiraAnalyticsRepository, "update_statistics", record_update)
    basic = {
        "id": "100",
        "key": "SH-100",
        "fields": {
            "summary": "fixed sample",
            "creator": {"name": "meng.wang1"},
            "project": {"key": "SH"},
            "status": {},
            "issuetype": {},
        },
    }
    monkeypatch.setattr(
        Gateway,
        "search_all_payloads",
        lambda self, query, **kwargs: dated_rows([basic], query),
    )
    activity = {
        "id": "100",
        "key": "SH-100",
        "fields": {"comment": {"comments": [{'id': 'action-886-44', "author": {"name": "meng.wang1"}}]}},
    }
    monkeypatch.setattr(
        Gateway,
        "search_activity_payloads",
        lambda self, query, **kwargs: dated_rows([activity], query),
    )
    api = client(tmp_path, database_factory=database_factory)
    started = query_card(api, {"mode": "advanced", "jql": "project = SH"}).json()
    assert wait_terminal(api, started["taskId"]) == "completed"
    assert len(computed) == 2
    assert writes == [True, True, False, False]
    repository = JiraAnalyticsRepository(database_factory())
    with repository.database.connect() as connection:
        stored = [
            json.loads(row[0])
            for row in connection.execute(
                text(
                    "SELECT fact_json FROM jira_analytics_facts WHERE snapshot_id=:id ORDER BY sequence"
                ),
                {"id": started["snapshotId"]},
            )
        ]
    assert stored == computed[1]
    payload = api.get("/api/jira/cards/self-test/statistics").json()
    assert payload["state"] == "ready"
    for period in ("current", "previous"):
        people = [
            person
            for line in payload["periods"]["year"][period]["productLines"]
            for person in line["people"]
        ]
        assert sum(person["bugCount"] for person in people) == 1
        assert sum(person["commentCount"] for person in people) == 1
