from dataclasses import replace
from datetime import datetime, timedelta
import time

from fastapi.testclient import TestClient
from core.confluence.audit.models import AuditBatch, AuditFinding, AuditStatus, ProjectAudit
from core.confluence.audit import manual_audit_period
from core.confluence.audit.exporter import export_audit_xlsx_by_product_line
from core.confluence.project import Project, ProjectIdentity, ProductSpaceRef, ConfluencePageRef
from core.jira.audit.models import IssueAuditResult
from smarttest_web.app import create_app
from test_web_session import FakeAuthenticator, FakeFactsOwner
from test_manual_audit_api import JiraOwner
import pytest


@pytest.fixture(autouse=True)
def no_real_weekly_email(monkeypatch):
    sent = []
    monkeypatch.setattr('core.email.outlook.send_email', lambda **message: sent.append(message))
    return sent


class ActualJira(JiraOwner):
    def run(self, scope, cancellation, progress):
        report = super().run(scope, cancellation, progress)
        return replace(report, issues=(IssueAuditResult('SH-1', 'https://jira.amlogic.com/browse/SH-1', 'test', 'QA', True, ()),))


def test_summary_failure_is_not_labeled_export_failure(monkeypatch, no_real_weekly_email):
    def fail_summary(*args):
        raise KeyError('DOPL')
    monkeypatch.setattr('smarttest_web.audit.email_job.summarize_audit', fail_summary)
    with TestClient(make_app(), base_url='https://testserver') as client:
        client.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
        result = trigger(client)
        assert result['reports']['jira']['stage'] == 'summarizing'
        assert result['reports']['confluence']['stage'] == 'summarizing'
        assert no_real_weekly_email == []


class Facts(FakeFactsOwner):
    def refresh(self, access, password):
        pass

    def query(self, access, **kwargs):
        return {'state': 'ready', 'projects': [{'project_id': 'P1', 'fields': {'current stage': '2 DEVELOPMENT'}}]}


class Confluence:
    def resolve(self, payload):
        assert 'projectIds' not in payload
        assert payload['filters']['support mode'] == ['A', 'B']
        assert payload['excludeCurrentStageAtOrAbove'] == 4
        assert payload['excludeSupportModeBProductLines'] == ['SDPL']
        return manual_audit_period(datetime.fromisoformat(payload['startDate']).date(), datetime.fromisoformat(payload['endDate']).date())

    def run(self, period, cancellation, progress):
        project = Project(ProjectIdentity('1', 'P1'), 'Project', ProductSpaceRef('TV'), ConfluencePageRef('1'))
        finding = AuditFinding('P1', 'Test', 'test.weekly', AuditStatus.UPDATED, 'changed')
        return AuditBatch('batch', period, datetime.now(), (ProjectAudit(project, (finding,)),))

    def export(self, report, directory):
        return export_audit_xlsx_by_product_line(report, directory)


def make_app(**kwargs):
    return create_app(authenticator=FakeAuthenticator, project_facts_owner=Facts,
                      jira_audit_owner=lambda *_: ActualJira(), confluence_audit_owner=lambda *_: Confluence(), **kwargs)


