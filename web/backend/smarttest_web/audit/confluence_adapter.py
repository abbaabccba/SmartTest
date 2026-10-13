from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from time import perf_counter

from core.confluence.audit import (
    AuditPeriod,
    ConfluencePageDocument,
    ConfluenceWeeklyAuditUseCase,
    export_audit_xlsx_by_product_line,
)
from core.confluence.project import (
    ProjectDetails,
    ProjectQuery,
)
from core.domain.detail import DetailState
from core.logging import smart_log
from core.weekly_audit import weekly_audit_project_in_scope
from core.product_lines import PRODUCT_LINES

from ..task_manager import WEB_TASKS
from ..project_facts_api import ProjectFactsWebOwner
from ..resource_access import remote_status


@dataclass(frozen=True)
class ResolvedConfluenceAudit:
    projects: tuple
    period: AuditPeriod


class WebConfluenceAuditOwner:
    def __init__(self, cache_service, repository, page_gateway, *, access):
        self._cache = cache_service
        self._repository = repository
        self._gateway = page_gateway
        self._access = access

    @classmethod
    def from_credentials(cls, access, password: str):
        facts = ProjectFactsWebOwner()
        return cls(*facts.audit_dependencies(access, password), access=access)

    def resolve(self, payload: dict) -> ResolvedConfluenceAudit:
        try:
            period = AuditPeriod(_period_bound(payload.get("startDate")), _period_bound(payload.get("endDate")))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_input") from error
        project_ids = tuple(
            dict.fromkeys(str(item) for item in payload.get("projectIds") or ())
        )
        if project_ids:
            for project_id in project_ids:
                self._access.require("project", project_id, "catalog")
            projects = tuple(
                project for project_id in project_ids
                if (project := self._repository.get(project_id, ProjectDetails()))
                is not None
            )
        else:
            filters = dict(payload.get("filters") or {})
            commercial_years = set(filters.pop("date of commercial approval", ()))
            projects = self._repository.list(
                ProjectQuery.from_filters(filters),
                0, 10000,
                visible_ids=self._access.ids("project", "catalog"),
            ).projects
            projects = self._repository.load_many(projects, ProjectDetails(facts=True))
            projects = tuple(project for project in projects if weekly_audit_project_in_scope(
                project, commercial_years, payload.get("excludeCurrentStageAtOrAbove"),
                payload.get("excludeSupportModeBProductLines") or (),
            ))
            projects += tuple(
                self._cache.resolve_project_url(url, PRODUCT_LINES[0])
                for url in payload.get("manualProjectUrls") or ()
            )
        if not projects:
            raise ValueError("invalid_input")
        return ResolvedConfluenceAudit(projects, period)

    def run(self, resolved, cancellation, progress):
        started = perf_counter()
        cancellation.raise_if_cancelled()
        progress("refreshing_projects", 0, len(resolved.projects))
        result = ConfluenceWeeklyAuditUseCase(self).run(
            resolved.projects, resolved.period,
            cancellation=cancellation, progress=progress,
            task_manager=WEB_TASKS, parent_task_id=getattr(cancellation, "task_id", ""),
        )
        _timing("review.total", started, project_count=len(resolved.projects), finding_count=sum(len(item.findings) for item in result.projects))
        return result

    def load_project_details(self, project_id, details, cancellation):
        started = perf_counter()
        cancellation.raise_if_cancelled()
        try:
            project = self._cache.refresh_project(
                project_id, details, cancellation=cancellation,
            )
        except Exception as error:
            cancellation.raise_if_cancelled()
            raise RuntimeError("remote_unavailable") from error
        cancellation.raise_if_cancelled()
        if project is None:
            raise RuntimeError("not_found")
        if any(
            getattr(project, name).state is DetailState.FAILED
            for name in details.sections()
        ):
            raise RuntimeError("remote_unavailable")
        _timing("review.project.details", started, project_id=str(project_id), section_count=len(details.sections()))
        return project

    def load_current_page(self, page_id, cancellation):
        started = perf_counter()
        cancellation.raise_if_cancelled()
        self._access.require("page", page_id, "metadata")
        page = self._read_page(page_id)
        cancellation.raise_if_cancelled()
        document = _document(page)
        _timing("review.page.current", started, page_id=str(page_id), version=document.version)
        return document

    def load_page_versions(self, page_id, period, current, cancellation):
        started = perf_counter()
        cancellation.raise_if_cancelled()
        pages = [current]
        if current.updated_at is None:
            raise RuntimeError("remote_unavailable")
        if current.updated_at >= period.start:
            for version in range(current.version - 1, 0, -1):
                cancellation.raise_if_cancelled()
                page = self._read_page(page_id, version)
                cancellation.raise_if_cancelled()
                pages.append(_document(page))
                if page.updated_at is None or page.updated_at < period.start:
                    break
        result = tuple(reversed(pages))
        _timing("review.page.versions", started, page_id=str(page_id), version_count=len(result))
        return result

    def _read_page(self, page_id, version=None):
        self._access.require_active()
        try:
            page = (self._gateway.get_page(page_id) if version is None
                    else self._gateway.get_page_version(page_id, version))
        except Exception as error:
            self._access.require_active()
            if remote_status(error) == 403:
                self._access.revoke("page", page_id)
            if remote_status(error) == 401:
                raise PermissionError("reauthentication_required") from error
            raise
        if str(page.id) != str(page_id):
            raise PermissionError("permission_denied")
        self._access.publish((("page", page_id, "body", scope)
            for scope in self._access.scopes("page", page_id, "metadata")), lambda: None)
        return page

    @staticmethod
    def export(batch, output_dir: Path):
        return export_audit_xlsx_by_product_line(batch, output_dir)


def _document(page):
    return ConfluencePageDocument(
        page.id, page.title, page.url, page.body, page.view_body,
        page.version, page.updated_at,
    )


def _timing(stage, started, **extra):
    smart_log("Confluence review timing", platform="web", domain="framework", source="confluence_review", emit_runtime_event=False,
              extra={"stage": stage, "duration_ms": round((perf_counter() - started) * 1000, 3), **extra})


def _period_bound(value):
    parsed = datetime.fromisoformat(str(value or ""))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed
