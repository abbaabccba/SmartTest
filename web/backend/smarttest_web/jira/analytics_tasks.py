from __future__ import annotations
from threading import RLock

from ..task_manager import WEB_TASKS, ScopedTaskIndex, snapshot_payload


class JiraAnalyticsTasks:
    def __init__(self, manager=WEB_TASKS):
        self.manager = manager
        self._index = ScopedTaskIndex(manager)
        self._annual_lock = RLock()
        self._annual = {}
        self._pages = None

    @property
    def pages(self):
        from .analytics_pages import JiraMetricPages
        with self._annual_lock:
            if self._pages is None:
                self._pages = JiraMetricPages()
            return self._pages

    def close_pages(self):
        with self._annual_lock:
            pages = self._pages
        if pages is not None:
            pages.close()

    def open_pages(self):
        with self._annual_lock:
            self._pages = None

    def submit_annual(self, scope, runner):
        with self._annual_lock:
            future = self._annual.get(scope)
            if future is not None and not future.done():
                return future
            future = self.manager.submit_coordinator("jira-annual-activity", runner)
            self._annual[scope] = future
            def discard(_future):
                with self._annual_lock:
                    if self._annual.get(scope) is _future:
                        self._annual.pop(scope, None)
            future.add_done_callback(discard)
            return future

    def submit(self, session_hash, runner, *, card_key=""):
        key = (str(session_hash), str(card_key))
        future = self.manager.submit("jira-analytics-search", runner)
        task_id = self.manager.task_id(future)
        self._index.replace(key, task_id)
        return task_id

    def status(self, session_hash, task_id, *, card_key=""):
        if not self._index.owns((session_hash, card_key), task_id):
            return None
        return {"id": str(task_id), **snapshot_payload(self.manager.snapshot(task_id))}

    def cancel(self, session_hash, task_id, *, card_key=""):
        if not self._index.owns((session_hash, card_key), task_id):
            return False
        return self.manager.cancel(str(task_id))

    def clear_session(self, session_hash):
        self._index.clear_prefix(session_hash)


JIRA_ANALYTICS_TASKS = JiraAnalyticsTasks()
