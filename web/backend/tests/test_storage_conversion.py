"""Business contracts on an explicitly configured isolated MySQL schema."""
import base64
from concurrent.futures import ThreadPoolExecutor
import importlib
import os
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError

from smarttest_web.config import ApplicationDatabaseSettings
from smarttest_web.database import APPLICATION_METADATA, WebDatabase
from smarttest_web import schema


def test_target_schema_declares_all_persistent_owners():
    assert len(schema.application_metadata().tables) == 38
    assert 'smarttest_schema' not in APPLICATION_METADATA.tables
    assert 'web_daily_refresh' in APPLICATION_METADATA.tables
    assert APPLICATION_METADATA.tables['jira_analytics_facts'].c.sequence is not None




def test_mysql_initial_revision_roundtrip_and_no_schema_drift(application_database):
    from alembic import command
    from alembic.config import Config
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    for operation, revision in ((command.downgrade, 'base'), (command.upgrade, 'head')):
        with application_database.transaction() as connection:
            config.attributes['connection'] = connection
            operation(config, revision)
    with application_database.transaction() as connection:
        config.attributes['connection'] = connection
        command.check(config)
    schema.verify_schema_revision(application_database)


def test_mysql_limits_case_trailing_spaces_and_alternate_unique_conflict(application_database):
    from smarttest_web.jira.issue_repository import JiraIssueRepository
    from smarttest_web.schema import user_preferences
    from test_jira_issue_repository import _issue
    from dataclasses import replace
    from core.jira.domain import IssueIdentity, IssueDetails
    database = application_database
    with database.connect() as connection:
        assert 'STRICT_ALL_TABLES' in connection.execute(text('SELECT @@sql_mode')).scalar_one()
        assert len(inspect(connection).get_table_names()) == 39
    with pytest.raises(DataError):
        with database.transaction() as connection:
            connection.execute(user_preferences.insert().values(username='u', scope='s', key='x' * 192,
                value_json='{}', schema_version=1, updated_at=1))
    with database.transaction() as connection:
        for key in ('A', 'a', 'a '):
            connection.execute(user_preferences.insert().values(username='u', scope='s', key=key,
                value_json='null', schema_version=1, updated_at=1))
    with database.connect() as connection:
        assert connection.execute(select(user_preferences.c.key).order_by(user_preferences.c.key)).scalars().all() == ['A', 'a', 'a ']
    from smarttest_web.schema import web_resource_access
    with database.transaction() as connection:
        maximum = {'account': '🙂' * 128, 'platform': '🙂' * 160, 'kind': '🙂' * 32,
                   'resource_id': '🙂' * 191, 'capability': '🙂' * 32, 'scope': '🙂' * 224, 'confirmed_at': 1}
        connection.execute(web_resource_access.insert().values(maximum))
        connection.execute(web_resource_access.insert().values({**maximum, 'scope': '🙂' * 223 + 'a'}))
        assert connection.execute(select(web_resource_access.c.scope)).scalars().all() == ['🙂' * 223 + 'a', '🙂' * 224]
    repository = JiraIssueRepository(database)
    original = _issue()
    repository.save_core((original,))
    conflict = replace(original, identity=IssueIdentity('different-id', original.identity.key, original.identity.web_url), summary='must rollback')
    with pytest.raises(IntegrityError):
        repository.save_core((conflict,))
    assert repository.get(original.identity.key, IssueDetails()).summary == original.summary


def test_mysql_release_queries_keep_authorized_scope(application_database):
    from test_confluence_repository import _project
    from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
    from smarttest_web.release_query import ProjectReleaseQueryService
    repository = ConfluenceProjectRepository(application_database)
    repository.save_core((_project(),))
    service = ProjectReleaseQueryService(application_database)
    result = service.dashboard(visible_ids=['900'])
    assert len(result['releases']) == 1
    assert result['releases'][0]['projectId'] == 'P100'
    assert service.dashboard(visible_ids=[])['releases'] == []


