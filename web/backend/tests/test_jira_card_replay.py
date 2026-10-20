from sqlalchemy import text
from threading import Event
from datetime import date, timedelta
from core.jira.services.filter_service import jira_period_condition

from fastapi.testclient import TestClient
from test_jira_analytics_api import Gateway, client, query_card, wait_terminal


def test_statistics_returns_all_period_views_from_one_annual_snapshot_without_requery(
    tmp_path, monkeypatch, *, database_factory
):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path, database_factory=database_factory)
    api.post('/api/jira/analytics/search', json={'mode': 'basic', 'basic': {}})
    started = api.post('/api/jira/cards/self-test/query').json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    assert len(calls) == 2
    assert jira_period_condition('year') in calls[0]
    def reject(*_args, **_kwargs):
        raise AssertionError('local replay accessed Jira')
    from test_jira_analytics_api import FilterOwner
    monkeypatch.setattr(FilterOwner, 'validate', reject)
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    monkeypatch.setattr(Gateway, 'user_groups', reject)
    payload = api.get('/api/jira/cards/self-test/statistics').json()
    assert payload['state'] == 'ready'
    assert tuple(payload['periods']) == ('week', 'month', 'quarter', 'year')
    assert all(set(view['currentJqlByProductLineAndMetric']) == {
        'China Operator', 'Smart Device', 'TV', 'Global Operator & STB', 'Wireless Connection',
    } for view in payload['periods'].values())
    assert len(calls) == 2
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    assert api.get('/api/jira/cards/self-test/statistics').json()['state'] == 'ready'


def test_stale_statistics_version_is_rejected_before_decoding_annual_facts(
    tmp_path, monkeypatch, *, database_factory
):
    from core.jira.services.jql_statistics_cards import JiraStatisticsCardDefinition
    from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
    api = client(tmp_path, database_factory=database_factory)
    started = query_card(api, {'mode': 'basic', 'basic': {}}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    monkeypatch.setattr(JiraStatisticsCardDefinition, 'fingerprint', property(lambda _self: 'new-schema'))
    def reject(*_args, **_kwargs):
        raise AssertionError('stale facts must not be decoded and GET must not query Jira')
    monkeypatch.setattr(JiraAnalyticsRepository, 'local_statistics', reject)
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    payload = api.get('/api/jira/cards/self-test/statistics').json()
    assert payload['state'] == 'no_snapshot'


def test_same_account_relogin_restores_conditions_and_card_without_remote_fetch(
    tmp_path, monkeypatch, *, database_factory
):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path, database_factory=database_factory)
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
    assert restored['periods']['month']['teamTotal'] == 0


def test_wrong_year_snapshot_is_not_replayed_or_queried_by_get(
    tmp_path, monkeypatch, *, database_factory
):
    calls = []
    monkeypatch.setattr(Gateway, 'search_all_payloads', lambda _self, jql, **_kw: calls.append(jql) or [])
    api = client(tmp_path, database_factory=database_factory)
    started = query_card(api, {'mode': 'basic', 'basic': {}}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    database = database_factory()
    with database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE jira_analytics_snapshots SET jql=replace(jql,:value_1,:value_2) WHERE snapshot_id=:value_3"
            ),
            {
                "value_1": jira_period_condition("year"),
                "value_2": 'created >= "1999-01-01" AND created <= now()',
                "value_3": started["snapshotId"],
            },
        )
    payload = api.get('/api/jira/cards/self-test/statistics').json()
    assert payload['state'] == 'no_snapshot'
    assert 'productLines' not in payload
    assert len(calls) == 2


def test_another_session_can_read_same_account_result_but_not_task_or_cancel(
    tmp_path, monkeypatch, *, database_factory
):
    release = Event()
    def collect(_self, _jql, **_kwargs):
        assert release.wait(5)
        return []
    monkeypatch.setattr(Gateway, 'search_all_payloads', collect)
    first = client(tmp_path, database_factory=database_factory)
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


def test_login_and_session_restore_do_not_fetch_organization_architecture_or_jira(
    tmp_path, monkeypatch, *, database_factory
):
    def reject(*_args, **_kwargs):
        raise AssertionError('remote fetch must not start')
    monkeypatch.setattr('core.confluence.gateway.ConfluenceGateway.get_page', reject)
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    api = client(tmp_path, database_factory=database_factory)
    assert api.get('/api/auth/session').json()['authenticated'] is True
    assert api.get('/api/jira/cards/self-test/statistics').json() == {'state': 'no_snapshot'}


