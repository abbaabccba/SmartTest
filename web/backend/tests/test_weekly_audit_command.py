from datetime import datetime
import time
from zoneinfo import ZoneInfo

from core.weekly_audit import fixed_weekly_audit_scope
from smarttest_web.audit.email_history import AuditEmailHistory
from smarttest_web.audit.email_job import AuditEmailJob
from smarttest_web.audit.weekly_command import (
    ScheduledAuditRunner, install_windows_task, weekly_task_definition,
)


def test_windows_task_definition_is_fixed_to_friday_at_1800(tmp_path):
    definition = weekly_task_definition(tmp_path / 'python.exe', tmp_path / 'weekly_audit.py')
    assert definition == {
        'name': 'SmartTest.WeeklyAuditEmail',
        'weekday': 'FRI',
        'time': '18:00',
        'command': f'"{tmp_path / "python.exe"}" "{tmp_path / "weekly_audit.py"}"',
    }


def test_install_upserts_only_the_fixed_product_task(tmp_path):
    calls = []
    install_windows_task(tmp_path / 'python.exe', tmp_path / 'weekly_audit.py',
                         run=lambda args, **options: calls.append((args, options)))
    assert calls == [([
        'schtasks.exe', '/Create', '/F', '/TN', 'SmartTest.WeeklyAuditEmail',
        '/SC', 'WEEKLY', '/D', 'FRI', '/ST', '18:00', '/TR',
        f'"{tmp_path / "python.exe"}" "{tmp_path / "weekly_audit.py"}"',
    ], {'check': True})]


def test_scheduled_occurrence_is_claimed_once_across_runner_instances(
    tmp_path, *, database_factory
):
    history = AuditEmailHistory(database_factory())
    calls = []
    due = datetime(2026, 9, 14, 15, tzinfo=ZoneInfo('Asia/Shanghai'))
    assert ScheduledAuditRunner(history, lambda trigger, source: calls.append((trigger, source)) or 'completed').run(due) == 0
    assert ScheduledAuditRunner(history, lambda trigger, source: calls.append((trigger, source)) or 'completed').run(due) == 0
    assert calls == [(due, 'scheduled')]


def test_scheduled_failure_returns_nonzero_without_allowing_duplicate_delivery(
    tmp_path, *, database_factory
):
    history = AuditEmailHistory(database_factory())
    due = datetime(2026, 9, 14, 15, tzinfo=ZoneInfo('Asia/Shanghai'))
    assert ScheduledAuditRunner(history, lambda *_: 'partial').run(due) == 1
    assert ScheduledAuditRunner(history, lambda *_: 'completed').run(due) == 0


def test_scheduled_entry_uses_shared_job_to_send_both_reports(
    tmp_path, monkeypatch, *, database_factory
):
    from test_audit_email_api import ActualJira, Confluence
    sent = []
    monkeypatch.setattr('core.email.outlook.send_email', lambda **message: sent.append(message))
    history = AuditEmailHistory(database_factory())
    job = AuditEmailJob(history, reports_root=tmp_path / "audit-email-reports")
    due = datetime(2026, 9, 14, 15, tzinfo=ZoneInfo('Asia/Shanghai'))

    class Access:
        def require_active(self): pass

    def launch(trigger, source):
        result = job.trigger('ping.xiong', fixed_weekly_audit_scope(trigger),
                             Access(), 'secret', 0, None, lambda *_: ActualJira(), lambda *_: Confluence(),
                             trigger_source=source)
        for _ in range(200):
            result = history.get('ping.xiong', result['id'])
            if result['state'] not in {'queued', 'running'}:
                return result['state']
            time.sleep(.01)
        raise AssertionError('scheduled audit did not finish')

    try:
        assert ScheduledAuditRunner(history, launch).run(due) == 0
        assert [message['to'] for message in sent] == [
            ['fae.qa@amlogic.com'],
            ['fae.qa@amlogic.com'],
        ]
        latest = history.list_runs('ping.xiong')['runs'][0]
        assert latest['source'] == 'scheduled'
    finally:
        job.close()
