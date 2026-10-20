from __future__ import annotations

from copy import deepcopy

from .task_manager import WEB_TASKS

import os
import re
from threading import Lock
from time import perf_counter

from core.confluence import ConfluenceGateway, ConfluenceGatewayConfig
from core.confluence.project import (
    ProjectDetails,
    ProjectQuery,
    ProjectSyncScope,
)
from core.domain.detail import DetailState
from core.confluence.project_catalog import (
    PRODUCT_SPACE_FACET,
    PROJECT_SPACE_FACET_DEFINITIONS,
    extract_project_detail,
    is_wireless_module,
    query_project_facts,
    refresh_project_catalogs,
)
from core.confluence.project_rules import ROLE_LABELS, WIFI_ROLE_LABELS
from core.confluence.project_discovery import ProjectLocation, canonical_project_name, locate_basic_information
from core.confluence.html import table_fields
from core.product_lines import DASHBOARD_PRODUCT_LINES, PRODUCT_LINES, WIRELESS_CONNECTION
from core.confluence.project_mapper import ConfluenceProjectMapper
from core.logging import smart_log

from .confluence.cache_service import ConfluenceProjectCacheService
from .confluence.project_repository import ConfluenceProjectRepository
from .confluence_sync import ConfluenceProjectSyncCoordinator
from .query_snapshot_repository import PROJECT_FILTER_KEYS

PAGE_CATALOG_PRODUCT_SPACES = tuple(line.confluence_space_key for line in PRODUCT_LINES)
PROJECT_STATUS_PATTERN = re.compile(r"^\s*(NORMAL|WARNING|BLOCK)\b", re.IGNORECASE)


def _project_status(value):
    matched = PROJECT_STATUS_PATTERN.match(str(value or ""))
    return matched.group(1).upper() if matched else None


PROJECT_FILTER_FACET_DEFINITIONS = tuple(
    definition for definition in PROJECT_SPACE_FACET_DEFINITIONS
    if definition[0] in PROJECT_FILTER_KEYS
)


class _QueryAccessSnapshot:
    """Stable authorization view for one cached query while catalog grants update."""

    _keys = (
        ("project", "catalog"), ("project", "roles"), ("project", "evidence"),
        ("catalog", "ready"),
        ("page", "metadata"),
    )

    def __init__(self, access):
        access.require_active()
        self._ids = {key: frozenset(access.ids(*key)) for key in self._keys}

    def ids(self, kind, capability):
        return self._ids.get((kind, capability), frozenset())

    def allows(self, kind, resource_id, capability):
        return str(resource_id) in self.ids(kind, capability)

    def require(self, kind, resource_id, capability):
        if not self.allows(kind, resource_id, capability):
            raise PermissionError("permission_denied")


