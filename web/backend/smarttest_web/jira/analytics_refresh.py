import hashlib
from time import monotonic

from core.async_tasks import TaskCancelled
from core.jira.services.jql_statistics_cards import build_annual_facts
from core.logging import smart_log


def metric_source_key(card, query, fingerprint, metric):
    return f'{metric}-v1:' + hashlib.sha256(f'{card}:{query}:{fingerprint}'.encode()).hexdigest()


def refresh_metric(repository, pages, gateway, account, card, query, roster, metric, token, progress):
    scope = metric_source_key(card, query, roster.fingerprint, metric)
    snapshot = repository.begin('', account, query, {}, '', expires_at=0, card_key=scope,
                                roster_fingerprint=roster.fingerprint)
    started = monotonic()
    metadata = {'annual': True, 'activityRoster': roster.fingerprint, 'metric': metric}
    try:
        def consume(rows):
            token.raise_if_cancelled()
            facts = build_annual_facts(gateway, [], [], current_activities=rows,
                                       previous_activities=[], activity_roster=roster)['_facts']
            if not repository.update_statistics(snapshot, metadata, facts=facts):
                raise TaskCancelled()
        count = pages.run(gateway, query, metric, token, consume, progress)
        token.raise_if_cancelled()
        if not repository.activate(snapshot, metadata):
            raise TaskCancelled()
        smart_log('Jira metric refresh completed', platform='web', domain='jira',
                  extra={'card_key': card, 'metric': metric, 'processed': count,
                         'duration_ms': round((monotonic()-started)*1000, 3)})
        return snapshot
    except Exception as error:
        repository.finish(snapshot, 'cancelled' if isinstance(error, TaskCancelled) else 'failed',
                          getattr(error, 'code', type(error).__name__))
        smart_log('Jira metric refresh failed', platform='web', domain='jira', level='ERROR',
                  extra={'card_key': card, 'metric': metric, 'error_code': getattr(error, 'code', type(error).__name__)})
        raise
