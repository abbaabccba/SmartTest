from __future__ import annotations

import pytest

from core.domain.detail import DetailState
from core.domain.values import NamedValue, PersonRef
from core.jira.domain import IssueDetails
from core.jira.mapper import JiraIssueMapper
from core.jira.repository import IssueRepository


ISSUE_PAYLOAD = {
    "id": "1001",
    "key": "SH-1",
    "self": "https://jira.example/rest/api/2/issue/1001",
    "fields": {
        "summary": "Lightweight issue",
        "project": {"id": "10", "key": "SH", "name": "Smart Home"},
        "status": {"id": "3", "name": "Open"},
        "issuetype": {"id": "1", "name": "Bug"},
        "priority": {"id": "2", "name": "P2"},
        "assignee": {"accountId": "a1", "name": "alice", "displayName": "Alice"},
        "reporter": {"accountId": "b1", "name": "bob", "displayName": "Bob"},
        "created": "2026-08-01T10:00:00.000+0000",
        "updated": "2026-08-02T11:00:00.000+0000",
        "labels": ["one"],
        "description": "must stay unloaded in list mapping",
        "comment": {"comments": [{"id": "c0", "body": "not requested"}]},
        "attachment": [{"id": "a0", "filename": "not-requested.txt"}],
    },
}


class RecordingJiraGateway:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def search_issues(self, query, page):
        self.calls.append(("search", query, page))
        return {"issues": [ISSUE_PAYLOAD], "startAt": 0, "maxResults": 25, "total": 1}

    def get_issue(self, issue_key):
        self.calls.append(("get", issue_key))
        return ISSUE_PAYLOAD

    def load_issue_sections(self, issue_key, sections):
        self.calls.append(("details", issue_key, sections))
        return {
            "comments": [{"id": "c1", "body": "Loaded comment", "author": {"name": "alice"}}],
        }


def test_jira_mapper_creates_lightweight_issue_with_unloaded_details() -> None:
    issue = JiraIssueMapper("https://jira.example").from_search(ISSUE_PAYLOAD)

    assert issue.identity.key == "SH-1"
    assert issue.project.key == "SH"
    assert issue.summary == "Lightweight issue"
    assert issue.comments.state is DetailState.UNLOADED
    assert issue.attachments.state is DetailState.UNLOADED
    assert issue.description.state is DetailState.UNLOADED


def test_jira_mapper_maps_creator_and_components_as_core_issue_fields() -> None:
    payload = {
        **ISSUE_PAYLOAD,
        "fields": {
            **ISSUE_PAYLOAD["fields"],
            "creator": {"name": "carol", "displayName": "Carol"},
            "components": [
                {"id": "10", "name": "Video"},
                {"id": "11", "name": "Audio"},
            ],
        },
    }

    issue = JiraIssueMapper("https://jira.example").from_search(payload)

    assert issue.creator == PersonRef("carol", "carol", "Carol")
    assert issue.components == (
        NamedValue("10", "Video"), NamedValue("11", "Audio"),
    )


def test_issue_repository_search_does_not_fetch_any_detail_section() -> None:
    gateway = RecordingJiraGateway()
    repository = IssueRepository(gateway, JiraIssueMapper("https://jira.example"))

    page = repository.search("project = SH", page=0)

    assert [issue.identity.key for issue in page.issues] == ["SH-1"]
    assert gateway.calls == [("search", "project = SH", 0)]


def test_issue_repository_loads_only_declared_section() -> None:
    gateway = RecordingJiraGateway()
    mapper = JiraIssueMapper("https://jira.example")
    repository = IssueRepository(gateway, mapper)
    issue = mapper.from_search(ISSUE_PAYLOAD)

    loaded = repository.load_details(issue, IssueDetails(comments=True))

    assert gateway.calls == [("details", "SH-1", ("comments",))]
    assert loaded.comments.state is DetailState.LOADED
    assert loaded.comments.value[0].body == "Loaded comment"
    assert loaded.attachments.state is DetailState.UNLOADED
    assert loaded.description.state is DetailState.UNLOADED


def test_bulk_comment_mapping_preserves_author_and_creation_time():
    comment = JiraIssueMapper.from_comment({
        "id": "c1", "author": {"accountId": "qa-id", "displayName": "QA"},
        "created": "2020-01-01T12:30:00.000+0800",
    })
    assert comment.author == PersonRef("qa-id", "", "QA")
    assert comment.created_at.isoformat() == "2020-01-01T12:30:00+08:00"


