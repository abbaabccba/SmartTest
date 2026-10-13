from __future__ import annotations

from concurrent.futures import as_completed
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from core.jira.domain import Issue, IssueDetails

from .models import AuditReport, JiraAuditScope
from .rules import active_rules, audit_issue, is_audit_eligible
from .ai_review import review_failed_issues


class JiraAuditIssueSource(Protocol):
    def list_issues(self, scope: JiraAuditScope, cancellation) -> tuple[Issue, ...]: ...
    def load_details(self, issue: Issue, details: IssueDetails) -> Issue: ...


class _NoCancellation:
    def raise_if_cancelled(self) -> None:
        return None


class _CombinedCancellation:
    def __init__(self, *tokens): self._tokens = tokens

    def raise_if_cancelled(self) -> None:
        for token in self._tokens:
            token.raise_if_cancelled()


class JiraAuditUseCase:
    def __init__(self, source: JiraAuditIssueSource, *, ai_client_factory=None):
        self._source = source
        self._ai_client_factory = ai_client_factory

    def run(
        self,
        scope: JiraAuditScope,
        *,
        cancellation=None,
        progress=lambda *_: None,
        task_manager=None,
        parent_task_id: str = "",
    ) -> AuditReport:
        token = cancellation or _NoCancellation()
        token.raise_if_cancelled()
        progress("fetching_issues", 0, 0)
        issues = self._source.list_issues(scope, token)
        token.raise_if_cancelled()
        eligible = [issue for issue in issues if is_audit_eligible(issue)]
        results = self._audit_issues(
            eligible, token, progress,
            task_manager=task_manager, parent_task_id=parent_task_id,
        )
        results = review_failed_issues(
            results, client_factory=self._ai_client_factory,
            cancellation=token, progress=progress,
        )
        results = [replace(result, description="") for result in results]
        progress("finalizing", len(results), len(results))
        return AuditReport(
            scope, datetime.now(timezone.utc), active_rules(), tuple(results),
        )

    def _audit_issues(self, issues, token, progress, *, task_manager, parent_task_id):
        if task_manager is None or not parent_task_id:
            results = []
            for index, issue in enumerate(issues, 1):
                token.raise_if_cancelled()
                progress("loading_details", index - 1, len(issues))
                results.append(self._audit_one(issue, token))
                progress("rule_auditing", index - 1, len(issues))
            return results
        futures = {
            task_manager.submit_child(
                parent_task_id, "jira-review-issue",
                lambda child_token, _child_progress, issue=issue: self._audit_one(
                    issue, _CombinedCancellation(token, child_token),
                ),
            ): index
            for index, issue in enumerate(issues)
        }
        results = [None] * len(issues)
        completed = 0
        for future in as_completed(futures):
            token.raise_if_cancelled()
            results[futures[future]] = future.result()
            completed += 1
            progress("loading_details", completed, len(issues))
        return results

    def _audit_one(self, issue, token):
        token.raise_if_cancelled()
        loaded = self._source.load_details(issue, IssueDetails(description=True, custom_fields=True))
        token.raise_if_cancelled()
        return audit_issue(loaded)
