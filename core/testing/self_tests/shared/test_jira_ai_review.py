from __future__ import annotations

import json
from threading import Lock
from time import sleep

import pytest

from core.ai import AIChatResponse, AIConfigurationError, AIResponseError, AITransportError
from core.domain.detail import DetailSection
from core.domain.values import FieldBag, NamedValue, PersonRef
from core.jira.audit import AIReviewStatus, JiraAuditScope, JiraAuditUseCase
from core.jira.domain import Issue, IssueIdentity, JiraProjectRef, RichText


GOOD_DESCRIPTION = """Steps to reproduce: Open video
Actual results: Freeze
Expected results: Playback
Reproducibility rate: 1/2
Comparison: Previous build works
Notes:
HW info: board A
SW info: build 1
"""


def test_ai_review_cannot_remove_missing_self_test_case_id():
    from dataclasses import replace
    from core.jira.audit.rules import audit_issue
    from core.jira.audit.ai_review import review_issue
    value = replace(issue(components=False), custom_fields=DetailSection.loaded(FieldBag.from_mapping(
        {'Channel of Reporter': {'value': 'Self-Test'}, 'Test Case ID': None})))
    result = audit_issue(value)
    response = json.dumps({'issue_key': result.key, 'decisions': [
        {'rule_id': item.rule_id, 'result': 'PASS', 'reason': '', 'guidance': ''} for item in result.violations]})
    reviewed = review_issue(result, AIClient([response]))
    assert not reviewed.passed
    assert [item.rule_id for item in reviewed.violations] == ['TEST_CASE_ID.REQUIRED']
    assert '缺少 Test Case ID' in reviewed.violations[0].reason
    assert (reviewed.ai_passed_count, reviewed.ai_failed_count) == (1, 1)


def test_prompt_preserves_natural_semantics_and_explicit_json_contract():
    from core.jira.audit.ai_review import _prompt
    from core.jira.audit.rules import audit_issue
    result = audit_issue(issue(components=False))
    prompt = _prompt(result)
    assert '不要求信息必须出现在初筛指定位置' in prompt
    assert 'issue_key 必须与输入相同' in prompt
    example = json.loads(prompt.split('JSON 输出示例：\n')[1].split('\n输入：\n')[0])
    assert example['issue_key'] == result.key
    assert {item['rule_id'] for item in example['decisions']} == {item.rule_id for item in result.violations}
    assert all(set(item) == {'rule_id', 'result', 'reason', 'guidance'} for item in example['decisions'])


def test_ai_phase_logs_http_failure_without_credentials_or_description(monkeypatch):
    import urllib.error
    from core.ai.client import AIChatClient
    from core.ai.core import AIClientConfig
    from core.jira.audit.rules import audit_issue
    from core.jira.audit.ai_review import review_issue
    events = []
    monkeypatch.setattr('core.jira.audit.ai_review.smart_log', lambda message, **fields: events.append(fields))
    def fail(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 401, 'secret-http-message', {}, None)
    client = AIChatClient(AIClientConfig('https://api.deepseek.com', 'deepseek-chat', 'secret-api-key'), opener=fail)
    reviewed = review_issue(audit_issue(issue(components=False)), client)
    assert reviewed.ai_review_status is AIReviewStatus.FAILED
    assert [event['extra']['stage'] for event in events] == ['request_started', 'request_finished']
    end = events[-1]['extra']
    assert end['issue_key'] == 'SH-1' and end['http_status'] == 401
    assert end['model'] == 'deepseek-chat' and end['category'] == 'transport'
    assert end['duration_ms'] >= 0
    assert 'secret-api-key' not in str(events) and 'secret-http-message' not in str(events)
    assert GOOD_DESCRIPTION not in str(events)


def issue(key='SH-1', *, description=GOOD_DESCRIPTION, summary='[ACME][T7][V1][Video]: freezes', components=True):
    return Issue(
        IssueIdentity(key, key, f'https://jira.example/browse/{key}'), summary,
        JiraProjectRef('SH'), NamedValue('1', 'Open'), NamedValue('2', 'Bug'),
        creator=PersonRef('chao.li', 'chao.li', 'Chao Li'),
        components=(NamedValue('10', 'Video'),) if components else (),
        description=DetailSection.loaded(RichText(description)),
        custom_fields=DetailSection.loaded(FieldBag.from_mapping({'Channel of Reporter': {'value': 'Customer-Feedback'}})),
    )


class Source:
    def __init__(self, issues): self.issues = tuple(issues)
    def list_issues(self, _scope, _cancellation): return self.issues
    def load_details(self, issue, _details): return issue


class AIClient:
    def __init__(self, responses): self.responses, self.prompts = iter(responses), []
    def chat_completion(self, messages, **_options):
        self.prompts.append(messages[-1].content)
        value = next(self.responses)
        if isinstance(value, BaseException): raise value
        return AIChatResponse(value, 'test')


def response(key, decisions):
    return json.dumps({'issue_key': key, 'decisions': [
        {'rule_id': rule_id, 'result': result, 'reason': reason, 'guidance': guidance}
        for rule_id, result, reason, guidance in decisions
    ]})


def run(issues, factory, *, progress=lambda *_: None):
    return JiraAuditUseCase(Source(issues), ai_client_factory=factory).run(
        JiraAuditScope('jql', 'x', 'project=SH'), progress=progress,
    )


