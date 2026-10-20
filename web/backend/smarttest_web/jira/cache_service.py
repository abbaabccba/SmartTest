from __future__ import annotations

from core.async_tasks import TaskCancelled, check_cancellation

from core.domain.detail import DetailSection, DetailState
from core.jira.domain import Issue, IssueDetails, IssuePage

from .issue_repository import JiraIssueRepository


_ERROR_CODES = {
    "authentication_failed", "permission_denied", "not_found", "rate_limited",
    "remote_unavailable", "mapping_failed", "database_failed",
}


class JiraIssueCacheService:
    def __init__(self, gateway, mapper, repository: JiraIssueRepository):
        self._gateway = gateway
        self._mapper = mapper
        self._repository = repository

    def list_issues(self, query: str = "", page: int = 0, page_size: int = 100) -> IssuePage:
        cached = self._repository.list(query, page, page_size)
        if cached.total:
            return cached
        self.refresh_issues(query, page=page)
        return self._repository.list(query, page, page_size)

    def get_issue(self, issue_key: str, details: IssueDetails) -> Issue | None:
        issue = self._repository.get(issue_key, details)
        if issue is None:
            issue = self._mapper.from_search(self._gateway.get_issue(issue_key))
            check_cancellation()
            self._repository.save_core((issue,))
            issue = self._repository.get(issue_key, details)
        for section in details.sections():
            if getattr(issue, section).state is DetailState.UNLOADED:
                self._refresh_section(issue_key, section)
                issue = self._repository.get(issue_key, details)
        return issue

    def refresh_issues(self, scope: str, *, page: int = 0) -> dict:
        payload = self._gateway.search_issues(scope, page)
        issues, failures = [], []
        for row in payload.get("issues") or ():
            try:
                issues.append(self._mapper.from_search(row))
            except Exception:
                failures.append(str(row.get("key") or row.get("id") or "mapping_failed"))
        check_cancellation()
        self._repository.save_core(issues)
        return {
            "issues": tuple(issues),
            "failed": tuple(failures),
            "total": int(payload.get("total") or len(issues)),
            "page_size": int(
                payload.get("maxResults")
                or getattr(getattr(self._gateway, "config", None), "page_size", 100)
            ),
        }

    def refresh_release_issues(self, scope: str, *, page: int = 0) -> dict:
        payload = self._gateway.search_release_issues(scope, page)
        metadata = payload.get("fieldMetadata") or {}
        issues, rows, failures = [], [], []
        for row in payload.get("issues") or ():
            try:
                issues.append(self._mapper.from_search(row))
                rows.append(row)
            except Exception:
                failures.append(str(row.get("key") or row.get("id") or "mapping_failed"))
        check_cancellation()
        self._repository.save_core(issues)
        for issue, row in zip(issues, rows):
            fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
            self._repository.replace_release_fields(issue.identity.key, fields, metadata)
        return {
            "issues": tuple(issues), "failed": tuple(failures),
            "total": int(payload.get("total") or len(issues)),
            "page_size": int(payload.get("maxResults") or payload.get("page_size")
                             or getattr(getattr(self._gateway, "config", None), "page_size", 100)),
        }

    def refresh_issue(self, issue_key: str, details: IssueDetails) -> Issue:
        core = self._mapper.from_search(self._gateway.get_issue(issue_key))
        check_cancellation()
        self._repository.save_core((core,))
        for section in details.sections():
            self._refresh_section(issue_key, section)
        return self._repository.get(issue_key, details)

    def refresh_sections(self, issue_key: str, details: IssueDetails) -> Issue:
        for section in details.sections():
            self._refresh_section(issue_key, section)
        return self._repository.get(issue_key, details)

    def invalidate_issue(self, issue_key: str) -> None:
        self._repository.delete(issue_key)

    def clear(self) -> None:
        self._repository.clear()

    def _refresh_section(self, issue_key: str, name: str) -> None:
        details = IssueDetails(**{name: True})
        current = self._repository.get(issue_key, details)
        try:
            payload = self._gateway.load_issue_sections(issue_key, (name,))
            refreshed = self._mapper.with_sections(current, payload, (name,))
            section = getattr(refreshed, name)
        except TaskCancelled:
            raise
        except Exception as error:
            previous = getattr(current, name)
            section = DetailSection.failed(
                _error_code(error), value=previous.value,
                source_revision=previous.source_revision,
            )
        check_cancellation()
        getattr(self._repository, f"replace_{name}")(issue_key, section)


def _error_code(error: Exception) -> str:
    code = str(getattr(error, "code", "") or "")
    return code if code in _ERROR_CODES else "remote_unavailable"
