from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event, RLock
from time import monotonic
import os
from uuid import uuid4


_CURRENT_TOKEN = ContextVar("async-task-token", default=None)


def current_cancellation():
    return _CURRENT_TOKEN.get()


def check_cancellation():
    token = current_cancellation()
    if token is not None:
        token.raise_if_cancelled()


@contextmanager
def cancellation_context(token):
    context = _CURRENT_TOKEN.set(token)
    try:
        yield
    finally:
        _CURRENT_TOKEN.reset(context)


class TaskCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self, task_id: str = "") -> None:
        self.task_id = str(task_id)
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def wait(self, seconds):
        if self._event.wait(seconds):
            self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TaskCancelled("cancelled")


@dataclass(frozen=True)
class AsyncTaskSnapshot:
    id: str
    label: str
    state: str
    progress: tuple[int, int]
    root_id: str
    parent_id: str = ""
    visible_child: "AsyncTaskSnapshot | None" = None
    revision: int = 0


@dataclass
class _Task:
    id: str
    label: str
    root_id: str
    parent_id: str
    created_at: float
    token: CancellationToken
    state: str = "queued"
    completed: int = 0
    total: int = 0
    progress_at: float = 0.0
    future: Future | None = None
    cancel_callback: object | None = None
    result: object | None = None