@pytest.mark.parametrize('has_findings', [True, False])
def test_previous_completed_audit_reuses_sqlite_filter_without_delivery_and_confluence_remediation(tmp_path, no_real_weekly_email, has_findings):
    from core.weekly_audit import fixed_weekly_audit_scope
    from core.jira.audit.models import AuditRule
    from smarttest_web.audit.email_history import AuditEmailHistory
    from smarttest_web.audit.email_job import AuditEmailJob
    from smarttest_web.database import WebDatabase
    from zoneinfo import ZoneInfo
    history = AuditEmailHistory(WebDatabase(tmp_path / 'audit.db'))
    current_scope = fixed_weekly_audit_scope(datetime(2026, 9, 18, 18, tzinfo=ZoneInfo('Asia/Shanghai')))
    old = history.create('qa', current_scope['previousPeriod'])
    old['createdAt'] = '2026-09-14T07:00:00+00:00'
    saved = 'key = SH-1 AND created >= 2026-09-07 AND created <= 2026-09-11'
    old['summary']['jira'] = {'total': 1, 'passed': 0, 'failed': 1, 'rate': '0%',
                            'issues': [], 'scope': saved, 'findings': [
        {'resourceId': 'SH-1', 'ruleId': 'r1', 'reason': 'old', 'url': 'https://jira/SH-1'}]}
    old['reports']['jira']['state'] = 'completed'
    if not has_findings:
        del old['summary']['jira']['findings']
    old['deliveries'] = {'jira': {'state': 'failed'}}
    history.save('qa', old)
    class Owner(ActualJira):
        scopes = []
        def run(self, scope, cancellation, progress):
            self.scopes.append(scope.jql)
            return replace(super().run(scope, cancellation, progress), rules=(AuditRule('r1', '', '', '', ''),))
    class Access:
        def require_active(self): pass
    owner = Owner()
    job = AuditEmailJob(history)
    try:
        result = job.trigger('qa', current_scope, Access(), 'secret', 0, None,
                             lambda *_: owner, lambda *_: Confluence(), trigger_source='manual')
        for _ in range(200):
            result = history.get('qa', result['id'])
            if result['state'] not in {'queued', 'running'}: break
            time.sleep(.01)
        assert result['state'] == 'completed'
        assert owner.scopes == [current_scope['jira']['jql'], saved]
        remediation = result['reports']['jira']['remediation']
        assert remediation['currentSummary']['total'] == 1
        if has_findings:
            assert remediation['findings'][0]['state'] == 'fixed'
        else:
            assert remediation['findings'] == []
            assert 'Previous_Audit_Current.xlsx' in result['reports']['jira']['attachments']
        assert 'previous_comparison_completed' in result['evidence']
        assert 'Previous_Audit_Remediation.xlsx' in result['reports']['jira']['attachments']
        assert result['reports']['confluence']['state'] == 'completed'
        assert 'remediation' not in result['reports']['confluence']
        persisted = AuditEmailHistory(WebDatabase(tmp_path / 'audit.db')).get('qa', result['id'])
        assert persisted['summary']['confluence']['projects'][0]['rules'][0]['status'] == 'updated'
        assert persisted['summary']['jira']['issues'][0]['aiReviewStatus'] == 'not_required'
        assert persisted['summary']['jira']['period'] == {key: current_scope[key] for key in ('startDate', 'endDate')}
        assert persisted['reports']['jira']['remediation']['currentSummary']['period'] == current_scope['previousPeriod']
        assert persisted['reports']['jira']['remediation']['currentSummary']['rules'][0]['ruleId'] == 'r1'
    finally:
        job.close()


def login(client):
    client.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
    client.put('/api/jira/filter-snapshot', json={'filters': {'project': ['SH']}, 'jql': ''})
    client.put('/api/confluence/filter-snapshot', json={'filters': {}, 'search': ''})


def trigger(client):
    response = client.post('/api/audit-email/runs')
    assert response.status_code == 200
    run_id = response.json()['id']
    for _ in range(200):
        result = client.get(f'/api/audit-email/runs/{run_id}').json()
        if result['state'] not in {'queued', 'running'}:
            return result
        time.sleep(.01)
    raise AssertionError('audit did not finish')


