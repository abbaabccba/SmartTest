from __future__ import annotations

from core.jira.jql import jira_filter_jql

import os
from pathlib import Path

from core.domain.detail import DetailState
from core.jira.audit import (
    JiraAuditUseCase,
    export_audit_xlsx,
    resolve_audit_input,
)
from core.jira.domain import Issue, IssueDetails
from core.jira.gateway import JiraGateway
from core.jira.mapper import JiraIssueMapper
from core.logging import smart_log

from ..task_manager import WEB_TASKS
from ..database import WebDatabase
from ..jira.cache_service import JiraIssueCacheService
from ..jira.issue_repository import JiraIssueRepository
from ..session import default_web_database_path


class WebJiraAuditOwner:
    def __init__(self, gateway, cache_service: JiraIssueCacheService):
        self._gateway = gateway
        self._cache = cache_service

    @classmethod
    def from_credentials(cls, username: str, password: str):
        base_url = os.getenv("SMARTTEST_JIRA_BASE_URL", "https://jira.amlogic.com")
        gateway = JiraGateway(base_url, username, password)
        cache = JiraIssueCacheService(
            gateway, JiraIssueMapper(base_url),
            JiraIssueRepository(WebDatabase(default_web_database_path())),
        )
        return cls(gateway, cache)

    def resolve(self, text):
        return resolve_audit_input(
            text,
            base_url=self._gateway.config.base_url,
            fetch_filter=self._gateway.fetch_filter,
            validate_jql=lambda jql: self._gateway.search_payload(
                jql, start_at=0, max_results=1,
            ),
        )

    def resolve_filter(self, snapshot):
        return self.resolve(jira_filter_jql(snapshot.filters, snapshot.jql))

    def run(self, scope, cancellation, progress):
        return JiraAuditUseCase(self).run(
            scope, cancellation=cancellation, progress=progress,
            task_manager=WEB_TASKS, parent_task_id=getattr(cancellation, "task_id", ""),
        )

    def list_issues(self, scope, cancellation) -> tuple[Issue, ...]:
        issues = []
        page = 0
        while True:
            cancellation.raise_if_cancelled()
            result = self._cache.refresh_issues(scope.jql, page=page)
            cancellation.raise_if_cancelled()
            if result["failed"]:
                raise RuntimeError("mapping_failed")
            issues.extend(result["issues"])
            if not result["issues"] or len(issues) >= result["total"]:
                return tuple(issues)
            page += 1

    def load_details(self, issue: Issue, details: IssueDetails) -> Issue:
        try:
            loaded = self._cache.get_issue(issue.identity.key, details)
            if loaded is None:
                raise RuntimeError("not_found")
            section_states = {
                name: getattr(loaded, name).state.value
                for name in details.sections()
            }
            refresh_required = any(
                state in {DetailState.STALE.value, DetailState.FAILED.value}
                for state in section_states.values()
            )
            smart_log(
                "Jira audit issue detail decision",
                platform="web",
                domain="audit",
                source="jira_review",
                emit_runtime_event=False,
                extra={
                    "stage": "load_details",
                    "issue_key": issue.identity.key,
                    "sections": list(details.sections()),
                    "section_states": section_states,
                    "action": "refresh_sections" if refresh_required else "use_cached",
                    "refreshes_core": False,
                },
            )
            if refresh_required:
                loaded = self._cache.refresh_sections(issue.identity.key, details)
            if any(
                getattr(loaded, name).state is DetailState.FAILED
                for name in details.sections()
            ):
                raise RuntimeError("remote_unavailable")
            return loaded
        except Exception as error:
            cause = error.__cause__
            response = getattr(cause, "response", None)
            smart_log(
                "Jira audit issue detail failed",
                platform="web",
                domain="audit",
                source="jira_review",
                level="ERROR",
                emit_runtime_event=False,
                extra={
                    "stage": "load_details",
                    "issue_key": issue.identity.key,
                    "sections": list(details.sections()),
                    "error_code": str(getattr(error, "code", "") or type(error).__name__),
                    "cause_type": type(cause).__name__ if cause is not None else "",
                    "http_status": (
                        getattr(cause, "status_code", None)
                        or getattr(response, "status_code", None)
                    ),
                },
            )
            raise

    @staticmethod
    def export(report, output_path: Path):
        return export_audit_xlsx(report, output_path=output_path)