def test_mysql_claims_and_revision_are_atomic(application_database):
    from smarttest_web.audit.email_history import AuditEmailHistory
    from smarttest_web.daily_refresh import DailyRefreshClock
    from smarttest_web.test_suite_repository import TestSuiteRepository, RevisionConflictError
    database = application_database
    def race(operation):
        barrier = Barrier(2)
        def run():
            barrier.wait(timeout=5)
            return operation()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda _: run(), range(2)))
    assert sorted(race(lambda: AuditEmailHistory(database).claim_scheduled_occurrence('owner', 'week'))) == [False, True]
    assert sum(bool(value) for value in race(lambda: DailyRefreshClock(database).claim(['owner']))) == 1
    repository = TestSuiteRepository(database)
    suite = repository.create(owner_username='owner', owner_display_name='Owner', name='suite', description='',
                              visibility='private', ordered_nodeids=['case'])
    def update():
        try:
            repository.update(suite.id, owner_username='owner', revision=1, name='suite', description='', visibility='private', ordered_nodeids=['case'])
            return True
        except RevisionConflictError:
            return False
    assert sorted(race(update)) == [False, True]


def test_mysql_snapshot_competition_and_insertion_order(application_database, monkeypatch):
    from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
    from smarttest_web.jira.schema import jira_analytics_queries
    from sqlalchemy import update
    repository = JiraAnalyticsRepository(application_database, now=lambda: 10)
    old = repository.begin('s', 'alice', 'old', {}, '', expires_at=100)
    new = repository.begin('s', 'alice', 'new', {}, '', expires_at=100)
    repository.update_statistics(new, {'annual': True}, facts=[{'issueId': 'z'}, {'issueId': 'a'}])
    repository.update_statistics(new, {'annual': True}, facts=[{'issueId': 'z'}, {'issueId': 'm'}])
    monkeypatch.setattr('core.jira.services.jql_statistics_cards.aggregate_annual_facts',
                        lambda facts, metadata, period, today=None: [fact['issueId'] for fact in facts])
    assert repository.local_statistics(new, 'year') == ['z', 'a', 'm']
    barrier = Barrier(2)
    def activate(snapshot):
        barrier.wait(timeout=5)
        return repository.activate(snapshot, {'annual': True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(activate, [old, new])) == [False, True]
    failed = repository.begin('s', 'alice', 'last', {}, '', expires_at=100)
    repository.finish(failed, 'failed', 'latest')
    with application_database.transaction() as connection:
        connection.execute(update(jira_analytics_queries).where(jira_analytics_queries.c.session_hash == 's').values(last_snapshot_id=''))
    assert repository.state('s', 'alice')['error'] == 'latest'


def test_mysql_app_session_credentials_snapshots_and_lifespan(application_database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from smarttest_web.app import create_app
    from smarttest_web.credentials import LinuxEncryptedCredentialStore
    from smarttest_web.session import PersistentSessionStore
    from smarttest_web.query_snapshot_repository import ConfluenceQuerySnapshotRepository
    from smarttest_web.jira_filter_snapshot_repository import JiraFilterSnapshotRepository
    from test_jira_analytics_api import FilterOwner, Gateway
    database = application_database
    credential_environment = {'SMARTTEST_WEB_CREDENTIAL_KEY': base64.urlsafe_b64encode(bytes(range(32))).decode()}
    def sessions(database):
        return PersistentSessionStore(database=database,
            credential_store=LinuxEncryptedCredentialStore(database=database, environ=credential_environment))
    class Authenticator:
        def authenticate(self, username, password):
            return {'success': True, 'username': username, 'display_name': username.title(), 'avatar_bytes': b'avatar'}
    cache = None
    cache_reads = []
    if os.environ.get('SMARTTEST_TEST_REDIS_HOST'):
        from smarttest_web.cache import RedisCache
        from smarttest_web.config import RedisCacheSettings
        environment = {name.replace('SMARTTEST_TEST_REDIS_', 'SMARTTEST_REDIS_'): value
            for name, value in os.environ.items() if name.startswith('SMARTTEST_TEST_REDIS_')}
        cache = RedisCache(RedisCacheSettings.from_environment(environment))
        original_get = cache.get_or_load
        def record_get(*args, **kwargs):
            cache_reads.append(1)
            return original_get(*args, **kwargs)
        monkeypatch.setattr(cache, 'get_or_load', record_get)
    app = create_app(database=database, reports_root=tmp_path / 'reports', session_store=sessions,
        statistics_cache=cache, statistics_cache_ttl_seconds=300 if cache else None,
        authenticator=Authenticator, project_facts_owner=Mock, facts_refresh=Mock,
        jira_filter_owner=lambda *_: (FilterOwner(), Gateway()))
    app.state.daily_refresh.scan = lambda: []
    with TestClient(app, base_url='https://testserver') as client:
        assert client.get('/health').status_code == 200
        assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'synthetic-secret'}).status_code == 200
        token = client.cookies.get('smarttest_session')
        store = sessions(database)
        assert store.get(token).password == 'synthetic-secret'
        access = store.resource_access(token, 'confluence:https://example.test', database)
        access.publish([('project', 'P100', 'catalog', 'TV')], lambda: None)
        assert access.ids('project', 'catalog') == {'P100'}
        bob = store.create('bob', 'another-synthetic-secret')
        assert store.resource_access(bob, 'confluence:https://example.test', database).ids('project', 'catalog') == set()
        hashed = store._hash(token)
        snapshots = ConfluenceQuerySnapshotRepository(database)
        snapshots.record(hashed, {}, '', ['P100'], 'revision', expires_at=store.get(token).expires_at)
        assert snapshots.get(hashed).project_ids == ('P100',)
        assert snapshots.get('another-session') is None
        filters = JiraFilterSnapshotRepository(database)
        filters.record(hashed, {'project': ['SH']}, '', expires_at=store.get(token).expires_at)
        assert filters.get(hashed).filters == {'project': ['SH']}
        assert client.post('/api/jira/analytics/search', json={'mode': 'advanced', 'jql': 'project = SH'}).status_code == 200
        queried = client.post('/api/jira/cards/task/query', json={'period': 'year'}).json()
        import time
        for _ in range(200):
            task = client.get(f"/api/jira/cards/task/tasks/{queried['taskId']}").json()
            if task['state'] not in {'queued', 'running'}:
                break
            time.sleep(.01)
        assert task['state'] == 'completed'
        statistics = client.get('/api/jira/cards/task/statistics').json()
        assert tuple(statistics['periods']) == ('week', 'month', 'quarter', 'year')
        if cache is not None:
            assert cache_reads == []  # Fake Gateway has not published verification facts.
            from core.jira.services.jql_statistics_cards import load_activity_qa_roster
            from smarttest_web.jira.analytics_refresh import metric_source_key
            from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
            with database.connect() as connection:
                stored = connection.execute(text("SELECT s.statistics_json FROM jira_analytics_queries q "
                    "JOIN jira_analytics_snapshots s ON s.snapshot_id=q.active_snapshot_id "
                    "WHERE q.account='alice' AND q.card_key='task'")).scalar_one()
            import json
            metadata = json.loads(stored)
            repository = JiraAnalyticsRepository(database)
            for sources in metadata['metricSources'].values():
                source = sources['verify']
                key = metric_source_key(source['card'], source['query'], load_activity_qa_roster().fingerprint, 'verify')
                complete = repository.begin('', 'alice', 'synthetic local verification', {}, '', card_key=key, expires_at=100)
                assert repository.activate(complete, {})
            statistics = client.get('/api/jira/cards/task/statistics').json()
            assert client.get('/api/jira/cards/task/statistics').json() == statistics
            assert len(cache_reads) >= 2
        assert client.get('/api/audit-email/history/jira').json() == {'rows': [], 'total': 0}
        assert client.post('/api/auth/logout').status_code == 200
        assert store.get(token) is None
        with pytest.raises(PermissionError):
            access.require_active()
        restored = sessions(database).create_from_saved('alice')
        assert sessions(database).get(restored).password == 'synthetic-secret'
        other = sessions(database).create_from_saved('alice')
        store.invalidate_credentials('alice')
        assert sessions(database).get(restored) is None
        assert sessions(database).get(other) is None
        from smarttest_web.credentials import CredentialMissingError
        with pytest.raises(CredentialMissingError):
            sessions(database).create_from_saved('alice')
        store.delete(restored)
        store.cleanup()


