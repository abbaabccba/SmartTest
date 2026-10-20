from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AIReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    COMPLETED = "completed"
    UNCONFIGURED = "unconfigured"
    FAILED = "failed"


@dataclass(frozen=True)
class AuditRule:
    rule_id: str
    section: str
    field: str
    requirement: str
    guidance: str


@dataclass(frozen=True)
class AuditViolation:
    rule_id: str
    section: str
    field: str
    observed: str
    reason: str
    guidance: str


@dataclass(frozen=True)
class IssueAuditResult:
    key: str
    url: str
    summary: str
    creator: str
    passed: bool
    violations: tuple[AuditViolation, ...]
    description: str = ""
    ai_review_status: AIReviewStatus = AIReviewStatus.NOT_REQUIRED
    ai_failure_category: str | None = None
    ai_passed_count: int = 0
    ai_failed_count: int = 0
    creator_identity: str = ""
    creator_account: str = ""


def group_creator_issues(issues):
    """Group unique runtime issues or saved issue snapshots by explicit creator identity."""
    unique = {issue['key'] if isinstance(issue, dict) else issue.key: issue for issue in issues}
    groups = {}
    for issue in unique.values():
        if isinstance(issue, dict):
            identity, account, name = issue.get('creatorIdentity'), issue.get('creatorAccount'), issue['creator']
        else:
            identity, account, name = issue.creator_identity, issue.creator_account, issue.creator
        key = ('identity', identity) if identity else ('account', account) if account else ('display', name)
        groups.setdefault(key, []).append(issue)
    return groups


@dataclass(frozen=True)
class JiraAuditScope:
    source_kind: str
    original: str
    jql: str


@dataclass(frozen=True)
class AuditReport:
    resolved: JiraAuditScope
    generated_at: datetime
    rules: tuple[AuditRule, ...]
    issues: tuple[IssueAuditResult, ...]

    @property
    def total_count(self) -> int:
        return len(self.issues)

    @property
    def passed_count(self) -> int:
        return sum(issue.passed for issue in self.issues)

    @property
    def failed_count(self) -> int:
        return self.total_count - self.passed_count

    @property
    def violation_count(self) -> int:
        return sum(len(issue.violations) for issue in self.issues)
