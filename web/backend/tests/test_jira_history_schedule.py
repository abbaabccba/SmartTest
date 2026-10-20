from concurrent.futures import ThreadPoolExecutor

from smarttest_web.database import WebDatabase
from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
from core.jira.services.jql_statistics_cards import merge_annual_activity_facts
from core.jira.gateway import JiraGateway
from core.async_tasks import CancellationToken
from smarttest_web.jira.analytics_pages import JiraMetricPages


def refresh_test_history(api):
    from smarttest_web.jira.analytics_refresh import refresh_metric
    from core.jira.services.jql_statistics_cards import activity_card_jql_pair, load_activity_qa_roster
    from core.jira.services.filter_service import compose_jql
    owner = api.app.state.daily_refresh
    username, password = owner.sessions.saved_credentials('coco')
    for card, query in owner.repository.history_scopes('coco').items():
        gateway = owner.gateway_factory(username, password)[1]
        for window in activity_card_jql_pair(card, '').values():
            refresh_metric(owner.repository, owner.tasks.pages, gateway, 'coco', card,
                compose_jql(query, window['jql']), load_activity_qa_roster(), 'verify',
                CancellationToken(), lambda *_: None)


def test_metric_merge_does_not_erase_other_metric():
    def fact(comment, verify):
        return {'issueId': '1', 'createdAt': '2026-01-01', 'statistics': {
            'productLines': [{'id': 'line', 'people': [{'identity': 'qa',
                'commentCount': comment, 'verifyCount': verify}]}]}}
    facts = [fact(7, 0)]
    merge_annual_activity_facts(facts, [fact(0, 1)], metrics=('verifyCount',))
    person = facts[0]['statistics']['productLines'][0]['people'][0]
    assert person == {'identity': 'qa', 'commentCount': 7, 'verifyCount': 1}


def test_comment_page_never_requests_or_requires_history():
    class API:
        def jql(self, query, **kwargs):
            assert kwargs['fields'] == ['comment', 'attachment', 'created']
            assert kwargs['expand'] is None
            assert kwargs['limit'] == 100
            return {'total': 1, 'issues': [{'id': '1', 'fields': {
                'created': '2026-01-01', 'attachment': [], 'comment': {'total': 0, 'comments': []}}}]}
    gateway = JiraGateway('https://example.test', '', '', api=API())
    page = gateway.search_metric_page('query', 0, 'comment')
    assert len(page['issues']) == 1


def test_shared_page_budget_failure_releases_slots_for_other_jobs():
    from threading import Lock
    from time import sleep
    lock = Lock()
    active = [0, 0]
    class Gateway:
        def search_metric_page(self, query, offset, metric):
            with lock:
                active[0] += 1
                active[1] = max(active)
            try:
                sleep(.01)
                if query == 'bad':
                    raise ValueError('failure')
                return {'total': 300, 'issues': [{'id': str(i)} for i in range(offset, offset+100)]}
            finally:
                with lock:
                    active[0] -= 1
    pages = JiraMetricPages(workers=2)
    seen = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(pages.run, Gateway(), query, 'comment', CancellationToken(),
                                lambda rows: seen.extend(rows), lambda *_: None) for query in ('a', 'bad', 'b')]
            assert jobs[0].result() == jobs[2].result() == 300
            import pytest
            with pytest.raises(ValueError): jobs[1].result()
        assert active[1] <= 2 and len(seen) == 600
    finally:
        pages.close()


def test_search_does_not_fetch_history_or_mix_scopes_and_same_scope_refresh_survives_failure(tmp_path, monkeypatch, database_factory):
    from test_jira_analytics_api import Gateway, client, query_card, wait_terminal, dated_rows
    calls = []
    fail = [False]
    def page(self, query, start, metric):
        calls.append((query, metric))
        if metric == 'verify' and fail[0]: raise RuntimeError('offline')
        row = {'id': '1', 'key': 'SH-1', 'fields': {'comment': {'comments': [{'id': 'action-88-77', 'author': {'name': 'meng.wang1'}}]}}}
        if metric == 'verify':
            row['fields'].pop('comment')
            row['changelog'] = {'histories': [{'id': 'action-91-46', 'author': {'name': 'meng.wang1'}, 'created': '2026-01-01T00:00:00Z',
                'items': [{'field': 'status', 'fromString': 'Resolved', 'toString': 'Verified'}]}]}
        return {'total': 1, 'issues': dated_rows([row], query)}
    monkeypatch.setattr(Gateway, 'search_metric_page', page)
    api = client(tmp_path, database_factory=database_factory)
    task = query_card(api, {'mode': 'advanced', 'jql': 'project=A'}).json()
    assert wait_terminal(api, task['taskId']) == 'completed'
    assert {metric for _, metric in calls} == {'comment'}
    assert not api.get('/api/jira/cards/self-test/statistics').json()['periods']['year']['availability']['current']['verify']
    schedule = api.app.state.daily_refresh
    refresh_test_history(api)
    assert api.get('/api/jira/cards/self-test/statistics').json()['periods']['year']['availability']['current']['verify']
    task = api.post('/api/jira/cards/self-test/query', json={}).json()
    assert wait_terminal(api, task['taskId']) == 'completed'
    assert sum(metric == 'verify' for _, metric in calls) == 2
    assert api.get('/api/jira/cards/self-test/statistics').json()['periods']['year']['availability']['current']['verify']
    fail[0] = True
    import pytest
    with pytest.raises(Exception): refresh_test_history(api)
    assert api.get('/api/jira/cards/self-test/statistics').json()['periods']['year']['availability']['current']['verify']
    task = query_card(api, {'mode': 'advanced', 'jql': 'project=B'}).json()
    assert wait_terminal(api, task['taskId']) == 'completed'
    assert not api.get('/api/jira/cards/self-test/statistics').json()['periods']['year']['availability']['current']['verify']
    assert schedule.repository.history_scopes('another-account') == {}


def test_comment_page_missing_attachment_metadata_is_not_successful_zero():
    import pytest
    from core.jira.gateway import JiraGatewayError
    class API:
        def jql(self, *_args, **_kwargs):
            return {'total': 1, 'issues': [{'id': '1', 'fields': {'comment': {'total': 0, 'comments': []}}}]}
    gateway = JiraGateway('https://example.test', '', '', api=API())
    with pytest.raises(JiraGatewayError, match='jira_attachments_missing'):
        gateway.search_metric_page('query', 0, 'comment')