def test_last_verification_matches_status_text_case_insensitively_with_suffixes():
    payload = {"changelog": {"histories": [
        {"author": {"name": "latest"}, "created": "2026-09-02T00:00:00Z", "items": [
            {"field": "status", "fromString": "rEsOlVeD - Pending QA", "toString": "vErIfIeD (QA)"}]},
        {"author": {"name": "earlier"}, "created": "2026-09-01T00:00:00Z", "items": [
            {"field": "status", "fromString": "Resolved", "toString": "Verified"}]},
    ]}}
    verification = JiraIssueMapper.last_verification(payload)
    assert verification.author.account == "latest"
    assert verification.created_at.isoformat() == "2026-09-02T00:00:00+00:00"

def test_qa_source_events_keep_native_ids_raw_content_and_unassigned_qa_without_inventing_attachment_links():
    payload = {'id': 'issue', 'fields': {'comment': {'comments': [
        {'id': 'c1', 'author': {'name': ' QA ', 'key': 'qa-id'}, 'body': {'type': 'doc', 'content': ['!other.png!']}, 'created': '2020-01-01T00:00:00+0800', 'updated': '2026-10-20T01:00:00Z'},
        {'id': 'c2', 'author': {'name': 'outside'}, 'body': 'not stored'}]}, 'attachment': [
        {'id': 'a1', 'filename': 'standalone.png', 'author': {'name': 'qa'}, 'created': '2026-10-20T00:00:00+0800', 'mimeType': 'image/png', 'size': 12, 'content': 'https://jira/content/a1'},
        {'id': 'a2', 'filename': 'other.png', 'author': {'name': 'outside'}}]}}
    events = JiraIssueMapper.qa_events(payload, {'qa': ()}, metric='comment')
    assert [(event['event_kind'], event['source_id']) for event in events] == [('comment', 'c1'), ('attachment', 'a1')]
    assert events[0]['payload']['body'] == payload['fields']['comment']['comments'][0]['body']
    assert events[0]['created_at'] == '2020-01-01T00:00:00+0800'
    assert events[0]['updated_at'] == '2026-10-20T01:00:00Z'
    assert events[1]['payload']['mimeType'] == 'image/png'
    assert events[1]['payload']['filename'] == 'standalone.png'
    assert all('comment_id' not in event['payload'] for event in events)


def test_all_qa_verification_evidence_uses_the_same_predicate_but_does_not_change_last_verification():
    item = {'field': 'status', 'from': '4', 'fromString': 'rEsOlVeD - QA', 'to': '5', 'toString': 'vErIfIeD (QA)'}
    payload = {'id': 'issue', 'changelog': {'histories': [
        {'id': 'h1', 'created': '2020-01-01T00:00:00Z', 'author': {'name': 'qa'}, 'items': [item, {'field': 'priority', 'fromString': 'High', 'toString': 'Low'}]},
        {'id': 'h2', 'created': '2026-10-01T00:00:00Z', 'author': {'name': 'qa'}, 'items': [item]},
        {'id': 'h3', 'created': '2026-10-20T00:00:00Z', 'author': {'name': 'outside'}, 'items': [item]},
        {'id': 'h4', 'created': '2026-10-21T00:00:00Z', 'author': {'name': 'qa'}, 'items': [{'field': 'status', 'fromString': 'Open', 'toString': 'Verified'}]},
    ]}}
    events = JiraIssueMapper.qa_events(payload, {'qa': ('TV',)}, metric='verify')
    assert [event['source_id'] for event in events] == ['h1', 'h2']
    assert events[0]['payload']['items'] == [item]
    assert JiraIssueMapper.last_verification(payload).author.account == 'outside'


@pytest.mark.parametrize('source_id', ['', 'x' * 192])
def test_qa_evidence_requires_complete_native_ids(source_id):
    with pytest.raises(ValueError, match='jira_qa_event_id_invalid'):
        JiraIssueMapper.qa_events({'id': 'issue', 'fields': {'comment': {'comments': [
            {'id': source_id, 'author': {'name': 'qa'}, 'body': 'raw'}]}}}, {'qa': ()}, metric='comment')


@pytest.mark.parametrize('author', [{'accountId': 'qa', 'displayName': 'qa'}, {'key': 'qa'}, {'name': 'outside', 'displayName': 'qa'}])
def test_qa_evidence_never_guesses_account_from_identity_or_display_name(author):
    payload = {'id': 'issue', 'fields': {'comment': {'comments': [{'id': 'c1', 'author': author, 'body': 'raw'}]},
                                      'attachment': [{'id': 'a1', 'author': author, 'filename': 'file.png'}]}}
    assert JiraIssueMapper.qa_events(payload, {'qa': ()}, metric='comment') == ()
