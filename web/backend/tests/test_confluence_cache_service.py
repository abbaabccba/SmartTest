from __future__ import annotations
from sqlalchemy import text

import pytest

from conftest import confirmed_access
from core.confluence.project import ProjectDetails, ProjectQuery, ProjectSyncScope
from core.confluence.project_mapper import ConfluenceProjectMapper
from core.domain.detail import DetailSection, DetailState
from core.domain.values import FieldBag
from smarttest_web.confluence.cache_service import ConfluenceProjectCacheService
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.release_query import ProjectReleaseQueryService


def _row(version=1):
    return {
        "identity": "900", "project_id": "P100", "name": "Project One",
        "space_key": "DOPL", "space_name": "DOPL", "page_url": "https://c/900",
        "fields": {"project status": "Normal", "current stage": "EVT", "support mode": "Onsite",
                   "project owner": "Catalog Owner", "launch os": "Android 16", "launch time": "2026-10-01"},
        "project_owners": [{"identity": "owner-1", "name": "Catalog Owner"}],
        "catalog_source": {"page_id": "10", "title": "Catalog", "version": version},
    }


class ConfluenceGateway:
    def __init__(self):
        self.version = 1
        self.catalog_calls = 0
        self.detail_calls = []
        self.fail_sections = set()

    def query_project_catalog(self, query, page):
        self.catalog_calls += 1
        return {"projects": [_row(self.version)], "page": page, "page_size": 100, "total": 1}

    def get_project_catalog(self, project_id):
        return _row(self.version)

    def refresh_project_catalogs(self, scope):
        return {"projects": [_row(self.version)], "failed_product_spaces": []}

    def load_project_sections(self, project_id, sections):
        self.detail_calls.append((project_id, sections))
        if sections[0] in self.fail_sections:
            raise RuntimeError("offline")
        return {
            "roles": {"FAE QA": [{"identity": "u1", "name": "Alice"}]},
            "milestones": {"SOP": "2026-10"}, "hardware": {}, "software": {},
            "facts": {"region": "US"}, "evidence": [],
        }


def _service(tmp_path, *, database_factory):
    gateway = ConfluenceGateway()
    repository = ConfluenceProjectRepository(database_factory())
    return ConfluenceProjectCacheService(gateway, ConfluenceProjectMapper(), repository, access=confirmed_access(repository.database)), gateway, repository


def test_catalog_ready_is_not_published_when_database_write_fails(
    tmp_path, monkeypatch, *, database_factory
) -> None:
    import smarttest_web.confluence.cache_service as cache_module

    records = []
    monkeypatch.setattr(cache_module, "smart_log", lambda message, **kwargs: records.append((message, kwargs)), raising=False)
    service, gateway, repository = _service(tmp_path, database_factory=database_factory)
    gateway.refresh_project_catalogs = lambda _scope: {
        "projects": [_row()], "failed_product_spaces": [], "complete_spaces": ["DOPL"],
    }
    monkeypatch.setattr(repository, "save_core", lambda _projects: (_ for _ in ()).throw(RuntimeError("db failed")))

    with pytest.raises(RuntimeError, match="db failed"):
        service.refresh_projects(ProjectSyncScope())

    assert service._access.ids("project", "catalog") == set()
    assert service._access.ids("catalog", "ready") == set()
    failed = next(kwargs for message, kwargs in records if message == "Confluence catalog publish timing" and kwargs["extra"].get("outcome") == "failure")
    assert failed["extra"]["exception_type"] == "RuntimeError"
    assert failed["extra"]["database_error_code"] is None
    assert "db failed" not in repr(records)


def test_catalog_refresh_emits_safe_mapping_and_publish_timings(
    tmp_path, monkeypatch, *, database_factory
) -> None:
    import smarttest_web.confluence.cache_service as cache_module
    import smarttest_web.confluence.project_repository as repository_module

    records = []
    capture = lambda message, **kwargs: records.append((message, kwargs))
    monkeypatch.setattr(cache_module, "smart_log", capture, raising=False)
    monkeypatch.setattr(repository_module, "smart_log", capture, raising=False)
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )
    gateway.refresh_project_catalogs = lambda _scope: {
        "projects": [_row()], "failed_product_spaces": [], "complete_spaces": ["DOPL"],
    }

    service.refresh_projects(ProjectSyncScope())

    stages = {kwargs["extra"]["stage"] for _message, kwargs in records}
    assert {
        "filter.catalog_mapping",
        "filter.catalog_publish_begin",
        "filter.catalog_publish",
        "filter.database_write",
    } <= stages
    assert "P100" not in repr(records) and "900" not in repr(records)