def test_real_dual_runs_keep_five_records_and_attachments_after_restart():
    with TestClient(make_app(), base_url='https://testserver') as client:
        assert client.post('/api/audit-email/runs').status_code == 401
        assert client.get('/api/audit-email/runs').status_code == 401
        login(client)
        assert len(client.get('/api/audit-email/runs').json()['runs']) == 0
        runs = [trigger(client) for _ in range(5)]
        assert len({run['id'] for run in runs}) == 5
        for index, run in enumerate(runs):
            assert run['state'] == 'completed', run['evidence']
            assert run['summary']['jira']['total'] == 1
            assert run['summary']['confluence']['TV'] == [1, 1]
            assert run['reports']['jira']['sourceIds'][0] == run['id']
            assert len(run['reports']['jira']['sourceIds']) == min(index + 1, 4)
            assert 'project in (IPTV, SH, TV, OTT,RK)' in run['reports']['jira']['html']
        assert len(runs[0]['reports']['confluence']['sourceIds']) == 1
        assert len(runs[-1]['reports']['confluence']['sourceIds']) == 4
        first = runs[0]
        for kind in ('jira', 'confluence'):
            assert first['reports'][kind]['attachments']
            name = first['reports'][kind]['attachments'][0]
            assert client.get(f'/api/audit-email/runs/{first["id"]}/attachments/{kind}/{name}').content[:2] == b'PK'
    with TestClient(make_app(), base_url='https://testserver') as client:
        login(client)
        assert client.get(f'/api/audit-email/runs/{first["id"]}').json() == first
        listing = client.get('/api/audit-email/runs').json()
        assert listing['total'] == 5
        assert len(listing['runs']) == 4
        assert len(client.get('/api/audit-email/runs?offset=4').json()['runs']) == 1
        name = first['reports']['jira']['attachments'][0]
        assert client.get(f'/api/audit-email/runs/{first["id"]}/attachments/jira/{name}').content[:2] == b'PK'
        assert client.get(f'/api/audit-email/runs/{first["id"]}/attachments/jira/no.xlsx').status_code == 404
        assert client.get('/api/audit-email/runs/missing').status_code == 404


def test_trigger_uses_fixed_scope_sends_two_emails_without_creating_global_filter_snapshots(no_real_weekly_email):
    with TestClient(make_app(), base_url='https://testserver') as client:
        client.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
        result = trigger(client)
        assert result['state'] == 'completed'
        assert client.get('/api/jira/filter-snapshot').json()['snapshot'] is None
        assert client.get('/api/confluence/project-facts').json()['querySnapshot'] is None
        assert [message['to'] for message in no_real_weekly_email] == [
            ['fae.qa@amlogic.com'],
            ['fae.qa@amlogic.com'],
        ]
        assert result['deliveries']['jira']['state'] == 'accepted'
        assert result['deliveries']['confluence']['state'] == 'accepted'
        assert 'report_type=jira stage=scope_resolved duration_ms=' in result['evidence']
        assert 'report_type=jira stage=audit_completed duration_ms=' in result['evidence']
        assert 'report_type=jira stage=export_completed duration_ms=' in result['evidence']
        assert 'report_type=jira stage=smtp_accepted error_code= duration_ms=' in result['evidence']


def test_one_mail_failure_does_not_prevent_the_other_delivery(monkeypatch):
    sent = []
    def send(**message):
        sent.append(message)
        if len(sent) == 1:
            raise RuntimeError('private transport detail')
    monkeypatch.setattr('core.email.outlook.send_email', send)
    with TestClient(make_app(), base_url='https://testserver') as client:
        login(client)
        result = trigger(client)
    assert result['state'] == 'partial'
    assert result['deliveries']['jira']['state'] == 'failed'
    assert result['deliveries']['jira']['error'] == 'RuntimeError'
    assert result['deliveries']['confluence']['state'] == 'accepted'
    assert len(sent) == 2
    assert 'private transport detail' not in result['evidence']


