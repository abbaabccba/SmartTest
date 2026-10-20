from __future__ import annotations

from threading import Event

from conftest import confirmed_access
from core.confluence.project import ProjectDetails
from core.confluence.project_mapper import ConfluenceProjectMapper
from core.domain.detail import DetailState
from smarttest_web.confluence.cache_service import ConfluenceProjectCacheService
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.confluence_sync import ConfluenceProjectSyncCoordinator
from core.async_tasks import AsyncTaskManager


class Gateway:
    def __init__(self, cancelled: Event):
        self.cancelled = cancelled
        self.detail_calls = []

    @staticmethod
    def get_project_catalog(project_id):
        return {
            "identity": f"page-{project_id}", "project_id": project_id, "name": project_id,
            "space_key": "DOPL", "catalog_source": {"page_id": "catalog", "version": 1},
            "fields": {},
        }

    def load_project_sections(self, project_id, sections):
        self.detail_calls.append((project_id, sections))
        self.cancelled.set()
        return {"facts": {"fresh": project_id}}


def test_sync_uses_cache_service_and_cancellation_prevents_queued_refresh(
    tmp_path, *, database_factory
) -> None:
    cancelled = Event()
    gateway = Gateway(cancelled)
    repository = ConfluenceProjectRepository(database_factory())
    repository.save_core((ConfluenceProjectMapper().from_catalog(gateway.get_project_catalog('P1')),))
    service = ConfluenceProjectCacheService(gateway, ConfluenceProjectMapper(), repository, access=confirmed_access(repository.database, ('P1', 'P2')))
    coordinator = ConfluenceProjectSyncCoordinator(service, max_workers=1)

    result = coordinator.sync(
        ("P1", "P2"), ProjectDetails(facts=True), cancelled=cancelled.is_set,
    )

    assert result == ["updated", "cancelled"]
    assert gateway.detail_calls == [("P1", ("facts",))]
    assert repository.get("P1", ProjectDetails(facts=True)).facts.state is DetailState.LOADED
    assert repository.get("P2", ProjectDetails()) is None


def test_sync_submits_resource_limited_children_under_the_owned_root() -> None:
    class Service:
        def refresh_project(self, _project_id, _details):
            return None

    manager = AsyncTaskManager(max_workers=2, child_visibility_seconds=0)
    root = manager.register_long_running("details")
    snapshots = []
    manager.subscribe(snapshots.append)
    try:
        coordinator = ConfluenceProjectSyncCoordinator(Service(), max_workers=1, manager=manager)
        assert coordinator.sync(("P1", "P2"), ProjectDetails(facts=True), parent_id=root) == ["updated", "updated"]
        children = {task.id for task in snapshots if task.parent_id == root}
        assert len(children) == 2
        assert all(task.root_id == root for task in snapshots if task.id in children)
    finally:
        manager.complete_long_running(root)
        manager.close()