def test_confluence_list_fetches_only_core_and_get_loads_only_requested_section(
    tmp_path, *, database_factory
) -> None:
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )

    page = service.list_projects(ProjectQuery(), 0, 100)
    project = service.get_project("P100", ProjectDetails(facts=True))

    assert page.projects[0].facts.state is DetailState.UNLOADED
    assert project.facts.state is DetailState.LOADED
    assert project.roles.state is DetailState.UNLOADED
    assert dict(project.facts.value.values)["current stage"] == "EVT"
    assert gateway.detail_calls == []


def test_confluence_revision_change_and_remote_failure_preserve_cache(
    tmp_path, *, database_factory
) -> None:
    service, gateway, repository = _service(tmp_path, database_factory=database_factory)
    service.list_projects(ProjectQuery(), 0, 100)
    service.get_project("P100", ProjectDetails(roles=True, facts=True))
    gateway.version = 2
    service.refresh_projects(ProjectSyncScope())

    stale = repository.get("P100", ProjectDetails(roles=True, facts=True, evidence=True))
    assert stale.roles.state is DetailState.STALE
    assert stale.facts.state is DetailState.STALE
    assert stale.evidence.state is DetailState.UNLOADED

    gateway.fail_sections.add("facts")
    failed = service.refresh_project("P100", ProjectDetails(facts=True))
    assert failed.facts.state is DetailState.FAILED
    assert failed.facts.error_code == "remote_unavailable"
    assert dict(failed.facts.value.values)["current stage"] == "EVT"
    cached = repository.get("P100", ProjectDetails(roles=True))
    assert cached.roles.state is DetailState.STALE


def test_catalog_refresh_merges_catalog_facts_without_erasing_provider_details(
    tmp_path, *, database_factory
) -> None:
    service, _gateway, repository = _service(
        tmp_path, database_factory=database_factory
    )
    service.list_projects(ProjectQuery())
    repository.replace_facts(
        "P100", DetailSection.loaded(FieldBag.from_mapping({"wifi module": "W2"})),
    )

    service.refresh_projects(ProjectSyncScope())

    facts = repository.get("P100", ProjectDetails(facts=True)).facts
    assert facts.state is DetailState.LOADED
    assert dict(facts.value.values)["wifi module"] == "W2"
    assert dict(facts.value.values)["launch os"] == "Android 16"


def test_confluence_empty_filter_result_does_not_refetch_when_cache_exists(
    tmp_path, *, database_factory
) -> None:
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )
    service.list_projects(ProjectQuery(), 0, 100)

    page = service.list_projects(
        ProjectQuery.from_filters({"current stage": ("DVT",)}), 0, 100,
    )

    assert page.projects == ()
    assert gateway.catalog_calls == 1


def test_confluence_remote_detail_failure_preserves_catalog_fields(
    tmp_path, *, database_factory
) -> None:
    service, gateway, _repository = _service(
        tmp_path, database_factory=database_factory
    )
    service.list_projects(ProjectQuery())
    gateway.fail_sections.add("facts")

    project = service.refresh_project("P100", ProjectDetails(facts=True))

    assert project.facts.state is DetailState.FAILED
    assert dict(project.facts.value.values)["current stage"] == "EVT"
    assert project.facts.error_code == "remote_unavailable"


def test_detail_refresh_preserves_confluence_catalog_owner_and_exposes_all_major_qa(
    tmp_path, *, database_factory
) -> None:
    service, gateway, repository = _service(tmp_path, database_factory=database_factory)
    gateway.load_project_sections = lambda _project_id, _sections: {
        "roles": {
            "Major FAE QA": [
                {"identity": "qa-1", "name": "Alice"},
                {"identity": "qa-2", "name": "Bob"},
            ],
            "FAE QA": [{"identity": "other-qa", "name": "Mallory"}],
        },
    }
    service.refresh_projects(ProjectSyncScope())

    refreshed = service.refresh_project("P100", ProjectDetails(roles=True))
    dashboard = ProjectReleaseQueryService(repository.database).dashboard(visible_ids=("900",))
    release = dashboard["releases"][0]
    with repository.database.connect() as connection:
        stored_roles = tuple(
            connection.execute(
                text("""SELECT role_id,role_name FROM confluence_project_roles
            WHERE confluence_id='900' ORDER BY role_id""")
            )
        )

    assert [person.display_name for person in refreshed.owner_summary] == ["Catalog Owner"]
    assert stored_roles == (
        ("role.fae_qa", "FAE QA"),
        ("role.major_fae_qa", "Major FAE QA"),
    )
    assert release["projectOwners"] == "Catalog Owner"
    assert set(release["majorFaeQa"].split(", ")) == {"Alice", "Bob"}
    assert "Mallory" not in release["majorFaeQa"]
