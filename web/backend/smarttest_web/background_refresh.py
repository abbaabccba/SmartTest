from __future__ import annotations

from threading import Lock

from core.logging import smart_log

from .resource_access import remote_status
from .task_manager import WEB_TASKS, ScopedTaskIndex, snapshot_payload


class BackgroundFactsRefresh:
    """Process-local single-flight refresh state; credentials live only in the worker closure."""

    def __init__(self, submit=None):
        self._submit = submit or self._start_thread
        self._lock = Lock()
        self._jobs = {}
        self._index = ScopedTaskIndex(WEB_TASKS)

    @staticmethod
    def _start_thread(work):
        return WEB_TASKS.submit("confluence-facts", lambda token, progress: work(progress, token))

    @property
    def state(self):
        return self.state_for("")

    def state_for(self, access):
        return self.status_for(access)["state"]

    def status_for(self, access):
        account = str(access).strip().casefold()
        with self._lock:
            job = self._jobs.get(account)
            if not job:
                return {"state": "idle", "completed": 0, "total": 0}
            status = {"state": job.get("terminal") or "loading",
                      "completed": job["completed"], "total": job["total"]}
            task_id = self._index.current((account, "confluence"))
        if task_id:
            try:
                task = WEB_TASKS.snapshot(task_id)
                status.update(
                    state={"queued": "loading", "running": "loading", "completed": job.get("terminal") or "ready"}.get(task.state, task.state),
                    completed=task.progress[0], total=task.progress[1], task=snapshot_payload(task),
                )
            except KeyError:
                pass
        return status

    def start_details(
        self, owner, access, password, *, filters=None, search="", on_error=None,
        on_success=None,
    ):
        account = access.session_hash
        with self._lock:
            if self._active(account, self._jobs.get(account)):
                return False
            job = {
                "terminal": "", "completed": 0, "total": 0, "kind": "details",
            }
            self._jobs[account] = job

        def cancelled():
            with self._lock:
                task_id = self._index.current((account, "confluence"))
            if not task_id:
                return bool(job.get("terminal") == "cancelled")
            try:
                return WEB_TASKS.snapshot(task_id).state == "cancelled"
            except KeyError:
                return True

        def progress(completed, total, manager_progress=lambda _completed, _total: None):
            with self._lock:
                job.update(completed=completed, total=total)
            manager_progress(completed, total)

        def work(manager_progress=lambda _completed, _total: None, manager_token=None):
            if cancelled():
                return
            try:
                options = {
                    "filters": filters, "search": search, "cancelled": cancelled,
                    "progress": lambda completed, total: progress(completed, total, manager_progress),
                }
                if manager_token is not None:
                    options["parent_task_id"] = manager_token.task_id
                owner.refresh_and_sync_details(access, password, **options)
                if not cancelled() and on_success is not None:
                    on_success()
            except Exception as error:  # noqa: BLE001 - only safe job state crosses the API
                error_state = on_error(error) if on_error is not None else "failed"
                smart_log('Confluence project refresh failed', platform='web', domain='framework',
                          level='error', source='background_facts_refresh', emit_runtime_event=False,
                          extra={'account': access.account, 'stage': 'filter.refresh_and_sync_details',
                                 'state': error_state, 'exception_type': type(error).__name__,
                                 'http_status': remote_status(error)})
                with self._lock:
                    job["terminal"] = error_state
            else:
                if not cancelled():
                    with self._lock:
                        job["terminal"] = "ready"

        submitted = self._submit(work)
        if submitted is not None:
            self._index.replace((account, "confluence"), WEB_TASKS.task_id(submitted))
        return True

    def cancel(self, access):
        account = str(access).strip().casefold()
        with self._lock:
            job = self._jobs.get(account)
            if not job or job.get("kind") != "details" or not self._active(account, job):
                return False
            job["terminal"] = "cancelled"
            task_id = self._index.current((account, "confluence"))
        return WEB_TASKS.cancel(task_id) if task_id else True

    def _active(self, account, job):
        if not job or job.get("terminal"):
            return False
        task_id = self._index.current((account, "confluence"))
        if not task_id:
            return True
        try:
            return WEB_TASKS.snapshot(task_id).state in {"queued", "running"}
        except KeyError:
            return False