class AsyncTaskManager:
    """Framework-neutral bounded task owner for one process."""

    def __init__(self, *, max_workers: int = 16, child_visibility_seconds: float = 2.0,
                 progress_coalesce_seconds: float = 0.25, clock=monotonic):
        self.max_workers = max(1, int(max_workers))
        self.child_visibility_seconds = float(child_visibility_seconds)
        self.progress_coalesce_seconds = float(progress_coalesce_seconds)
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._coordinator_executor = ThreadPoolExecutor(max_workers=1)
        self._lock = RLock()
        self._tasks: dict[str, _Task] = {}
        self._revisions: dict[str, int] = {}
        self._subscribers = []
        self._resource_limits = {}
        self._closed = False

    @classmethod
    def from_environment(cls):
        def value(name, default):
            try: return float(os.getenv(name, default))
            except ValueError: return float(default)
        return cls(max_workers=max(1, int(value("SMARTTEST_ASYNC_MAX_CONCURRENCY", 16))),
                   child_visibility_seconds=max(0.0, value("SMARTTEST_ASYNC_TASK_CHILD_VISIBILITY_SECONDS", 2)),
                   progress_coalesce_seconds=max(0.0, value("SMARTTEST_ASYNC_TASK_PROGRESS_COALESCE_SECONDS", 0.25)))

    def subscribe(self, callback):
        with self._lock: self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback) if callback in self._subscribers else None

    def task_id(self, future):
        return next(task_id for task_id, task in self._tasks.items() if task.future is future)

    def _publish(self, task):
        self._revisions[task.id] = self._revisions.get(task.id, 0) + 1
        snapshot = self._snapshot(task)
        for callback in tuple(self._subscribers): callback(snapshot)

    def register_long_running(self, label, cancel_callback=None) -> str:
        with self._lock:
            task_id = uuid4().hex
            task = _Task(task_id, str(label), task_id, "", self._clock(), CancellationToken(), state="running", cancel_callback=cancel_callback)
            self._tasks[task_id] = task
            self._publish(task)
            return task_id

    def complete_long_running(self, task_id, *, failed=False) -> None:
        with self._lock:
            task = self._tasks[str(task_id)]
            task.state = "failed" if failed else "completed"
            self._publish(task)

    def submit(self, label, runner, *, resource_key="", resource_limit=0) -> Future:
        return self._submit(label, runner, parent_id="", resource_key=resource_key,
                            resource_limit=resource_limit)

    def submit_coordinator(self, label, runner) -> Future:
        """Run a root coordinator outside the bounded worker capacity."""
        return self._submit(label, runner, parent_id="", coordinator=True)

    def submit_child(self, parent_id, label, runner, *, resource_key="", resource_limit=0) -> Future:
        return self._submit(label, runner, parent_id=str(parent_id), resource_key=resource_key,
                            resource_limit=resource_limit)

    def _submit(self, label, runner, *, parent_id: str, coordinator=False,
                resource_key="", resource_limit=0) -> Future:
        with self._lock:
            if self._closed:
                raise RuntimeError("task manager is closed")
            parent = self._tasks.get(parent_id) if parent_id else None
            if parent_id and parent is None:
                raise KeyError(parent_id)
            task_id = uuid4().hex
            task = _Task(task_id, str(label), parent.root_id if parent else task_id,
                         parent_id, self._clock(), CancellationToken(task_id))
            self._tasks[task_id] = task
            root = self._tasks[task.root_id]
            if parent is not None and (parent.token._event.is_set() or root.token._event.is_set()):
                task.token.cancel()
                task.state = "cancelled"
                task.future = Future()
                task.future.cancel()
                self._publish(task)
            elif coordinator:
                task.future = self._coordinator_executor.submit(self._run, task, runner)
            elif resource_key:
                key = str(resource_key)
                limit = max(1, int(resource_limit))
                resource = self._resource_limits.get(key)
                if resource is None:
                    resource = self._resource_limits[key] = {
                        "limit": limit, "running": 0, "pending": deque(),
                    }
                elif resource["limit"] != limit:
                    raise ValueError(f"resource limit already configured: {key}")
                task.future = Future()
                resource["pending"].append((task, runner))
                self._dispatch_resource(key, resource)
            else:
                task.future = self._executor.submit(self._run, task, runner)
        return task.future

    def _dispatch_resource(self, key, resource):
        while resource["running"] < resource["limit"] and resource["pending"]:
            task, runner = resource["pending"].popleft()
            if task.future.cancelled():
                task.state = "cancelled"
                self._publish(task)
                continue
            resource["running"] += 1
            self._executor.submit(self._run_resource, key, task, runner)

    def _run_resource(self, key, task, runner):
        future = task.future
        try:
            if not future.set_running_or_notify_cancel():
                with self._lock:
                    task.state = "cancelled"
                    self._publish(task)
                return
            try:
                future.set_result(self._run(task, runner))
            except BaseException as error:
                future.set_exception(error)
        finally:
            with self._lock:
                resource = self._resource_limits[key]
                resource["running"] -= 1
                self._dispatch_resource(key, resource)

    def _run(self, task, runner):
        with self._lock:
            task.state = "running"
            self._publish(task)
        try:
            task.token.raise_if_cancelled()
            with cancellation_context(task.token):
                result = runner(task.token, lambda completed, total: self._progress(task, completed, total))
            task.token.raise_if_cancelled()
        except TaskCancelled:
            with self._lock:
                task.state = "cancelled"
                self._publish(task)
            raise
        except Exception:
            with self._lock:
                task.state = "failed"
                self._publish(task)
            raise
        else:
            with self._lock:
                task.state = "completed"
                task.result = result
                if task.total:
                    task.completed = task.total
                self._publish(task)
            return result

    def _progress(self, task, completed, total):
        now = self._clock()
        with self._lock:
            if now - task.progress_at < self.progress_coalesce_seconds and int(completed) < int(total):
                return
            task.completed, task.total, task.progress_at = int(completed), int(total), now
            self._publish(task)

    def cancel(self, task_id) -> bool:
        with self._lock:
            task = self._tasks.get(str(task_id))
            if task is None:
                return False
            for item in self._tasks.values():
                if item.root_id == task.root_id:
                    item.token.cancel()
                    if item.cancel_callback is not None:
                        item.cancel_callback()
                    if item.future is not None and item.future.cancel() and item.state == "queued":
                        item.state = "cancelled"
                        self._publish(item)
            return True

    def join(self, task_id, timeout=None) -> None:
        """Drain one root and its children without closing the shared manager."""
        with self._lock:
            root = self._tasks[self._tasks[str(task_id)].root_id]
        if root.future is not None:
            _done, pending = wait((root.future,), timeout=timeout)
            if pending:
                raise TimeoutError
        with self._lock:
            children = tuple(item.future for item in self._tasks.values()
                             if item.root_id == root.id and item.future is not None)
        _done, pending = wait(children, timeout=timeout)
        if pending:
            raise TimeoutError

    def snapshot(self, task_id) -> AsyncTaskSnapshot:
        with self._lock:
            task = self._tasks[str(task_id)]
            return self._snapshot(task)

    def result(self, task_id):
        with self._lock:
            return self._tasks[str(task_id)].result

    def _snapshot(self, task):
        now = self._clock()
        descendants = [item for item in self._tasks.values() if not task.parent_id and item.root_id == task.root_id and item.id != task.id
                       and item.state == "running" and now - item.created_at >= self.child_visibility_seconds]
        child = min(descendants, key=lambda item: item.created_at, default=None)
        return AsyncTaskSnapshot(task.id, task.label, task.state,
                                 (task.completed, task.total), task.root_id,
                                 task.parent_id, self._snapshot(child) if child else None,
                                 self._revisions.get(task.id, 0))

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(self._tasks.values())
            for task in tasks:
                task.token.cancel()
        for task in tasks:
            if task.cancel_callback is not None:
                task.cancel_callback()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._coordinator_executor.shutdown(wait=True, cancel_futures=True)
