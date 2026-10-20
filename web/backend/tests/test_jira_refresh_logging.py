from unittest.mock import Mock

from smarttest_web.jira import analytics_refresh as owner


def test_refresh_progress_throttles_logs_without_throttling_progress(monkeypatch):
    now = [0.0]
    records = []
    monkeypatch.setattr(owner, 'monotonic', lambda: now[0])
    monkeypatch.setattr(owner, 'smart_log', lambda message, **kwargs: records.append((message, kwargs)))
    callback = Mock()
    report = owner.JiraRefreshProgress('task', 'verify', callback)
    for count in range(1, 501):
        now[0] = count / 100
        report(count, 500)
    report.finish('completed')
    assert callback.call_count == 500
    assert [record[1]['extra']['outcome'] for record in records] == ['started', 'progress', 'completed']
    assert records[-1][1]['extra']['processed'] == 500
    assert records[-1][1]['extra']['total'] == 500
    assert all('card=task metric=verify' in message for message, _ in records)
    assert '500/500' in records[-1][0]


def test_refresh_failure_keeps_safe_context(monkeypatch):
    records = []
    monkeypatch.setattr(owner, 'smart_log', lambda message, **kwargs: records.append((message, kwargs)))
    report = owner.JiraRefreshProgress('self-test', 'basic', lambda *_: None)
    report(100, 200)
    report.finish('failed', error_code='jira_search_failed')
    assert records[-1][1]['level'] == 'ERROR'
    assert records[-1][1]['extra']['error_code'] == 'jira_search_failed'
    assert '100/200' in records[-1][0]
    assert 'error_code=jira_search_failed' in records[-1][0]


def test_metric_failure_is_logged_once_and_propagates_original_error(monkeypatch):
    import pytest
    repository, pages = Mock(), Mock()
    error = RuntimeError('private remote body')
    pages.run.side_effect = error
    records = []
    monkeypatch.setattr(owner, 'smart_log', lambda message, **kwargs: records.append((message, kwargs)))
    with pytest.raises(RuntimeError) as caught:
        owner.refresh_metric(repository, pages, Mock(), 'private-account', 'task', 'private-query',
                             Mock(fingerprint='roster', assignments=()), 'verify', Mock(), Mock())
    assert caught.value is error
    assert [item[1]['extra']['outcome'] for item in records] == ['started', 'failed']
    assert 'private' not in str(records)
    assert repository.finish.call_args.args[1:] == ('failed', 'RuntimeError')


def test_progress_callback_failure_is_not_suppressed(monkeypatch):
    import pytest
    monkeypatch.setattr(owner, 'smart_log', Mock())
    callback = Mock(side_effect=RuntimeError('cancelled'))
    report = owner.JiraRefreshProgress('task', 'basic', callback)
    with pytest.raises(RuntimeError, match='cancelled'):
        report(100, 200)
    callback.assert_called_once_with(100, 200)


def test_concurrent_refresh_objects_have_independent_log_intervals(monkeypatch):
    now = [0]
    records = []
    monkeypatch.setattr(owner, 'monotonic', lambda: now[0])
    monkeypatch.setattr(owner, 'smart_log', lambda message, **kwargs: records.append(kwargs['extra']))
    first = owner.JiraRefreshProgress('task', 'verify', lambda *_: None)
    now[0] = 3
    second = owner.JiraRefreshProgress('self-test', 'comment', lambda *_: None)
    now[0] = 5
    first(100, 300)
    second(100, 200)
    assert [(r['card_key'], r['outcome']) for r in records] == [
        ('task', 'started'), ('self-test', 'started'), ('task', 'progress')]
    now[0] = 8
    second(200, 200)
    second.finish('completed')
    assert records[-1]['processed'] == records[-1]['total'] == 200


def test_refresh_begin_failure_uses_shared_report_and_keeps_original_error(monkeypatch):
    import pytest
    records = []
    monkeypatch.setattr(owner, 'smart_log', lambda message, **kwargs: records.append(kwargs['extra']))
    callback = Mock()
    report = owner.JiraRefreshProgress('task', 'comment', callback)
    repository = Mock()
    error = RuntimeError('private database error')
    repository.begin.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        owner.refresh_metric(repository, Mock(), Mock(), 'private-account', 'task', 'private-query',
                             Mock(fingerprint='roster', assignments=()), 'comment', Mock(), callback, log_progress=report)
    assert caught.value is error
    assert report.finished
    assert [r['outcome'] for r in records] == ['started', 'failed']
    assert 'private' not in str(records)
    repository.finish.assert_not_called()
