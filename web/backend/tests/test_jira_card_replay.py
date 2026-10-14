from threading import Event
from datetime import date, timedelta
from core.jira.services.filter_service import jira_period_condition

from fastapi.testclient import TestClient
from smarttest_web.database import WebDatabase
from test_jira_analytics_api import Gateway, client, query_card, wait_terminal


def test_period_switches_read_one_annual_snapshot_without_jira_or_tasks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path)
    api.post('/api/jira/analytics/search', json={'mode': 'basic', 'basic': {}})
    absent = api.post('/api/jira/cards/self-test/query', json={'period': 'week', 'intent': 'reuse'}).json()
    assert absent['state'] == 'no_snapshot' and 'taskId' not in absent
    assert calls == []
    started = api.post('/api/jira/cards/self-test/query', json={'intent': 'refresh'}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    assert len(calls) == 2
    assert jira_period_condition('year') in calls[0]
    def reject(*_args, **_kwargs):
        raise AssertionError('local replay accessed Jira')
    from test_jira_analytics_api import FilterOwner
    monkeypatch.setattr(FilterOwner, 'validate', reject)
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    monkeypatch.setattr(Gateway, 'user_groups', reject)
    for period in ['week', 'month', 'quarter', 'year']:
        payload = api.post('/api/jira/cards/self-test/query', json={'period': period, 'intent': 'reuse'}).json()
        assert payload['state'] == 'ready' and 'taskId' not in payload
        assert payload['query']['activeSnapshotId'] == started['snapshotId']
        assert payload['period'] == period
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    assert api.get('/api/jira/cards/self-test/statistics').json()['state'] == 'ready'



def test_card_period_defaults_to_month_and_restores_independently_without_get_fetch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path)
    api.put('/api/preferences/jira/cards/customer-feedback', json={'items': {'period': 'quarter'}})
    assert api.get('/api/jira/cards/self-test/statistics').json()['period'] == 'month'
    assert api.get('/api/jira/cards/customer-feedback/statistics').json()['period'] == 'quarter'
    api.post('/api/jira/analytics/search', json={'mode': 'advanced', 'jql': 'status = Open OR status = Closed ORDER BY created DESC'})
    periods = [('week', f'created >= "{(date.today() - timedelta(days=date.today().weekday())).isoformat()}"'),
               ('month', f'created >= "{date.today().replace(day=1).isoformat()}"'),
               ('quarter', f'created >= "{date.today().year}-{((date.today().month - 1) // 3) * 3 + 1:02d}-01"'),
               ('year', f'created >= "{date.today().year}-01-01"')]
    for period, condition in periods:
        started = api.post('/api/jira/cards/self-test/query', json={'period': period}).json()
        assert wait_terminal(api, started['taskId']) == 'completed'
        payload = api.get('/api/jira/cards/self-test/statistics').json()
        assert payload['period'] == period
        assert jira_period_condition("year") in calls[-2]
        assert calls[-2].endswith('ORDER BY created DESC')
        for response in [started, payload, api.get(f"/api/jira/cards/self-test/tasks/{started['taskId']}").json()]:
            assert not {'activeJql', 'activeComparisonJql', 'userJql'} & response['query'].keys()
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    assert api.get('/api/jira/cards/self-test/statistics').json()['period'] == 'year'
    assert len(calls) == 8
    assert api.post('/api/jira/cards/self-test/query', json={'period': 'invalid'}).status_code == 422
    assert len(calls) == 8
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'bob', 'password': 'secret'})
    assert api.get('/api/jira/cards/self-test/statistics').json()['period'] == 'month'
    assert len(calls) == 8


def test_stale_statistics_version_is_rejected_before_decoding_annual_facts(tmp_path, monkeypatch):
    from core.jira.services.jql_statistics_cards import JiraStatisticsCardDefinition
    from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
    api = client(tmp_path)
    started = query_card(api, {'mode': 'basic', 'basic': {}}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    monkeypatch.setattr(JiraStatisticsCardDefinition, 'fingerprint', property(lambda _self: 'new-schema'))
    def reject(*_args, **_kwargs):
        raise AssertionError('stale facts must not be decoded and GET must not query Jira')
    monkeypatch.setattr(JiraAnalyticsRepository, 'local_statistics', reject)
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    payload = api.get('/api/jira/cards/self-test/statistics').json()
    assert payload['state'] == 'no_snapshot'


def test_same_account_relogin_restores_conditions_and_card_without_remote_fetch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path)
    draft = {'mode': 'advanced', 'jql': 'reporter = "fan.xu"'}
    started = query_card(api, draft).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    original = api.get('/api/jira/cards/self-test/statistics').json()
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    restored = api.get('/api/jira/cards/self-test/statistics').json()
    assert restored == original
    assert api.get('/api/jira/analytics/state').json()['conditions']['jql'] == draft['jql']
    assert len(calls) == 2
    assert api.get('/api/dashboard/jira-team-bugs').status_code == 404
    assert restored['state'] == 'ready'
    assert restored['teamTotal'] == 0


