from __future__ import annotations
from sqlalchemy import text

from core.domain.detail import DetailState
from core.jira.domain import IssueDetails
from core.jira.mapper import JiraIssueMapper
from smarttest_web.jira.cache_service import JiraIssueCacheService
from smarttest_web.jira.issue_repository import JiraIssueRepository


def _payload(revision="2026-08-01T00:00:00+00:00"):
    return {
        "id": "100", "key": "SH-100", "self": "https://jira/SH-100",
        "fields": {
            "summary": "Cached issue", "project": {"id": "10", "key": "SH", "name": "Smart Home"},
            "status": {"id": "1", "name": "Open"}, "issuetype": {"id": "2", "name": "Bug"},
            "priority": None, "assignee": None, "reporter": None,
            "created": revision, "updated": revision, "labels": ["cache"],
        },
    }


class JiraGateway:
    def __init__(self):
        self.revision = "2026-08-01T00:00:00+00:00"
        self.core_calls = []
        self.detail_calls = []
        self.fail_sections = set()

    def search_issues(self, query, page=0):
        return {"issues": [_payload(self.revision)], "page": page, "page_size": 100, "total": 1}

    def search_release_issues(self, query, page=0):
        payload = _payload(self.revision)
        payload["fields"].update({
            "customfield_101": "P100", "customfield_102": {"value": "Android 16"},
            "fixVersions": [{"id": "v1", "name": "Android 16", "released": False}],
        })
        return {
            "issues": [payload], "page": page, "page_size": 100, "total": 1,
            "fieldMetadata": {
                "customfield_101": "Project ID", "customfield_102": "Software Release",
                "customfield_103": "Severity", "customfield_104": "Compare Status",
                "customfield_105": "QA Assignee", "customfield_106": "Manager",
            },
        }

    def get_issue(self, issue_key):
        self.core_calls.append(issue_key)
        return _payload(self.revision)

    def load_issue_sections(self, issue_key, sections):
        self.detail_calls.append((issue_key, sections))
        if sections[0] in self.fail_sections:
            raise RuntimeError("offline")
        return {
            "description": {"text": "description"},
            "comments": [{"id": "c1", "body": {"text": "comment"}}],
            "attachments": [], "links": [], "custom_fields": {},
        }


def _service(tmp_path, *, database_factory):
    gateway = JiraGateway()
    repository = JiraIssueRepository(database_factory())
    return JiraIssueCacheService(gateway, JiraIssueMapper(), repository), gateway, repository


def test_jira_list_cache_miss_fetches_only_core_fields(
    tmp_path, *, database_factory
) -> None:
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )

    page = service.list_issues("project=SH", 0, 100)

    assert [issue.identity.key for issue in page.issues] == ["SH-100"]
    assert page.issues[0].comments.state is DetailState.UNLOADED
    assert gateway.detail_calls == []


def test_jira_get_loads_only_the_requested_section(
    tmp_path, *, database_factory
) -> None:
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )
    service.list_issues("project=SH", 0, 100)

    issue = service.get_issue("SH-100", IssueDetails(comments=True))

    assert issue.comments.state is DetailState.LOADED
    assert issue.description.state is DetailState.UNLOADED
    assert gateway.detail_calls == [("SH-100", ("comments",))]


def test_jira_revision_change_marks_only_loaded_details_stale(
    tmp_path, *, database_factory
) -> None:
    service, gateway, repository = _service(tmp_path, database_factory=database_factory)
    service.list_issues("project=SH", 0, 100)
    service.get_issue("SH-100", IssueDetails(comments=True))
    gateway.revision = "2026-08-02T00:00:00+00:00"

    service.refresh_issues("project=SH")

    issue = repository.get("SH-100", IssueDetails(comments=True, attachments=True))
    assert issue.comments.state is DetailState.STALE
    assert issue.attachments.state is DetailState.UNLOADED


def test_jira_refresh_sections_does_not_refetch_fresh_core_fields(
    tmp_path, *, database_factory
) -> None:
    service, gateway, repository = _service(tmp_path, database_factory=database_factory)
    service.list_issues("project=SH", 0, 100)
    service.get_issue("SH-100", IssueDetails(description=True))
    gateway.revision = "2026-08-02T00:00:00+00:00"
    service.refresh_issues("project=SH")
    assert repository.get("SH-100", IssueDetails(description=True)).description.state is DetailState.STALE

    issue = service.refresh_sections("SH-100", IssueDetails(description=True))

    assert issue.description.state is DetailState.LOADED
    assert gateway.core_calls == []
    assert gateway.detail_calls == [
        ("SH-100", ("description",)),
        ("SH-100", ("description",)),
    ]


def test_jira_remote_detail_failure_preserves_old_value_and_other_sections(
    tmp_path, *, database_factory
) -> None:
    service, gateway, repository = _service(tmp_path, database_factory=database_factory)
    service.list_issues("project=SH", 0, 100)
    service.get_issue("SH-100", IssueDetails(description=True, comments=True))
    gateway.fail_sections.add("comments")

    issue = service.refresh_issue("SH-100", IssueDetails(comments=True))

    assert issue.comments.state is DetailState.FAILED
    assert issue.comments.error_code == "remote_unavailable"
    assert issue.comments.value[0].id == "c1"
    cached = repository.get("SH-100", IssueDetails(description=True))
    assert cached.description.state is DetailState.LOADED


def test_jira_remote_detail_failure_without_old_value_records_failed_state(
    tmp_path, *, database_factory
) -> None:
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )
    service.list_issues("project=SH", 0, 100)
    gateway.fail_sections.add("attachments")

    issue = service.get_issue("SH-100", IssueDetails(attachments=True))

    assert issue.attachments.state is DetailState.FAILED
    assert issue.attachments.value is None
    assert issue.attachments.error_code == "remote_unavailable"


def test_release_refresh_saves_core_and_stable_release_projection(
    tmp_path, *, database_factory
) -> None:
    service, _gateway, repository = _service(
        tmp_path, database_factory=database_factory
    )

    result = service.refresh_release_issues('"Project ID" = "P100"')

    assert result["total"] == 1
    with repository.database.connect() as connection:
        assert connection.execute(
            text(
                "SELECT project_business_id,software_release FROM jira_issue_release_facts"
            )
        ).fetchone() == ("P100", "Android 16")
        assert (
            connection.execute(
                text("SELECT version_name FROM jira_issue_fix_versions")
            ).fetchone()[0]
            == "Android 16"
        )
