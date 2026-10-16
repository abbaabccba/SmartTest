from datetime import date

from smarttest_web.audit.email_history import AuditEmailHistory
from smarttest_web.database import WebDatabase


def test_history_is_shared_dated_and_paginated_without_inventing_missing_values(tmp_path):
    history = AuditEmailHistory(WebDatabase(tmp_path / 'history.db'))
    for account in ('coco', 'other'):
        for day in range(1, 13):
            run = history.create(account, {})
            run['createdAt'] = f'2026-10-{day:02d}T02:00:00+00:00'
            run['summary']['jira'] = {'total': day, 'passed': 1, 'failed': day - 1, 'rate': 'saved%'}
            history.save(account, run)
    page = history.browse('coco', 'jira')
    assert page['total'] == 24
    assert len(page['rows']) == 10
    assert [row['total'] for row in page['rows']] == [12, 12, 11, 11, 10, 10, 9, 9, 8, 8]
    assert page['rows'][0]['date'] == '2026-10-12'
    assert page['rows'][0]['modifiedPassed'] is None
    filtered = history.browse('coco', 'jira', start_date=date(2026, 10, 3), end_date=date(2026, 10, 5))
    assert [row['total'] for row in filtered['rows']] == [5, 5, 4, 4, 3, 3]
    assert history.browse('coco', 'jira', offset=24)['rows'] == []


def test_confluence_history_is_one_period_per_row_with_four_product_line_groups(tmp_path):
    from core.product_lines import PRODUCT_LINES
    history = AuditEmailHistory(WebDatabase(tmp_path / 'history.db'))
    for _ in range(12):
        run = history.create('coco', {})
        run['summary']['confluence'] = {line.name: [0, 8] for line in PRODUCT_LINES}
        history.save('coco', run)
    page = history.browse('coco', 'confluence')
    assert page['total'] == 12 and len(page['rows']) == 10
    assert page['productLines'] == [line.name for line in PRODUCT_LINES]
    row = page['rows'][0]
    assert len(row['lines']) == 4
    assert all(line['projectCount'] is None and line['passed'] == 0 and line['failed'] == 8 for line in row['lines'])
    assert len(history.browse('coco', 'confluence', offset=10)['rows']) == 2


def test_confluence_project_count_does_not_substitute_checkpoint_total(tmp_path):
    from core.product_lines import PRODUCT_LINES
    history = AuditEmailHistory(WebDatabase(tmp_path / 'history.db'))
    run = history.create('coco', {})
    summary = {line.name: [0, 0] for line in PRODUCT_LINES}
    summary['TV'] = [2, 3]
    summary['projects'] = [{'productLine': 'TV', 'projectId': 'saved-project'}]
    run['summary']['confluence'] = summary
    history.save('coco', run)
    row = next(line for line in history.browse('coco', 'confluence')['rows'][0]['lines'] if line['productLine'] == 'TV')
    assert row['projectCount'] == 1
    assert (row['passed'], row['failed']) == (2, 1)
    assert history.get('coco', run['id'])['summary']['confluence']['projects'] == summary['projects']


def test_jira_modified_count_reuses_saved_reaudit_without_overwriting_current_summary(tmp_path):
    history = AuditEmailHistory(WebDatabase(tmp_path / 'history.db'))
    run = history.create('coco', {})
    run['summary']['jira'] = {'total': 74, 'passed': 60, 'failed': 14, 'rate': '81.08%'}
    run['reports']['jira']['remediation'] = {'state': 'completed', 'currentSummary': {
        'total': 123, 'passed': 97, 'failed': 26, 'rate': '78.86%', 'issues': [{'key': 'saved-reaudit'}]}}
    history.save('coco', run)
    row = history.browse('coco', 'jira')['rows'][0]
    assert (row['total'], row['passed'], row['failed'], row['rate']) == (74, 60, 14, '81.08%')
    assert row['modifiedPassed'] == 97
    assert row['modifiedRate'] == '78.86%'
    assert history.get('coco', run['id'])['reports']['jira']['remediation']['currentSummary']['issues'] == [{'key': 'saved-reaudit'}]


def test_history_query_logs_database_account_and_filter_counts_without_sensitive_payload(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('SMARTTEST_LOG_DIR', str(tmp_path / 'logs'))
    history = AuditEmailHistory(WebDatabase(tmp_path / 'history.db'))
    private = history.create('owner', {})
    private['summary']['jira'] = {'total': 1, 'scope': 'DO NOT LOG PRIVATE JQL'}
    history.save('owner', private)
    history.browse('viewer', 'jira', start_date=date(2026, 1, 1))
    records = [json.loads(line) for line in (tmp_path / 'logs' / 'smarttest.log').read_text(encoding='utf-8').splitlines()]
    record = next(record for record in records if record['source'] == 'audit_email_history')
    assert record['platform'] == 'web'
    fields = record['extra']
    assert fields['account'] == 'viewer' and fields['kind'] == 'jira'
    assert fields['database_path'] == str(history.database.path.resolve())
    assert (fields['database_runs'], fields['visible_runs'], fields['candidate_rows'], fields['filtered_rows'], fields['returned_rows']) == (1, 1, 1, 1, 1)
    assert fields['offset'] == 0 and fields['start_date'] == '2026-01-01'
    assert 'DO NOT LOG PRIVATE JQL' not in json.dumps(records)


def test_shared_history_read_does_not_share_execution_writes_or_scheduled_claims(tmp_path):
    history = AuditEmailHistory(WebDatabase(tmp_path / 'history.db'))
    period = {'startDate': '2026-10-02T18:00:00+08:00', 'endDate': '2026-10-09T18:00:00+08:00'}
    original = history.create('owner', period)
    original['reports']['jira']['state'] = 'completed'
    history.save('owner', original)
    assert history.previous_jira_audit('owner', period)['id'] == original['id']
    assert history.previous_jira_audit('viewer', period) is None
    viewed = history.get('viewer', original['id'])
    viewed['state'] = 'failed'
    history.save('viewer', viewed)
    assert history.get('viewer', original['id'])['state'] == 'queued'
    assert history.claim_scheduled_occurrence('owner', 'same-period')
    assert history.claim_scheduled_occurrence('viewer', 'same-period')
    assert not history.claim_scheduled_occurrence('owner', 'same-period')


def test_reopening_history_does_not_restore_screenshot_rows(tmp_path):
    database = WebDatabase(tmp_path / 'history.db')
    history = AuditEmailHistory(database)
    assert history.list_runs('viewer') == {'runs': [], 'total': 0}
    assert AuditEmailHistory(database).browse('viewer', 'jira')['rows'] == []