def test_annual_facts_survive_restart_and_periods_only_slice_issue_created_at(
    tmp_path, monkeypatch, *, database_factory
):
    from core.jira.services.filter_service import jira_period_ranges
    month = jira_period_ranges('month')
    dates = [month['current']['start'], month['previous']['start'], f"{date.today().year - 1}-01-01"]
    rows = [{"id": str(i), "key": f"TV-{i}", "fields": {"created": created + "T00:00:00+08:00",
        "project": {"key": "TV"}, "creator": {"name": "fan.xu"},
        "comment": {"comments": [{"id": "comment-1", "author": {"name": "fan.xu"}, "created": "2000-01-01T00:00:00Z", "body": "never-store"}]}},
        "changelog": {"histories": [{"id": "history-1", "author": {"name": "fan.xu"}, "created": "2020-01-01T00:00:00Z",
            "items": [{"field": "status", "fromString": "Resolved", "toString": "Verified"}]}]}}
        for i, created in enumerate(dates)]
    def search(_self, query, **kwargs):
        previous = f'created >= "{date.today().year - 1}-01-01"' in query
        return [row for row in rows if (row['fields']['created'][:4] != str(date.today().year)) == previous]
    monkeypatch.setattr(Gateway, 'search_all_payloads', search)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', lambda self, query, **kwargs: search(self,query))
    api = client(tmp_path, database_factory=database_factory)
    started = query_card(api, {'mode': 'basic', 'basic': {}}).json()
    assert wait_terminal(api, started['taskId']) == 'completed'
    from test_jira_history_schedule import refresh_test_history
    refresh_test_history(api)
    with database_factory().connect() as connection:
        facts = str(
            connection.execute(
                text("SELECT fact_json FROM jira_analytics_facts")
            ).fetchall()
        )
        assert 'never-store' not in facts and 'histories' not in facts
        # Existing Self-Test snapshots predate the Customer Feedback fields.
        import json
        for row in connection.execute(
            text("SELECT sequence, fact_json FROM jira_analytics_facts")
        ).fetchall():
            fact = json.loads(row[1])
            for line in fact['statistics']['productLines']:
                for field in ('components', 'issueCount', 'invalidCount'):
                    line.pop(field, None)
            connection.execute(
                text(
                    "UPDATE jira_analytics_facts SET fact_json=:fact_json WHERE sequence=:sequence"
                ),
                {"fact_json": json.dumps(fact), "sequence": row[0]},
            )
    def reject(*_args, **_kwargs): raise AssertionError('restart/period switch called Jira')
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', reject)
    monkeypatch.setattr(Gateway, 'user_groups', reject)
    restarted = client(tmp_path, database_factory=database_factory)
    payload = restarted.get('/api/jira/cards/self-test/statistics').json()
    assert payload['state'] == 'ready' and 'taskId' not in payload
    for period in ['week', 'month', 'quarter', 'year']:
        view = payload['periods'][period]
        for key, window in jira_period_ranges(period).items():
            count = sum(created >= window['start'] and (window['end'] is None or created < window['end']) for created in dates)
            assert view[key]['teamTotal'] == count
            for metric in ('bugCount', 'commentCount', 'verifyCount'):
                assert sum(person[metric] for line in view[key]['productLines'] for person in line['people']) == count
    restarted.post('/api/jira/analytics/search', json={'mode': 'advanced', 'jql': 'status = Closed'})
    changed = restarted.get('/api/jira/cards/self-test/statistics').json()
    assert changed['state'] == 'no_snapshot' and 'taskId' not in changed
    assert restarted.get('/api/jira/cards/task/statistics').json()['state'] == 'no_snapshot'


def test_supported_v4_annual_snapshot_restores_without_remote_and_keeps_scope_gates(
    tmp_path, monkeypatch, database_factory
):
    from core.jira.services.jql_statistics_cards import card_definition, effective_card_jql_pair, load_activity_qa_roster
    from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
    api = client(tmp_path, database_factory=database_factory)
    api.post('/api/jira/analytics/search', json={'mode': 'advanced', 'jql': 'project = SH'})
    repository = JiraAnalyticsRepository(database_factory())
    roster = load_activity_qa_roster().fingerprint
    pair = effective_card_jql_pair('task', 'project = SH', 'year')
    source = repository.begin('', 'coco', 'activity', {}, '', expires_at=0, card_key='legacy-activity')
    assert repository.update_statistics(source, {'queryRevision': 'r'}, facts=[])
    assert repository.activate(source, {'queryRevision': 'r'})
    metadata = {'annual': True, 'activityRoster': roster, 'roster': [],
        'availability': {period: {'basic': True, 'activity': True} for period in ('current', 'previous')},
        'activitySources': {period: {'snapshotId': source, 'revision': 'r'} for period in ('current', 'previous')}}
    snapshot = repository.begin('', 'coco', pair['current']['jql'], {}, '', expires_at=0,
        user_jql='project = SH', card_key='task', comparison_jql=pair['previous']['jql'],
        roster_fingerprint=f"{card_definition('task').fingerprint}:annual-activity-v4:{roster}")
    assert repository.update_statistics(snapshot, metadata, facts=[])
    assert repository.activate(snapshot, metadata)
    def reject(*args, **kwargs):
        raise AssertionError('restoring persisted facts must not access Jira')
    from test_jira_analytics_api import FilterOwner
    monkeypatch.setattr(Gateway, 'search_all_payloads', reject)
    monkeypatch.setattr(Gateway, 'search_activity_payloads', reject)
    monkeypatch.setattr(FilterOwner, 'validate', reject)
    restored = api.get('/api/jira/cards/task/statistics').json()
    assert restored['state'] == 'ready'
    assert tuple(restored['periods']) == ('week', 'month', 'quarter', 'year')
    assert restored['query']['activeSnapshotId'] == snapshot
    assert all('productLines' in view for view in restored['periods'].values()) and 'task' not in restored
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'bob', 'password': 'secret'})
    assert api.get('/api/jira/cards/task/statistics').json()['state'] == 'no_snapshot'
    api.post('/api/auth/logout')
    api.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    assert api.get('/api/jira/cards/task/statistics').json()['state'] == 'ready'
    with repository.database.transaction() as connection:
        connection.execute(text('UPDATE jira_analytics_snapshots SET jql=:jql WHERE snapshot_id=:id'),
            {'jql': 'different scope', 'id': snapshot})
    assert api.get('/api/jira/cards/task/statistics').json()['state'] == 'no_snapshot'
    with repository.database.transaction() as connection:
        connection.execute(text('UPDATE jira_analytics_snapshots SET jql=:jql,roster_fingerprint=:fingerprint WHERE snapshot_id=:id'),
            {'jql': pair['current']['jql'], 'fingerprint': f'changed-definition:annual-activity-v4:{roster}', 'id': snapshot})
    assert api.get('/api/jira/cards/task/statistics').json()['state'] == 'no_snapshot'
