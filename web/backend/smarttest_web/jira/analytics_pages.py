from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Condition, Thread

from core.async_tasks import TaskCancelled, cancellation_context
from core.jira.gateway import JiraGatewayError


class JiraMetricPages:
    """One bounded, round-robin page budget for Web Comment and Verify jobs."""

    def __init__(self, workers=12):
        self._condition = Condition()
        self._jobs = deque()
        self._active = 0
        self._workers = workers
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='jira-metric-page')
        self._thread = Thread(target=self._dispatch, name='jira-metric-dispatch', daemon=True)
        self._thread.start()

    def run(self, gateway, query, metric, token, consume, progress):
        job = dict(gateway=gateway, query=query, metric=metric, token=token, consume=consume,
                   progress=progress, next=0, total=None, pending=0, processed=0, error=None, future=Future())
        with self._condition:
            if self._closed:
                raise TaskCancelled()
            self._jobs.append(job)
            self._condition.notify_all()
        return job['future'].result()

    def _dispatch(self):
        with self._condition:
            while True:
                for job in tuple(self._jobs):
                    try:
                        job['token'].raise_if_cancelled()
                    except TaskCancelled as error:
                        job['error'] = error
                    finished = job['error'] or (job['total'] is not None and job['next'] >= job['total'])
                    if finished and not job['pending']:
                        self._jobs.remove(job)
                        if job['error']:
                            job['future'].set_exception(job['error'])
                        else:
                            job['future'].set_result(job['processed'])
                if self._closed and not self._jobs:
                    return
                dispatched = False
                if self._active < self._workers:
                    for _ in range(len(self._jobs)):
                        job = self._jobs[0]
                        self._jobs.rotate(-1)
                        if job['error'] or (job['total'] is None and job['pending']) or (
                            job['total'] is not None and job['next'] >= job['total']):
                            continue
                        offset = job['next']
                        job['next'] += 100
                        job['pending'] += 1
                        self._active += 1
                        self._pool.submit(self._page, job, offset)
                        dispatched = True
                        break
                if not dispatched:
                    self._condition.wait(.1)

    def _page(self, job, offset):
        try:
            job['token'].raise_if_cancelled()
            with cancellation_context(job['token']):
                page = job['gateway'].search_metric_page(job['query'], offset, job['metric'])
                rows, total = page.get('issues') or [], int(page.get('total') or 0)
                if len(rows) < min(100, max(0, total - offset)):
                    raise JiraGatewayError('jira_incomplete_page')
                job['token'].raise_if_cancelled()
                job['consume'](rows)
                with self._condition:
                    job['total'] = max(job['total'] or 0, total)
                    job['processed'] += len(rows)
                    job['progress'](job['processed'], job['total'])
        except Exception as error:
            with self._condition:
                job['error'] = job['error'] or error
        finally:
            with self._condition:
                job['pending'] -= 1
                self._active -= 1
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            for job in self._jobs:
                job['error'] = TaskCancelled()
            self._condition.notify_all()
        self._thread.join()
        self._pool.shutdown(wait=True, cancel_futures=True)