class ProjectFactsWebOwner:
    """Web presentation owner over the Confluence current-state cache."""

    def __init__(
        self,
        *,
        repository: ConfluenceProjectRepository,
        client_factory=None,
        sync_coordinator_factory=ConfluenceProjectSyncCoordinator,
    ):
        self._repository = repository
        self._client_factory = client_factory or self._make_client
        self._sync_coordinator_factory = sync_coordinator_factory
        self._refresh_lock = Lock()

    @staticmethod
    def _make_client(username, password):
        base_url = os.getenv("SMARTTEST_CONFLUENCE_BASE_URL", "https://confluence.amlogic.com")
        return ConfluenceGateway(ConfluenceGatewayConfig(base_url), username, password)

    def refresh(self, access, password, *, product_spaces=PAGE_CATALOG_PRODUCT_SPACES, scheduled=False):
        from .daily_refresh import manual_source_allowed
        if not scheduled and not manual_source_allowed('confluence'):
            raise PermissionError('manual_refresh_disabled')
        with self._refresh_lock:
            started = perf_counter()
            service = self._service(access, password)
            result = service.refresh_projects(
                ProjectSyncScope(product_space_keys=product_spaces),
            )
            smart_log("Confluence filter preparation timing", platform="web", domain="framework", source="project_facts_owner", emit_runtime_event=False,
                      extra={"stage": "filter.catalog_refresh", "duration_ms": round((perf_counter() - started) * 1000, 3),
                             "product_space_count": len(PAGE_CATALOG_PRODUCT_SPACES), "project_count": len(result.get("projects") or ()),
                             "failure_count": len(result.get("failed") or ())})
            if result['failed']:
                raise RuntimeError('remote_unavailable')
        return None

    def sync_details(
        self, access, password, *, filters=None, search="", cancelled=None, progress=None,
        parent_task_id="", force=False,
    ):
        del search
        project_ids = self._acquisition_scope(access, filters, force=force)
        progress = progress or (lambda *_: None)
        progress(0, len(project_ids))
        if not project_ids:
            return
        service = self._service(access, password)
        coordinator = self._sync_coordinator_factory(service)
        results = coordinator.sync(
            project_ids,
            ProjectDetails(roles=True, facts=True, evidence=True),
            cancelled=cancelled or (lambda: False),
            progress=progress,
            parent_id=parent_task_id,
            **({'require_complete': True} if force else {}),
        )
        if force and any(result != 'updated' for result in results):
            raise RuntimeError('confluence_details_failed')

    def _acquisition_scope(self, access, filters, *, force=False):
        visible_ids = access.ids("project", "catalog")
        catalog = self._repository.list(ProjectQuery(), 0, 100000, visible_ids=visible_ids)
        projects = catalog.projects
        requested_spaces = tuple((filters or {}).get(PRODUCT_SPACE_FACET) or ())
        known_spaces = {project.product_space.key for project in projects}
        if requested_spaces and set(requested_spaces) <= known_spaces:
            projects = tuple(
                project for project in projects if project.product_space.key in requested_spaces
            )
        loaded = self._repository.load_many(
            projects, ProjectDetails(roles=True, facts=True),
        )
        return tuple(
            project.identity.confluence_id
            for project in loaded
            if force or _detail_state((project,)) != "ready"
        )

    def refresh_and_sync_details(
        self, access, password, *, filters=None, search="", cancelled=None, progress=None,
        parent_task_id="",
    ):
        self.refresh(access, password)
        self.sync_details(
            access, password, filters=filters, search=search,
            cancelled=cancelled, progress=progress,
            parent_task_id=parent_task_id,
        )

    def query(self, access, *, filters=None, fixed_filters=None, search="", page=0, page_size=10000):
        started = perf_counter()
        query_access = _QueryAccessSnapshot(access)
        ready_product_spaces = query_access.ids("catalog", "ready")
        cached = self._repository.list(
            ProjectQuery(), 0, 100000,
            visible_ids=query_access.ids("project", "catalog"),
        )
        if cached.total == 0:
            state = "ready" if set(PAGE_CATALOG_PRODUCT_SPACES) <= ready_product_spaces else "no_snapshot"
            self._log_query_timing(started, state, cached.total, query_access, ready_product_spaces, 0)
            return self._state(state, ready_product_spaces)
        projects = self._repository.load_many(
            cached.projects, ProjectDetails(roles=True, facts=True),
        )
        snapshot = {"projects": [
            row
            for project in projects if project
            for row in _project_snapshot_rows(project)
        ]}
        filter_product_spaces = set(ready_product_spaces)
        if any(row["space_key"] == WIRELESS_CONNECTION.confluence_space_key for row in snapshot["projects"]):
            filter_product_spaces.add(WIRELESS_CONNECTION.confluence_space_key)
        result = query_project_facts(snapshot, filters=filters, fixed_filters=fixed_filters, search=search)
        detail_state = _detail_state(projects)
        semantic_statuses = tuple(_project_status(project.get("status")) for project in result["projects"])
        block_count = semantic_statuses.count("BLOCK")
        warning_count = semantic_statuses.count("WARNING")
        start = int(page) * int(page_size)
        visible = result["projects"]
        self._log_query_timing(started, "ready", cached.total, query_access, ready_product_spaces, len(visible))
        return {
            "state": "ready",
            "detailState": detail_state,
            "accessibleProjectCount": cached.total,
            "blockProjectCount": block_count,
            "warningProjectCount": warning_count,
            "productSpaces": _product_space_rows(),
            "facets": _facet_rows(result["facets"], filter_product_spaces),
            "projects": visible[start:start + int(page_size)],
            "pagination": {
                "page": int(page),
                "pageSize": int(page_size),
                "total": len(visible),
            },
            "ownerHierarchy": result["ownerHierarchy"],
            "discrepancies": [],
            "counts": {"stale": 0, "failed": 0, "inactive": 0},
        }

    @staticmethod
    def _log_query_timing(started, state, cached_count, access, ready_spaces, visible_count):
        smart_log("Confluence filter query timing", platform="web", domain="framework", source="project_facts_owner", emit_runtime_event=False,
                  extra={"stage": "filter.query_aggregate", "duration_ms": round((perf_counter() - started) * 1000, 3),
                         "result_state": state, "cached_count": cached_count,
                         "visible_grant_count": len(access.ids("project", "catalog")),
                         "ready_space_count": len(ready_spaces), "visible_count": visible_count})

    def facts_version(self) -> str:
        return self._repository.facts_version()

    def invalidate_project(self, project_id: str, access) -> None:
        access.require("project", project_id, "catalog")
        access.revoke("project", project_id)

    def _service(self, access, password):
        client = self._client_factory(access.account, password)
        gateway = _ProjectFactsGateway(client, self._repository)
        return ConfluenceProjectCacheService(
            gateway, ConfluenceProjectMapper(), self._repository, access=access,
        )

    def audit_dependencies(self, access, password):
        client = self._client_factory(access.account, password)
        gateway = _ProjectFactsGateway(client, self._repository)
        return (
            ConfluenceProjectCacheService(
                gateway, ConfluenceProjectMapper(), self._repository, access=access,
            ),
            self._repository,
            client,
        )

    @staticmethod
    def _state(state, ready_product_spaces=()):
        return {
            "state": state, "accessibleProjectCount": 0,
            "detailState": "ready" if state == "ready" else "missing",
            "blockProjectCount": 0,
            "warningProjectCount": 0,
            "productSpaces": _product_space_rows(),
            "facets": [
                {"key": key, "label": label, "labels": [label],
                 "options": _product_space_rows(ready_product_spaces) if key == PRODUCT_SPACE_FACET else []}
                for key, label in PROJECT_FILTER_FACET_DEFINITIONS
            ],
            "projects": [], "ownerHierarchy": [], "discrepancies": [],
            "counts": {"stale": 0, "failed": 0, "inactive": 0},
        }


