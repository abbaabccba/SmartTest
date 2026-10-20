"""Bounded retries for idempotent remote reads; never wrap business runners."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import sleep

from requests.exceptions import ConnectionError, Timeout, SSLError, ChunkedEncodingError
from tenacity import Retrying, retry_if_exception, stop_after_attempt

from core.async_tasks import current_cancellation
from core.logging import smart_log

READ_MAX_ATTEMPTS = 4
READ_RETRY_DELAYS = (1, 2, 4)


def _status(error):
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) or getattr(error, "status_code", None)


def _retryable(error):
    if isinstance(error, SSLError):
        return False
    return isinstance(error, (ConnectionError, Timeout, ChunkedEncodingError)) or _status(error) in {429, 502, 503, 504}


def _delay(state):
    error = state.outcome.exception()
    if _status(error) == 429:
        response = getattr(error, "response", None)
        header = getattr(response, "headers", {}).get("Retry-After", "")
        try:
            seconds = int(header) if str(header).isdigit() else (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            if seconds >= 0:
                return seconds
        except (TypeError, ValueError, OverflowError):
            pass
    return READ_RETRY_DELAYS[min(state.attempt_number - 1, 2)]


def read_request(operation, *args, **kwargs):
    token = current_cancellation()
    def check():
        if token is not None:
            token.raise_if_cancelled()
    def wait(seconds):
        check()
        (token.wait if token is not None else sleep)(seconds)
        check()
    def before_sleep(state):
        error = state.outcome.exception()
        task_id = getattr(token, "task_id", "")
        task_id = task_id if isinstance(task_id, str) else ""
        smart_log(f"Remote read retry operation={getattr(operation, '__name__', type(operation).__name__)} taskId={task_id} attempt={state.attempt_number}/{READ_MAX_ATTEMPTS} "
                  f"waitSeconds={state.next_action.sleep} errorType={type(error).__name__} httpStatus={_status(error)}",
                  domain="framework", source="read_requests")
    for attempt in Retrying(stop=stop_after_attempt(READ_MAX_ATTEMPTS), retry=retry_if_exception(_retryable),
                            wait=_delay, sleep=wait, before_sleep=before_sleep, reraise=True):
        with attempt:
            check()
            result = operation(*args, **kwargs)
            check()
            return result
