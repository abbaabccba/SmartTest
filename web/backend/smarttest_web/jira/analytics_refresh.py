import hashlib
from time import monotonic

from core.async_tasks import TaskCancelled
from core.jira.mapper import JiraIssueMapper
from core.jira.services.jql_statistics_cards import build_annual_facts
from core.logging import smart_log


JIRA_REFRESH_LOG_INTERVAL_SECONDS = 5


class JiraRefreshProgress:
    """Log existing refresh progress at a bounded rate; forward every update."""

    def __init__(self, card, metric, progress):
        self.card, self.metric, self.progress = card, metric, progress
        self.finished = False
        self.processed = self.total = 0
        self.started = self.last_logged = monotonic()
        self._log('started')

    def _log(self, outcome, **extra):
        details = ' '.join(f'{key}={extra[key]}' for key in ('snapshot_id', 'stage', 'period', 'error_code', 'cause_type', 'http_status') if key in extra)
        smart_log(f'Jira refresh card={self.card} metric={self.metric} '
                  f'{outcome} progress={self.processed}/{self.total} {details}',
                  platform='web', domain='jira', source='jira_refresh',
                  level='ERROR' if outcome == 'failed' else 'INFO',
                  extra={'card_key': self.card, 'metric': self.metric, 'outcome': outcome,
                         'processed': self.processed, 'total': self.total,
                         'duration_ms': round((monotonic() - self.started) * 1000, 3), **extra})

    def __call__(self, count, total):
        self.processed, self.total = count, total
        self.progress(count, total)
        now = monotonic()
        if now - self.last_logged >= JIRA_REFRESH_LOG_INTERVAL_SECONDS:
            self._log('progress')
            self.last_logged = now

    def finish(self, outcome, **extra):
        self._log(outcome, **extra)
        self.finished = True


def metric_source_key(card, query, fingerprint, metric):
    return f'{metric}-v1:' + hashlib.sha256(f'{card}:{query}:{fingerprint}'.encode()).hexdigest()


def refresh_metric(repository, pages, gateway, account, card, query, roster, metric, token, progress, *, log_progress=None):
    report = log_progress or JiraRefreshProgress(card, metric, progress)
    snapshot = None
    try:
        scope = metric_source_key(card, query, roster.fingerprint, metric)
        snapshot = repository.begin('', account, query, {}, '', expires_at=0, card_key=scope,
                                    roster_fingerprint=roster.fingerprint)
        metadata = {'annual': True, 'activityRoster': roster.fingerprint, 'metric': metric,
                    'qaEvidence': {'version': 1, 'roster': list(roster.assignments),
                                   'kinds': ['comment', 'attachment'] if metric == 'comment' else ['verify'],
                                   'complete': False}}
        def consume(rows):
            token.raise_if_cancelled()
            facts = build_annual_facts(gateway, [], [], current_activities=rows,
                                       previous_activities=[], activity_roster=roster)['_facts']
            events = [event for row in rows for event in JiraIssueMapper.qa_events(
                row, dict(roster.assignments), metric=metric)]
            if not repository.update_statistics(snapshot, metadata, facts=facts, qa_events=events):
                raise TaskCancelled()
        pages.run(gateway, query, metric, token, consume, report)
        token.raise_if_cancelled()
        metadata['qaEvidence']['complete'] = True
        if not repository.activate(snapshot, metadata):
            raise TaskCancelled()
        report.finish('completed')
        return snapshot
    except Exception as error:
        if snapshot is not None:
            repository.finish(snapshot, 'cancelled' if isinstance(error, TaskCancelled) else 'failed',
                              getattr(error, 'code', type(error).__name__))
        report.finish('cancelled' if isinstance(error, TaskCancelled) else 'failed',
                      error_code=getattr(error, 'code', type(error).__name__))
        raise