class _ProjectFactsGateway:
    def __init__(self, client, repository):
        self._client = client
        self._repository = repository
        self._details = {}

    def refresh_project_catalogs(self, scope):
        buffer = _CatalogSyncBuffer()
        snapshot = refresh_project_catalogs(self._client, buffer, manager=WEB_TASKS)
        rows = snapshot.get("projects") or ()
        if scope.product_space_keys:
            allowed = set(scope.product_space_keys)
            rows = [row for row in rows if row.get("space_key") in allowed]
        return {"projects": rows, "failed_product_spaces": snapshot.get("failed_product_spaces") or (),
                "complete_spaces": snapshot.get("complete_spaces") or ()}

    def query_project_catalog(self, query, page):
        payload = self.refresh_project_catalogs(ProjectSyncScope())
        rows = payload["projects"]
        return {"projects": rows, "page": page, "page_size": len(rows), "total": len(rows)}

    def get_project_catalog(self, project_id):
        self._details.pop(project_id, None)
        project = self._repository.get(project_id, ProjectDetails(facts=True))
        if project is None:
            raise KeyError(project_id)
        return _catalog_row(project)

    def get_project_catalog_by_url(self, url, product_line):
        entry = self._client.get_page_by_url(url)
        root = self._client.get_parent_page(entry.id)
        if root is None:
            raise LookupError("project_root_missing")
        location = ProjectLocation("", "", root.title, root.url, root.url,
                                   space_key=product_line.confluence_space_key)
        basic, error, _context = locate_basic_information(
            self._client, location, resolved_root_page_id=root.id,
        )
        if basic is None:
            raise LookupError(error)
        basic = self._client.get_page(basic.id)
        fields = table_fields(basic.body or basic.view_body)
        project_id = str(fields.get("project id") or "").strip()
        if not project_id:
            raise ValueError("project_id_missing")
        return {
            "identity": f"{product_line.confluence_space_key}:{project_id}",
            "project_id": project_id, "name": canonical_project_name(basic.title, project_id),
            "space_key": product_line.confluence_space_key, "space_name": product_line.name,
            "space_url": product_line.confluence_url, "page_id": root.id, "page_url": root.url,
            "fields": fields,
            "catalog_source": {"page_id": root.id, "title": root.title, "url": root.url,
                               "version": root.version},
        }

    def load_project_sections(self, project_id, sections):
        if project_id not in self._details:
            project = self._repository.get(project_id, ProjectDetails(facts=True))
            if project is None:
                raise KeyError(project_id)
            self._details[project_id] = extract_project_detail(self._client, _catalog_row(project))
        detail = self._details[project_id]
        result = {}
        if "roles" in sections:
            result["roles"] = detail.get("roles") or {}
        if "facts" in sections:
            result["facts"] = detail.get("fields") or {}
        if "evidence" in sections:
            result["evidence"] = [
                {"source": "catalog", **detail["catalog_source"]},
                *detail["evidence"],
            ]
            result["access_grants"] = [
                ("page", item["page_id"], "metadata", f"{project_id}:evidence")
                for item in detail["evidence"]
            ]
        for name in ("milestones", "hardware", "software"):
            if name in sections:
                result[name] = {}
        return result


