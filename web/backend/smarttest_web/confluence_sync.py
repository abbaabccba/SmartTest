from __future__ import annotations

from concurrent.futures import as_completed

from core.confluence.project import ProjectDetails
from core.async_tasks import TaskCancelled
from .task_manager import WEB_TASKS


class ConfluenceProjectSyncCoordinator:
    """Bounded foreground orchestration over the current-cache service."""

    def __init__(self, cache_service, *, max_workers: int = 4, manager=WEB_TASKS):
        self._cache_service = cache_service
        self._max_workers = max(1, int(max_workers))
        self._manager = manager

    def sync(
        self,
        project_ids,
        details: ProjectDetails,
        *,
        cancelled=lambda: False,
        progress=lambda *_: None,
        parent_id="",
        require_complete=False,
    ) -> list[str]:
        identifiers = tuple(str(project_id) for project_id in project_ids)

        def refresh(project_id: str) -> str:
            if cancelled():
                return "cancelled"
            try:
                project = self._cache_service.refresh_project(project_id, details,
                    **({'require_complete': True} if require_complete else {}))
                if require_complete:
                    from core.domain.detail import DetailState
                    if any(getattr(project, section).state is not DetailState.LOADED for section in details.sections()):
                        return 'failed'
            except TaskCancelled:
                raise
            except Exception:
                return "failed"
            return "updated"

        results = ["cancelled"] * len(identifiers)
        def submit(project_id):
            runner = lambda token, _progress: (token.raise_if_cancelled(), refresh(project_id))[-1]
            options = {"resource_key": "confluence-project-detail", "resource_limit": self._max_workers}
            if parent_id:
                return self._manager.submit_child(parent_id, "confluence-project-detail", runner, **options)
            return self._manager.submit("confluence-project-detail", runner, **options)

        futures = {submit(project_id): index for index, project_id in enumerate(identifiers)}
        completed = 0
        while futures:
            future = next(as_completed(futures))
            index = futures.pop(future)
            results[index] = future.result()
            completed += 1
            progress(completed, len(identifiers))
        return results
