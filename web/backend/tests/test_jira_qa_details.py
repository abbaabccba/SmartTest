from datetime import date

import pytest
from core.jira.services.jql_statistics_cards import QARoster, card_definition, effective_card_jql_pair
from smarttest_web.jira.analytics_refresh import metric_source_key
from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository


def evidence_repository(database, monkeypatch):
    roster = QARoster('roster', (('qa', ('TV',)), ('unassigned', ())))
    monkeypatch.setattr('core.jira.services.jql_statistics_cards.load_activity_qa_roster', lambda: roster)
    repo = JiraAnalyticsRepository(database, today=lambda: date(2026, 1, 2))
    conditions = repo.publish_conditions('', 'alice', {'userJql': ''}, expires_at=0)
    sources = {}
    for year, created in [('current', '2026-01-01'), ('previous', '2025-12-31')]:
        source = {'card': 'task', 'query': year}
        sources[year] = {'comment': source, 'verify': source}
        for metric in ('comment', 'verify'):
            key = metric_source_key('task', year, roster.fingerprint, metric)
            snapshot = repo.begin('', 'alice', year, {}, '', expires_at=0, card_key=key, roster_fingerprint=roster.fingerprint)
            events = []
            for index, account in enumerate(('qa', 'unassigned')):
                for number in range(27 if account == 'qa' else 1):
                    events.append({'issue_id': 'issue-' + year, 'source_id': f'{index}-{number}', 'event_kind': metric,
                        'author_identity': account, 'author_account': account, 'author_display_name': account,
                        'created_at': '2026-01-02T01:00:00+0800' if number == 0 else '2026-01-01T18:00:00Z', 'updated_at': None,
                        'payload': {'body': '<script>raw</script>', 'items': [{'fromString': 'Resolved', 'toString': 'Verified'}]}})
            metadata = {'annual': True, 'metric': metric, 'qaEvidence': {'version': 1, 'complete': True, 'roster': list(roster.assignments)}}
            repo.update_statistics(snapshot, metadata, facts=[{'issueId': 'issue-' + year, 'createdAt': created,
                'statistics': {'teamTotal': 0, 'unassignedCount': 0, 'unmappedCount': 0, 'productLines': []}}], qa_events=events)
            repo.activate(snapshot, metadata)
    pair = effective_card_jql_pair('task', '', 'year')
    base = repo.begin('', 'alice', pair['current']['jql'], {}, '', expires_at=0, card_key='task', user_jql='',
        comparison_jql=pair['previous']['jql'], roster_fingerprint=f'{card_definition("task").fingerprint}:annual-metrics-v5')
    metadata = {'annual': True, 'metricSources': sources,
        'availability': {kind: {'basic': True, 'comment': True, 'verify': True} for kind in ('current', 'previous')}}
    repo.update_statistics(base, metadata, facts=[]); repo.activate(base, metadata)
    return repo, base


def test_qa_details_bind_references_period_roster_and_paginate_without_changing_latest_count(application_database, monkeypatch):
    repo, base = evidence_repository(application_database, monkeypatch)
    reference = repo.local_statistics(base, 'week')['qaEvidence']['current']['verify']
    summary = repo.qa_details('alice', 'task', reference, product_line='TV')
    assert summary['total'] == 2  # Week crosses the year; latest-count facts remain zero.
    assert {row['historyCount'] for row in summary['rows']} == {27}
    first = summary['rows'][0]
    page = repo.qa_details('alice', 'task', reference, product_line='TV', issue_id=first['issueId'], person='qa', actions=True)
    assert page['total'] == 27 and len(page['rows']) == 25
    assert page['rows'][0]['createdAt'] == '2026-01-01T18:00:00Z'  # UTC > 01:00 +0800
    assert len(repo.qa_details('alice', 'task', reference, product_line='TV', issue_id=first['issueId'], person='qa', actions=True, page=1)['rows']) == 2
    download = repo.qa_details('alice', 'task', reference, all_qa=True, actions=True, download=True)
    assert download['pageSize'] == 1000 and len(download['rows']) == 56
    assert repo.qa_details('alice', 'task', reference, all_qa=True, actions=True)['pageSize'] == 25
    assert repo.qa_details('alice', 'task', reference, all_qa=True)['total'] == 4
    with pytest.raises(ValueError, match='details_version_changed'):
        repo.qa_details('bob', 'task', reference, all_qa=True)
    with pytest.raises(ValueError, match='details_version_changed'):
        repo.qa_details('alice', 'task', {**reference, 'baseSnapshotId': 'foreign'}, all_qa=True)
    assert repo.qa_details('alice', 'task', reference, product_line='TV', issue_id='foreign', actions=True)['total'] == 0
    forged = {**reference, 'sources': [{**item, 'snapshotId': 'foreign'} for item in reference['sources']]}
    with pytest.raises(ValueError, match='details_version_changed'):
        repo.qa_details('alice', 'task', forged, all_qa=True)