def test_authenticated_accounts_share_history_details_and_attachments_without_retriggering(no_real_weekly_email):
    auth = FakeAuthenticator()
    app = create_app(authenticator=lambda: auth, project_facts_owner=Facts,
                     jira_audit_owner=lambda *_: ActualJira(), confluence_audit_owner=lambda *_: Confluence())
    with TestClient(app, base_url='https://testserver') as client:
        login(client)
        result = trigger(client)
        client.post('/api/auth/logout')
        auth.result = {'success': True, 'username': 'other', 'display_name': 'Other', 'avatar_bytes': b''}
        client.post('/api/auth/login', json={'username': 'other', 'password': 'secret'})
        assert client.get(f'/api/audit-email/runs/{result["id"]}').json() == result
        name = result['reports']['jira']['attachments'][0]
        assert client.get(f'/api/audit-email/runs/{result["id"]}/attachments/jira/{name}').content[:2] == b'PK'
        assert client.get('/api/audit-email/history/jira').json()['total'] == 1
        assert client.get('/api/audit-email/history/confluence').json()['total'] == 1
        assert len(no_real_weekly_email) == 2
        client.post('/api/auth/logout')
        for path in ('/api/audit-email/runs', '/api/audit-email/history/jira',
                     f'/api/audit-email/runs/{result["id"]}',
                     f'/api/audit-email/runs/{result["id"]}/attachments/jira/{name}'):
            assert client.get(path).status_code == 401
        client.post('/api/auth/login', json={'username': 'other', 'password': 'secret'})
        assert client.get('/api/audit-email/runs').json()['total'] == 1


def test_trigger_ignores_singleton_snapshots_and_legacy_jira_preference():
    template = 'project=SH AND created >=2026-08-28 AND created <=2026-09-4 order by updated DESC'
    with TestClient(make_app(), base_url='https://testserver') as client:
        login(client)
        client.put('/api/preferences/jira.html', json={'items': {'auditInput': 'project=WRONG'}, 'schemaVersion': 1})
        client.put('/api/jira/filter-snapshot', json={'filters': {}, 'jql': template})
        result = trigger(client)
        assert result['state'] == 'completed'
        scope = result['scope']
        jira_start = datetime.fromisoformat(scope['startDate']).strftime('%Y-%m-%d %H:%M')
        jira_end = datetime.fromisoformat(scope['endDate']).strftime('%Y-%m-%d %H:%M')
        expected = (
            'project in (IPTV, SH, TV, OTT,RK) AND issuetype in (Bug, Sub-bug) '
            f'AND created >= "{jira_start}" AND created < "{jira_end}" order by updated DESC'
        )
        assert scope['jiraInput'] == result['summary']['jira']['scope'] == expected
        assert scope['jiraTemplate'] == expected
        assert 'WRONG' not in scope['jiraInput']
        assert 'project=SH' not in scope['jiraInput']


def test_obsolete_event_and_editable_schedule_routes_are_removed():
    with TestClient(make_app(), base_url='https://testserver') as client:
        login(client)
        for method, path in [('get', '/api/audit-email/events'), ('post', '/api/audit-email/events'),
                             ('get', '/api/audit-email/schedule'), ('put', '/api/audit-email/schedule'),
                             ('delete', '/api/audit-email/schedule')]:
            assert getattr(client, method)(path).status_code == 404


def test_history_browser_gets_only_saved_account_data_and_validates_date_ranges(no_real_weekly_email):
    with TestClient(make_app(), base_url='https://testserver') as client:
        assert client.get('/api/audit-email/history/jira').status_code == 401
        client.post('/api/auth/login', json={'username': 'coco', 'password': 'secret'})
        result = client.get('/api/audit-email/history/jira').json()
        assert result == {'rows': [], 'total': 0}
        assert client.get('/api/audit-email/history/confluence').json()['total'] == 0
        assert client.get('/api/audit-email/history/jira?start_date=2026-01-01').json()['total'] == 0
        assert client.get('/api/audit-email/history/jira?offset=-1').status_code == 422
        assert client.get('/api/audit-email/history/jira?start_date=2026-10-20&end_date=2026-10-01').status_code == 422
        assert client.get('/api/audit-email/history/unknown').status_code == 422
        assert no_real_weekly_email == []