class _CatalogSyncBuffer:
    def __init__(self):
        self._snapshot = None

    def load(self):
        return self._snapshot

    def save(self, snapshot):
        self._snapshot = snapshot


def _catalog_row(project):
    fields = dict(project.facts.value.values) if project.facts.value is not None else {}
    return {
        "identity": project.identity.confluence_id,
        "page_id": project.catalog_page.page_id,
        "project_id": project.identity.project_id,
        "name": project.name,
        "space_key": project.product_space.key,
        "space_name": project.product_space.name,
        "space_url": project.product_space.url,
        "page_url": project.catalog_page.url,
        "catalog_source": {
            "page_id": project.catalog_page.page_id,
            "title": project.catalog_page.title,
            "url": project.catalog_page.url,
            "version": project.revision.value,
        },
        "fields": {**fields,
            "project status": project.status.name if project.status else "",
            "current stage": project.stage.name if project.stage else "",
            "support mode": project.support_mode.name if project.support_mode else "",
            "oem/operator": project.customer_summary,
        },
    }


def _project_snapshot_rows(project):
    fields = dict(project.facts.value.values) if project.facts.value is not None else {}
    fields.update({
        "project id": project.identity.project_id,
        "project status": project.status.name if project.status else "",
        "current stage": project.stage.name if project.stage else "",
        "support mode": project.support_mode.name if project.support_mode else "",
    })
    all_roles = {
        role.role.name: [
            {"identity": person.identity, "account": person.account, "name": person.display_name}
            for person in role.people
        ]
        for role in (project.roles.value or ())
    }
    source_identity = project.identity.confluence_id
    original = {
        "identity": project.identity.confluence_id,
        "source_identity": source_identity,
        "project_id": project.identity.project_id,
        "name": project.name,
        "space_key": project.product_space.key,
        "status": project.status.name if project.status else "",
        "stage": project.stage.name if project.stage else "",
        "support_mode": project.support_mode.name if project.support_mode else "",
        "customer_summary": project.customer_summary,
        "page_id": project.catalog_page.page_id,
        "page_url": project.catalog_page.url,
        "active": True,
        "fields": fields,
        "roles": {label: all_roles.get(label, []) for label in ROLE_LABELS},
    }
    if not is_wireless_module(fields.get("wifi module")):
        return (original,)
    wireless = deepcopy(original)
    wireless.update(
        identity=f"{source_identity}:wireless",
        space_key=WIRELESS_CONNECTION.confluence_space_key,
        roles={
            label: all_roles.get(wifi_label, [])
            for (label, wifi_label) in zip(ROLE_LABELS, WIFI_ROLE_LABELS)
        },
    )
    return original, wireless


def _detail_state(projects):
    states = tuple(
        getattr(project, section).state
        for project in projects
        for section in ("roles", "facts")
    )
    if any(state in {DetailState.STALE, DetailState.FAILED} for state in states):
        return "stale"
    if any(state is not DetailState.LOADED for state in states):
        return "missing"
    return "ready"


def _product_space_rows(allowed=None):
    allowed = None if allowed is None else set(allowed)
    return [
        {
            "value": line.confluence_space_key,
            "label": line.name,
            "projectGrouping": line.project_grouping or None,
        }
        for line in DASHBOARD_PRODUCT_LINES
        if allowed is None or line.confluence_space_key in allowed
    ]


def _facet_rows(values, ready_product_spaces=()):
    labels = dict(PROJECT_FILTER_FACET_DEFINITIONS)
    keys = tuple(labels)
    return [{
        "key": key,
        "label": labels.get(key, key.title()),
        "labels": [labels.get(key, key.title())],
        "options": (_product_space_rows(ready_product_spaces)
                    if key == PRODUCT_SPACE_FACET else values.get(key, [])),
    } for key in keys]
