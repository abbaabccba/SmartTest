from __future__ import annotations

from core.async_tasks import TaskCancelled

from concurrent.futures import as_completed
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4
from time import perf_counter

from core.confluence.project import Project, ProjectDetails
from core.logging import smart_log

from .models import (
    AuditBatch,
    AuditFinding,
    AuditPeriod,
    AuditStatus,
    ConfluencePageDocument,
    MISSING_QA,
    ProjectAudit,
)
from .regions import extract_page_region
from .rules import ROLE_LABELS, UPDATE_MATRIX_POINTS


class ConfluenceAuditProjectSource(Protocol):
    def load_project_details(
        self, project_id: str, details: ProjectDetails, cancellation,
    ) -> Project: ...
    def load_current_page(self, page_id: str, cancellation) -> ConfluencePageDocument: ...
    def load_page_versions(
        self, page_id: str, period: AuditPeriod,
        current: ConfluencePageDocument, cancellation,
    ) -> tuple[ConfluencePageDocument, ...]: ...


class _NoCancellation:
    def raise_if_cancelled(self) -> None:
        return None


class _CombinedCancellation:
    def __init__(self, *tokens): self._tokens = tokens

    def raise_if_cancelled(self) -> None:
        for token in self._tokens:
            token.raise_if_cancelled()


class ConfluenceWeeklyAuditUseCase:
    def __init__(self, source: ConfluenceAuditProjectSource):
        self._source = source

    def run(
        self,
        projects: tuple[Project, ...],
        period: AuditPeriod,
        *,
        cancellation=None,
        progress=lambda *_: None,
        task_manager=None,
        parent_task_id: str = "",
    ) -> AuditBatch:
        token = cancellation or _NoCancellation()
        audits = self._audit_projects(
            projects, period, token, progress,
            task_manager=task_manager, parent_task_id=parent_task_id,
        )
        progress("finalizing", len(audits), len(audits))
        return AuditBatch(
            uuid4().hex, period, datetime.now(timezone.utc), tuple(audits),
        )

    def _audit_projects(self, projects, period, token, progress, *, task_manager, parent_task_id):
        if task_manager is None or not parent_task_id:
            return [self._audit_one(project, period, token, progress) for project in projects]
        futures = {
            task_manager.submit_child(
                parent_task_id, "confluence-review-project",
                lambda child_token, _child_progress, project=project: self._audit_one(
                    project, period, _CombinedCancellation(token, child_token), lambda *_: None,
                ),
            ): index
            for index, project in enumerate(projects)
        }
        audits = [None] * len(projects)
        completed = 0
        for future in as_completed(futures):
            token.raise_if_cancelled()
            audits[futures[future]] = future.result()
            completed += 1
            progress("loading_details", completed, len(projects))
        return audits

    def _audit_one(self, project, period, token, progress):
        started = perf_counter()
        token.raise_if_cancelled()
        try:
            loaded = self._source.load_project_details(
                project.identity.project_id, ProjectDetails(roles=True, evidence=True), token,
            )
            result = self._audit_project(loaded, period, token, progress)
        except TaskCancelled:
            raise
        except Exception as error:
            token.raise_if_cancelled()
            reason = "remote_unavailable" if str(error) == "remote_unavailable" else "audit_failed"
            result = ProjectAudit(project, (AuditFinding(
                project.identity.project_id, project.name, "project.audit",
                AuditStatus.FAILED, reason, project.catalog_page.url,
            ),), _owners(project))
        smart_log("Confluence review project timing", domain="framework", source="confluence_review", emit_runtime_event=False,
                  extra={"stage": "review.project_total", "duration_ms": round((perf_counter() - started) * 1000, 3),
                         "project_id": project.identity.confluence_id, "finding_count": len(result.findings)})
        return result

    def _audit_project(self, project, period, token, progress):
        rules_started = perf_counter()
        evidence = {
            item.source: item.page for item in (project.evidence.value or ())
        }
        findings = []
        page_material = {}
        for index, point in enumerate(UPDATE_MATRIX_POINTS, 1):
            token.raise_if_cancelled()
            progress("loading_versions", index - 1, len(UPDATE_MATRIX_POINTS))
            page = evidence.get(point.source_page)
            if page is None:
                findings.append(AuditFinding(
                    project.identity.project_id, point.standard_name,
                    point.rule_id, AuditStatus.INVALID_FORMAT,
                    f"格式有误：查询不到{point.standard_name}",
                    project.catalog_page.url,
                ))
                continue
            if page.page_id not in page_material:
                try:
                    current = self._source.load_current_page(page.page_id, token)
                    token.raise_if_cancelled()
                    versions = self._source.load_page_versions(
                        page.page_id, period, current, token,
                    )
                    token.raise_if_cancelled()
                    page_material[page.page_id] = (current, versions or (current,))
                except TaskCancelled:
                    raise
                except Exception:
                    token.raise_if_cancelled()
                    page_material[page.page_id] = None
            material = page_material[page.page_id]
            if material is None:
                findings.append(AuditFinding(
                    project.identity.project_id, page.title, point.rule_id,
                    AuditStatus.FAILED, "remote_unavailable", page.url,
                ))
            else:
                findings.append(_audit_versions(
                    project, point, material[0], material[1], period,
                ))
        result = ProjectAudit(project, tuple(findings), _owners(project))
        smart_log("Confluence review rules timing", domain="framework", source="confluence_review", emit_runtime_event=False,
                  extra={"stage": "review.rules", "duration_ms": round((perf_counter() - rules_started) * 1000, 3),
                         "project_id": project.identity.confluence_id, "finding_count": len(findings),
                         "unique_page_count": len(page_material)})
        return result


def _audit_versions(project, point, current, versions, period):
    extracted = tuple(extract_page_region(item, point) for item in versions)
    current_region = extract_page_region(current, point)
    if not current_region.found:
        return AuditFinding(
            project.identity.project_id, current.title, point.rule_id,
            AuditStatus.INVALID_FORMAT,
            f"格式有误：查询不到{point.standard_name}", current.url,
        )
    changed = False
    if len(versions) == 1:
        changed = versions[0].version == 1 and period.contains(versions[0].updated_at)
    for version, older, newer in zip(versions[1:], extracted, extracted[1:]):
        if period.contains(version.updated_at) and (
            older.found, older.content
        ) != (
            newer.found, newer.content
        ):
            changed = True
    return AuditFinding(
        project.identity.project_id, current.title, point.rule_id,
        AuditStatus.UPDATED if changed else AuditStatus.NOT_UPDATED,
        "Region updated in audit period." if changed else "Region not updated in audit period.",
        current.url,
    )


def _owners(project: Project) -> tuple[str, ...]:
    values = []
    for role in project.roles.value or ():
        if role.role.name not in ROLE_LABELS:
            continue
        for person in role.people:
            name = person.display_name or person.account or person.identity
            if name and name not in values:
                values.append(name)
    return tuple(values) or (MISSING_QA,)