def test_mysql_expired_session_obeys_outer_transaction_rollback(application_database):
    from smarttest_web.session import PersistentSessionStore
    clock = [1000.0]
    store = PersistentSessionStore(database=application_database, ttl_seconds=100, now=lambda: clock[0])
    token = store.create('coco', 'secret')
    clock[0] += 101
    with pytest.raises(RuntimeError, match='abort'):
        with application_database.transaction():
            assert store.get(token) is None
            raise RuntimeError('abort')
    clock[0] -= 101
    assert store.get(token).username == 'coco'


def test_mysql_statistics_real_redis_versions_expiry_and_outage(application_database, monkeypatch):
    from dataclasses import replace
    from datetime import date
    import time
    from types import SimpleNamespace
    from smarttest_web.cache import RedisCache
    from smarttest_web.config import RedisCacheSettings
    from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
    from smarttest_web.jira.analytics_refresh import metric_source_key
    from core.jira.services import jql_statistics_cards as cards
    if not os.environ.get('SMARTTEST_TEST_REDIS_HOST'):
        pytest.skip('Real Redis required')
    env = {name.replace('SMARTTEST_TEST_REDIS_', 'SMARTTEST_REDIS_'): value
           for name, value in os.environ.items() if name.startswith('SMARTTEST_TEST_REDIS_')}
    settings = RedisCacheSettings.from_environment(env)
    with application_database.connect() as connection:
        assert connection.execute(text('SELECT @@transaction_isolation')).scalar_one() == 'REPEATABLE-READ'
    cache = RedisCache(settings)
    keys = []
    original_set = cache._client.set
    def record_set(key, value, **kwargs):
        keys.append(key)
        return original_set(key, value, **kwargs)
    monkeypatch.setattr(cache._client, 'set', record_set)
    monkeypatch.setattr(cards, 'load_activity_qa_roster', lambda: SimpleNamespace(fingerprint='roster-1'))
    today = [date(2026, 10, 16)]
    repo = JiraAnalyticsRepository(application_database, statistics_cache=cache,
        statistics_cache_ttl_seconds=1, today=lambda: today[0])
    calls = []
    aggregate = cards.aggregate_annual_facts
    def count(*args, **kwargs):
        calls.append(1)
        return aggregate(*args, **kwargs)
    monkeypatch.setattr(cards, 'aggregate_annual_facts', count)
    def publish(key, metadata):
        snapshot = repo.begin('s', 'alice', 'local', {}, '', expires_at=100, card_key=key)
        assert repo.update_statistics(snapshot, metadata, facts=[])
        assert repo.activate(snapshot, metadata)
        return snapshot
    source = {'card': 'task', 'query': 'local'}
    for metric in ('comment', 'verify'):
        publish(metric_source_key('task', 'local', 'roster-1', metric), {})
    metadata = {'annual': True, 'metricSources': {'current': {'comment': source, 'verify': source}},
        'availability': {year: {'basic': True, 'comment': True, 'verify': True} for year in ('current', 'previous')}}
    snapshot = publish('task', metadata)
    try:
        expected = repo.local_statistics(snapshot, 'year')
        assert repo.local_statistics(snapshot, 'year') == expected and len(calls) == 1
        publish(metric_source_key('task', 'local', 'roster-1', 'comment'), {})
        assert repo.local_statistics(snapshot, 'year') == expected and len(calls) == 2
        today[0] = date(2026, 10, 17)
        dated = repo.local_statistics(snapshot, 'year')
        assert len(calls) == 3
        time.sleep(1.05)
        assert repo.local_statistics(snapshot, 'year') == dated and len(calls) == 4
        offline = RedisCache(replace(settings, port=1, connect_timeout_seconds=0.05, socket_timeout_seconds=0.05))
        try:
            uncached = JiraAnalyticsRepository(application_database, statistics_cache=offline,
                statistics_cache_ttl_seconds=1, today=lambda: today[0])
            assert uncached.local_statistics(snapshot, 'year') == dated and len(calls) == 5
        finally:
            offline.close()
        partial = repo.begin('s', 'alice', 'pending', {}, '', expires_at=100, card_key='task')
        assert repo.update_statistics(partial, metadata, facts=[])
        repo.local_statistics(partial, 'year')
        repo.local_statistics(partial, 'year')
        assert len(calls) == 7
        activity = repo.begin('', 'alice', 'pending activity', {}, '', expires_at=100, card_key='activity')
        assert repo.update_statistics(activity, {'queryRevision': 'r'}, facts=[])
        legacy = {'annual': True, 'activityRoster': 'r',
            'activitySources': {'current': {'snapshotId': activity, 'revision': 'r'}},
            'availability': {year: {'basic': True, 'activity': True} for year in ('current', 'previous')}}
        main = publish('legacy', legacy)
        before = len(keys)
        repo.local_statistics(main, 'year')
        repo.local_statistics(main, 'year')
        assert len(calls) == 9 and len(keys) == before  # A pinned pending dependency is still mutable.
    finally:
        if keys:
            cache._client.delete(*keys)
        cache.close()