class ConcurrentAIClient:
    def __init__(self, delays=None):
        self.delays = delays or {}
        self.lock = Lock()
        self.active = self.peak = self.calls = 0
        self.completed = []

    def chat_completion(self, messages, **_options):
        payload = json.loads(messages[-1].content.split('\n输入：\n')[1])
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        sleep(self.delays.get(payload['issue_key'], 0.05))
        with self.lock:
            self.active -= 1
            self.completed.append(payload['issue_key'])
        decisions = [
            (item['rule_id'], 'PASS', '', '') for item in payload['violations']
        ]
        return AIChatResponse(response(payload['issue_key'], decisions), 'test')


def test_every_initial_violation_and_complete_description_are_sent_for_review():
    description = '|模块|需要填写信息|测试信息|\n|平台|客户代号||'
    initial_ids = {
        'SUMMARY.FORMAT', 'COMPONENT.REQUIRED', 'DESCRIPTION.TABLE_REQUIRED_VALUE',
    }
    client = AIClient([response('SH-1', [(rule_id, 'FAIL', 'still invalid', '') for rule_id in initial_ids])])
    result = run([issue(description=description, summary='bad', components=False)], lambda: client).issues[0]
    payload = json.loads(client.prompts[0].split('\n输入：\n')[1])
    assert {item['rule_id'] for item in payload['violations']} == initial_ids
    assert payload['description'] == description
    assert {item.rule_id for item in result.violations} == initial_ids
    assert result.description == ''


def test_partial_and_all_pass_decisions_become_the_formal_report_result():
    broken = issue(description='', components=False)
    initial = run([broken], lambda: (_ for _ in ()).throw(AIConfigurationError('missing'))).issues[0]
    decisions = [(item.rule_id, 'PASS', '', '') for item in initial.violations]
    decisions[-1] = (decisions[-1][0], 'FAIL', 'AI confirmed missing', 'fill it')
    partial = run([broken], lambda: AIClient([response('SH-1', decisions)])).issues[0]
    passed = run([broken], lambda: AIClient([response('SH-1', [
        (item.rule_id, 'PASS', '', '') for item in initial.violations
    ])])).issues[0]
    assert [item.reason for item in partial.violations] == ['AI confirmed missing']
    assert partial.ai_review_status is AIReviewStatus.COMPLETED and not partial.passed
    assert passed.ai_review_status is AIReviewStatus.COMPLETED and passed.passed
    assert passed.violations == ()
    assert partial.description == passed.description == ''


@pytest.mark.parametrize(('failure', 'status', 'category'), [
    (AIConfigurationError('private'), AIReviewStatus.UNCONFIGURED, 'configuration'),
    (TimeoutError('private'), AIReviewStatus.FAILED, 'timeout'),
    (AITransportError('private'), AIReviewStatus.FAILED, 'transport'),
    (AIResponseError('private'), AIReviewStatus.FAILED, 'invalid_response'),
    (ValueError('private'), AIReviewStatus.FAILED, 'invalid_response'),
    ('{}', AIReviewStatus.FAILED, 'invalid_response'),
])
def test_ai_failure_keeps_every_initial_violation(failure, status, category):
    broken = issue(description='', summary='bad', components=False)
    result = run([broken], lambda: AIClient([failure])).issues[0]
    assert result.ai_review_status is status
    assert result.ai_failure_category == category
    assert not result.passed and len(result.violations) >= 3
    assert 'private' not in repr(result)
    assert result.description == ''


def test_valid_issue_does_not_create_ai_client():
    created = []
    result = run([issue()], lambda: created.append(True)).issues[0]
    assert result.passed and result.ai_review_status is AIReviewStatus.NOT_REQUIRED
    assert result.description == ''
    assert created == []


def test_default_factory_selects_public_deepseek_and_ai_can_clear_iptv_style_false_positive(monkeypatch):
    selected = []
    description = """h2. [Steps to reproduce]
1、启动编码
h2. [Actual results]:
概率画面被放大
h2. [Expected results]:
编码正常
h2. [Reproducibility rate]:
100%
h2. [Comparison]:
无需对比
h2. [Notes]
_HW info:中兴L3AP_
SW info：[build]
"""
    client = AIClient([response('IPTV-43289', [
        ('SUMMARY.FORMAT', 'PASS', '', ''),
        ('DESCRIPTION.ACTUAL_RESULTS', 'PASS', '', ''),
        ('DESCRIPTION.RATE_FORMAT', 'PASS', '', ''),
    ])])
    monkeypatch.setattr('core.jira.audit.ai_review.create_chat_client', lambda model: selected.append(model) or client)
    result = JiraAuditUseCase(Source([issue('IPTV-43289', description=description, summary='legacy title')])).run(
        JiraAuditScope('jql', 'x', 'project=IPTV'),
    ).issues[0]
    assert selected == ['public-deepseek']
    assert result.passed and result.violations == ()
    assert result.description == ''


def test_failed_issues_are_reviewed_with_bounded_concurrency():
    client = ConcurrentAIClient()
    report = run(
        [issue(f'SH-{index}', description='', summary='bad') for index in range(1, 9)],
        lambda: client,
    )
    assert client.calls == 8
    assert 2 <= client.peak <= 6
    assert all(result.description == '' for result in report.issues)


def test_out_of_order_ai_completion_preserves_issue_order_and_reports_completed_count():
    client = ConcurrentAIClient({'SH-1': 0.12, 'SH-2': 0.01, 'SH-3': 0.05})
    progress_events = []
    report = run(
        [issue(f'SH-{index}', description='', summary='bad') for index in range(1, 4)],
        lambda: client,
        progress=lambda *event: progress_events.append(event),
    )
    assert client.completed != ['SH-1', 'SH-2', 'SH-3']
    assert [result.key for result in report.issues] == ['SH-1', 'SH-2', 'SH-3']
    assert [event[1] for event in progress_events if event[0] == 'ai_reviewing'] == [1, 2, 3]