def test_qa_details_distinguish_uncollected_and_replaced_sources(application_database, monkeypatch):
    repo, base = evidence_repository(application_database, monkeypatch)
    reference = repo.local_statistics(base, 'year')['qaEvidence']['current']['comment']
    key = metric_source_key('task', 'current', 'roster', 'comment')
    pending = repo.begin('', 'alice', 'current', {}, '', expires_at=0, card_key=key, roster_fingerprint='roster')
    # Failed/pending data cannot replace the complete old active source.
    assert repo.qa_details('alice', 'task', reference, product_line='TV')['total'] == 1
    repo.update_statistics(pending, {'annual': True}, facts=[]); repo.activate(pending, {'annual': True})
    with pytest.raises(ValueError, match='details_version_changed'):
        repo.qa_details('alice', 'task', reference, product_line='TV')
    updated = repo.local_statistics(base, 'year')['qaEvidence']['current']['comment']
    with pytest.raises(ValueError, match='details_not_collected'):
        repo.qa_details('alice', 'task', updated, product_line='TV')


def test_qa_detail_route_authenticates_and_revalidates_account_references(tmp_path, database_factory, monkeypatch):
    from test_jira_analytics_api import client, Gateway
    api = client(tmp_path, database_factory=database_factory)
    api.app.state.daily_refresh.repository._today = lambda: date(2026, 1, 2)
    repo, base = evidence_repository(database_factory(), monkeypatch)
    reference = repo.local_statistics(base, 'week')['qaEvidence']['current']['comment']
    api.post('/api/auth/login', json={'username': 'alice', 'password': 'secret'})
    def reject(*_args, **_kwargs):
        pytest.fail('Details must not request remote Jira')
    monkeypatch.setattr(Gateway, 'search_activity_payloads', reject)
    payload = {'reference': reference, 'allQa': True}
    result = api.post('/api/jira/cards/task/details', json=payload)
    assert result.status_code == 200 and result.json()['total'] == 4
    assert all('payload' not in row for row in result.json()['rows'])
    body = '||Name||Result||\r\n|A|one\\\\two|\r\n\r\n{color:red}red{color} [https://example.com]\r\n<script>bad()</script><img src="https://example.com/pixel"><a href="javascript:bad()">bad link</a><a href="/relative">relative</a><span style="position:fixed">styled</span>'
    from sqlalchemy import update
    from smarttest_web.jira.schema import jira_analytics_qa_events
    import json
    with repo.database.transaction() as connection:
        connection.execute(update(jira_analytics_qa_events).where(jira_analytics_qa_events.c.event_kind == 'comment').values(payload_json=json.dumps({'body': body})))
    display = api.post('/api/jira/cards/task/details', json={**payload, 'actions': True})
    assert display.status_code == 200
    html = display.json()['rows'][0]['bodyHtml']
    assert '<table>' in html and 'one<br>two' in html and '<font color="red">red</font>' in html
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html
    assert all(value not in html for value in ('<script', '<img', 'javascript:', 'href="/relative"', 'style='))
    assert display.json()['rows'][0]['payload']['body'] == body
    monkeypatch.setattr('smarttest_web.jira_api.render_markup', lambda *_args, **_kwargs: pytest.fail('Download must not render markup'))
    actions = api.post('/api/jira/cards/task/details', json={**payload, 'actions': True, 'download': True})
    assert actions.status_code == 200 and actions.json()['pageSize'] == 1000
    assert len(actions.json()['rows']) == 56
    assert all(row['payload']['body'] == body and 'bodyHtml' not in row for row in actions.json()['rows'])
    with repo.database.transaction() as connection:
        connection.execute(update(jira_analytics_qa_events).where(jira_analytics_qa_events.c.event_kind == 'comment').values(payload_json=json.dumps({'body': {'type': 'doc', 'content': []}})))
    structured = api.post('/api/jira/cards/task/details', json={**payload, 'actions': True})
    assert structured.status_code == 200 and all('bodyHtml' not in row for row in structured.json()['rows'])
    assert api.post('/api/jira/cards/task/details', json={**payload, 'page': -1}).status_code == 422
    assert api.post('/api/jira/cards/task/details', json={**payload, 'reference': {**reference, 'baseSnapshotId': 'foreign'}}).status_code == 409
    api.post('/api/auth/login', json={'username': 'bob', 'password': 'secret'})
    assert api.post('/api/jira/cards/task/details', json=payload).status_code == 409
    api.post('/api/auth/logout')
    assert api.post('/api/jira/cards/task/details', json=payload).status_code == 401


def test_zero_collected_sources_are_empty_and_missing_cross_year_source_is_uncollected(application_database, monkeypatch):
    from sqlalchemy import delete
    from smarttest_web.jira.schema import jira_analytics_qa_events, jira_analytics_queries
    repo, base = evidence_repository(application_database, monkeypatch)
    reference = repo.local_statistics(base, 'week')['qaEvidence']['current']['comment']
    with application_database.transaction() as connection:
        connection.execute(delete(jira_analytics_qa_events))
    result = repo.qa_details('alice', 'task', reference, all_qa=True)
    assert result['total'] == 0 and result['totals'] == {'comment': 0, 'attachment': 0, 'verify': 0}
    key = metric_source_key('task', 'previous', 'roster', 'comment')
    with application_database.transaction() as connection:
        connection.execute(delete(jira_analytics_queries).where(jira_analytics_queries.c.account == 'alice', jira_analytics_queries.c.card_key == key))
    incomplete = repo.local_statistics(base, 'week')['qaEvidence']['current']['comment']
    with pytest.raises(ValueError, match='details_not_collected'):
        repo.qa_details('alice', 'task', incomplete, all_qa=True)
