from datetime import datetime, timedelta
from time import time
from zoneinfo import ZoneInfo

from core.product_lines import PRODUCT_LINES
from .schema import ensure_component_schema
from core.logging import smart_log


REFRESH_TIMEZONE = ZoneInfo('Asia/Shanghai')
DAILY_REFRESH_HOUR = 5
REFRESH_ITEMS = (
    {'handler': 'jira', 'card_keys': ('self-test', 'task', 'customer-feedback'),
     'year_periods': ('current', 'previous'),
     'manual_allowed': {'basic': True, 'comment': True, 'verify': False}},
    {'handler': 'confluence',
     'product_spaces': tuple(line.confluence_space_key for line in PRODUCT_LINES),
     'manual_allowed': True},
)


def manual_metric_allowed(card, metric):
    return any(item['manual_allowed'].get(metric, False) for item in REFRESH_ITEMS
               if item['handler'] == 'jira' and card in item['card_keys']
               ) if card else True


def manual_source_allowed(handler):
    return any(item['manual_allowed'] for item in REFRESH_ITEMS if item['handler'] == handler)


def latest_slot(timestamp):
    local = datetime.fromtimestamp(timestamp, REFRESH_TIMEZONE)
    slot = local.replace(hour=DAILY_REFRESH_HOUR, minute=0, second=0, microsecond=0)
    return (slot - timedelta(days=1) if local < slot else slot).timestamp()


class DailyRefreshClock:
    def __init__(self, database, *, now=time):
        self.database, self.now = database, now
        ensure_component_schema(database, component='daily_refresh', version=1, statements=(
            '''CREATE TABLE IF NOT EXISTS web_daily_refresh (
                account TEXT PRIMARY KEY, claimed_slot REAL NOT NULL,
                running INTEGER NOT NULL DEFAULT 0, last_finished REAL,
                error TEXT NOT NULL DEFAULT '')''',
        ))

    def claim(self, accounts):
        slot = latest_slot(self.now())
        claimed = []
        with self.database.transaction() as connection:
            for account in accounts:
                row = connection.execute(
                    'SELECT claimed_slot,running FROM web_daily_refresh WHERE account=?',
                    (account,)).fetchone()
                if row and (row[0] >= slot or row[1]):
                    continue
                connection.execute('''INSERT INTO web_daily_refresh(account,claimed_slot,running)
                    VALUES(?,?,1) ON CONFLICT(account) DO UPDATE SET
                    claimed_slot=excluded.claimed_slot,running=1,error='' ''', (account, slot))
                claimed.append(account)
        return claimed

    def finish(self, account, error=''):
        with self.database.transaction() as connection:
            connection.execute('UPDATE web_daily_refresh SET running=0,last_finished=?,error=? WHERE account=?',
                               (self.now(), error, account))

    def recover(self):
        with self.database.transaction() as connection:
            connection.execute("UPDATE web_daily_refresh SET running=0,error='interrupted' WHERE running=1")


class DailyFactsRefresh:
    def __init__(self, database, sessions, jira_repository, gateway_factory, tasks, confluence):
        self.clock = DailyRefreshClock(database)
        self.sessions, self.repository = sessions, jira_repository
        self.gateway_factory, self.tasks, self.confluence = gateway_factory, tasks, confluence
        self.handlers = {'jira': self._jira, 'confluence': self._confluence}

    def scan(self):
        return [self.tasks.manager.submit_coordinator('daily-facts',
            lambda token, progress, account=account: self.refresh_account(account, token, progress))
            for account in self.clock.claim(self.sessions.saved_accounts())]

    def refresh_account(self, account, token, progress):
        error = ''
        session = None
        try:
            username, password = self.sessions.saved_credentials(account)
            session = self.sessions.create_from_saved(account)
            errors = []
            for item in REFRESH_ITEMS:
                token.raise_if_cancelled()
                try:
                    self.handlers[item['handler']](item, account, password, session, token, progress)
                except Exception as exc:
                    errors.append(getattr(exc, 'code', type(exc).__name__))
                    smart_log('Daily source refresh failed', platform='web', domain='framework', level='ERROR',
                              extra={'source_kind': item['handler'], 'error_type': errors[-1]})
            if errors:
                raise RuntimeError(','.join(errors))
        except Exception as exc:
            error = getattr(exc, 'code', type(exc).__name__)
            raise
        finally:
            if session is not None:
                self.sessions.delete(session)
            self.clock.finish(account, error)

    def _jira(self, item, account, password, session, token, progress):
        from .jira.analytics_service import card_analytics_service
        from .jira.analytics_refresh import refresh_metric
        from core.jira.services.jql_statistics_cards import activity_card_jql_pair, load_activity_qa_roster
        from core.jira.services.filter_service import compose_jql
        submitted = self.repository.history_scopes(account)
        roster = load_activity_qa_roster()
        errors = []
        for card in item['card_keys']:
            # Global facts never replace an unrelated submitted query snapshot.
            scopes = [('', card if submitted.get(card) == '' else 'daily:' + card)]
            if submitted.get(card):
                scopes.append((submitted[card], card))
            for user_jql, storage_key in scopes:
                token.raise_if_cancelled()
                try:
                    filters, gateway = self.gateway_factory(account, password)
                    service = card_analytics_service(filters, gateway, self.repository, self.tasks,
                                                    card, storage_key=storage_key)
                    result = service.search('', account, 0, {'mode': 'advanced', 'jql': user_jql},
                        scheduled=True, cancellation=token, report_progress=progress, periods=item['year_periods'])
                    if not result['validation'].get('valid'):
                        raise RuntimeError('invalid_jql')
                    windows = activity_card_jql_pair(card, '')
                    for period in item['year_periods']:
                        refresh_metric(self.repository, self.tasks.pages, gateway, account, card,
                            compose_jql(user_jql, windows[period]['jql']), roster, 'verify', token, progress)
                except Exception as exc:
                    errors.append(getattr(exc, 'code', type(exc).__name__))
                    smart_log('Daily Jira scope refresh failed', platform='web', domain='jira', level='ERROR',
                        extra={'card_key': card, 'scope_kind': 'global' if not user_jql else 'submitted',
                               'error_type': errors[-1]})
        if errors:
            raise RuntimeError(','.join(errors))

    def _confluence(self, item, account, password, session, token, progress):
        import os
        base = os.getenv('SMARTTEST_CONFLUENCE_BASE_URL', 'https://confluence.amlogic.com')
        access = self.sessions.resource_access(session, f"confluence:{base.rstrip('/').lower()}", self.clock.database)
        self.confluence.refresh(access, password, product_spaces=item['product_spaces'], scheduled=True)
        self.confluence.sync_details(access, password,
            filters={'__product_space__': list(item['product_spaces'])}, force=True,
            cancelled=lambda: token.raise_if_cancelled() or False, progress=progress)