def test_wrong_year_snapshot_is_not_replayed_or_queried_by_get(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path)
    started = query_card(api, {'mode': 'basic', 'basic': {}}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    database = WebDatabase(tmp_path / 'web.db')
    with database.transaction() as connection:
        connection.execute('UPDATE jira_analytics_snapshots SET jql=replace(jql,?,?) WHERE snapshot_id=?',
                           (jira_period_condition('year'), 'created >= "1999-01-01" AND created <= now()', started['snapshotId']))
    payload = api.get('/api/jira/cards/self-test/statistics').json()
    assert payload['state'] == 'no_snapshot'
    assert 'productLines' not in payload
    assert len(calls) == 2


def test_another_session_can_read_same_account_result_but_not_task_or_cancel(tmp_path, monkeypatch):
    release = Event()
    def collect(_self, _jql, **_kwargs):
        assert release.wait(5)
        return []
    monkeypatch.setattr(Gateway, 'search_all_payloads', collect)
    first = client(tmp_path)
    second = TestClient(first.app, base_url='https://testserver')
    second.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    try:
        started = query_card(first, {'mode': 'basic', 'basic': {}}).json()
        for _ in range(3):
            shared = second.get('/api/jira/cards/self-test/statistics').json()
            assert shared['state'] == 'loading'
            assert shared['query']['taskId'] == ''
            assert shared['query']['pendingSnapshotId'] == started['snapshotId']
        assert second.get(f"/api/jira/cards/self-test/tasks/{started['taskId']}").status_code == 404
        assert second.delete(f"/api/jira/cards/self-test/tasks/{started['taskId']}").status_code == 404
        assert first.get(f"/api/jira/cards/self-test/tasks/{started['taskId']}").status_code == 200
    finally:
        release.set()
    assert wait_terminal(first, started['taskId']) == 'completed'
    assert second.get('/api/jira/cards/self-test/statistics').json()['state'] == 'ready'


def test_login_and_session_restore_do_not_fetch_organization_architecture_or_jira(tmp_path, monkeypatch):
    def reject(*_args, **_kwargs):
        raise AssertionError('remote fetch must not start')
    monkeypatch.setattr('core.confluence.gateway.ConfluenceGateway.get_page', reject)
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    api = client(tmp_path)
    assert api.get('/api/auth/session').json()['authenticated'] is True
    assert api.get('/api/jira/cards/self-test/statistics').json() == {'state': 'no_snapshot', 'period': 'month'}


def test_annual_facts_survive_restart_and_periods_only_slice_issue_created_at(tmp_path, monkeypatch):
    from core.jira.services.filter_service import jira_period_ranges
    month = jira_period_ranges('month')
    dates = [month['current']['start'], month['previous']['start'], f"{date.today().year - 1}-01-01"]
    rows = [{"id": str(i), "key": f"TV-{i}", "fields": {"created": created + "T00:00:00+08:00",
        "project": {"key": "TV"}, "creator": {"name": "fan.xu"},
        "comment": {"comments": [{"author": {"name": "fan.xu"}, "created": "2000-01-01T00:00:00Z", "body": "never-store"}]}},
        "changelog": {"histories": [{"author": {"name": "fan.xu"}, "created": "2020-01-01T00:00:00Z",
            "items": [{"field": "status", "fromString": "Resolved", "toString": "Verified"}]}]}}
        for i, created in enumerate(dates)]
    def search(_self, query, **kwargs):
        previous = f'created >= "{date.today().year - 1}-01-01"' in query
        return [row for row in rows if (row['fields']['created'][:4] != str(date.today().year)) == previous]
    monkeypatch.setattr(Gateway, 'search_all_payloads', search)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', lambda self, query, **kwargs: search(self,query))
    api = client(tmp_path)
    started = query_card(api, {'mode': 'basic', 'basic': {}}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    with WebDatabase(tmp_path / 'web.db').connect() as connection:
        facts = str(connection.execute('SELECT fact_json FROM jira_analytics_facts').fetchall())
        assert 'never-store' not in facts and 'histories' not in facts
        # Existing Self-Test snapshots predate the Customer Feedback fields.
        import json
        for row in connection.execute('SELECT rowid, fact_json FROM jira_analytics_facts').fetchall():
            fact = json.loads(row[1])
            for line in fact['statistics']['productLines']:
                for field in ('components', 'issueCount', 'invalidCount'):
                    line.pop(field, None)
            connection.execute('UPDATE jira_analytics_facts SET fact_json=? WHERE rowid=?',
                               (json.dumps(fact), row[0]))
    def reject(*_args, **_kwargs): raise AssertionError('restart/period switch called Jira')
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', reject)
    monkeypatch.setattr(Gateway, 'user_groups', reject)
    restarted = client(tmp_path)
    for period in ['week', 'month', 'quarter', 'year']:
        payload = restarted.post('/api/jira/cards/self-test/query', json={'intent': 'reuse', 'period': period}).json()
        assert payload['state'] == 'ready' and 'taskId' not in payload
        for key, window in jira_period_ranges(period).items():
            count = sum(created >= window['start'] and (window['end'] is None or created < window['end']) for created in dates)
            assert payload[key]['teamTotal'] == count
            for metric in ('bugCount', 'commentCount', 'verifyCount'):
                assert sum(person[metric] for line in payload[key]['productLines'] for person in line['people']) == count
    restarted.post('/api/jira/analytics/search', json={'mode': 'advanced', 'jql': 'status = Closed'})
    changed = restarted.post('/api/jira/cards/self-test/query', json={'intent': 'reuse'}).json()
    assert changed['state'] == 'no_snapshot' and 'taskId' not in changed
    assert restarted.get('/api/jira/cards/task/statistics').json()['state'] == 'no_snapshot'
