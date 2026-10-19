"""Pool-pressure regressions without a production database or SQLite fallback."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import json
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import QueuePool

from core.async_tasks import TaskCancelled
from core.jira.services import jql_statistics_cards as cards
from smarttest_web import app as app_module
from smarttest_web.database import WebDatabase
from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
from smarttest_web.session import PersistentSessionStore


class PooledReads:
    """Mock SQL results while retaining real QueuePool and WebDatabase lifetimes."""
    def __init__(self, *, pending=False, with_source=False):
        self.pool = QueuePool(lambda: MagicMock(), pool_size=1, max_overflow=0, timeout=.15)
        self.url = SimpleNamespace(database="isolated-pool-contract")
        self.pending, self.with_source = pending, with_source
        self.fact_reads, self.metadata_connections = [], []
        self.source = "old-source"
        self.removed = set()
        self.metadata = {"annual": True, "availability": {
            year: {"basic": True, "comment": True, "verify": True} for year in ("current", "previous")}}
        if with_source:
            self.metadata["metricSources"] = {"current": {"comment": {"card": "self-test", "query": "source"}}}

    @contextmanager
    def connect(self):
        lease = self.pool.connect()
        connection = MagicMock()
        active = [False]
        def begin():
            active[0] = True
            return nullcontext()
        connection.begin.side_effect = begin
        connection.in_transaction.side_effect = lambda: active[0]
        connection.execute.side_effect = lambda statement, *_args: self.execute(connection, statement)
        try:
            yield connection
        finally:
            lease.close()

    def execute(self, connection, statement):
        sql = str(statement)
        columns = tuple(column.name for column in getattr(statement, "selected_columns", ()))
        if "FROM web_sessions" in sql:
            rows = [("qa", "QA", b"", 0, 99999, None)]
        elif "FROM web_account_identities" in sql:
            rows = [("QA", b"")]
        elif columns == ("statistics_json", "account", "card_key", "roster_fingerprint", "state"):
            self.metadata_connections.append(connection)
            rows = [(json.dumps(self.metadata), "qa", "self-test", "definition", "pending" if self.pending else "active")]
        elif columns == ("active_snapshot_id",):
            rows = [] if self.pending else [("snapshot",)]
        elif "SELECT s.snapshot_id FROM" in sql:
            rows = [(self.source,)]
        elif columns == ("snapshot_id",):
            identities = next(iter(statement.compile().params.values()))
            rows = [(identity,) for identity in identities if identity not in self.removed]
        elif columns == ("fact_json",):
            if getattr(self, "fact_error", None) is not None:
                raise self.fact_error
            identity = next(iter(statement.compile().params.values()))
            self.fact_reads.append((identity, connection))
            rows = []
        else:
            raise AssertionError("Unexpected database operation in isolated pool test")
        result = MagicMock()
        result.fetchone.return_value = result.first.return_value = rows[0] if rows else None
        result.__iter__.return_value = iter(rows)
        return result

    def dispose(self):
        self.pool.dispose()


def setup_app(monkeypatch, tmp_path, engine, *, cache=None):
    database = WebDatabase.__new__(WebDatabase)
    database._key, database._engine = object(), engine
    sessions = PersistentSessionStore(database=database, now=lambda: 0)
    sessions._credentials["qa"] = "test-password"
    pair = cards.effective_card_jql_pair("self-test", "", "year")
    fingerprint = cards.card_definition("self-test").fingerprint + ":annual-metrics-v5"
    monkeypatch.setattr(app_module, "verify_schema_revision", lambda _: None)
    monkeypatch.setattr(JiraAnalyticsRepository, "interrupt_pending", lambda _: None)
    monkeypatch.setattr(JiraAnalyticsRepository, "state", lambda *_args, **_kwargs: {
        "activeSnapshotId": "snapshot", "pendingSnapshotId": "", "taskId": "",
        "activeJql": pair["current"]["jql"], "activeComparisonJql": pair["previous"]["jql"],
        "userJql": "", "rosterFingerprint": fingerprint, "latestState": "active", "error": ""})
    monkeypatch.setattr(JiraAnalyticsRepository, "published_conditions", lambda *_args: {"userJql": ""})
    monkeypatch.setattr(JiraAnalyticsRepository, "statistics_preview", lambda *_args, **_kwargs: None)
    def no_remote(*_args):
        pytest.fail("GET statistics must never resolve a remote gateway")
    app = app_module.create_app(database=database, session_store=lambda **_kwargs: sessions,
        authenticator=lambda: object(), jira_filter_owner=no_remote, reports_root=tmp_path,
        statistics_cache=cache, statistics_cache_ttl_seconds=60 if cache else None)
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    client.cookies.set(app_module.SESSION_COOKIE, "test-session")
    return client, database


@pytest.mark.parametrize("cached", [False, True])
def test_concurrent_statistics_aggregation_does_not_starve_actual_auth_dependency(monkeypatch, tmp_path, cached):
    engine = PooledReads()
    entered, release, lock = Event(), Event(), Lock()
    count = [0]
    aggregate = cards.aggregate_annual_facts
    def blocked(facts, metadata, period, **kwargs):
        assert engine.pool.checkedout() == 0
        if period == "week":
            with lock:
                count[0] += 1
                if count[0] == 2:
                    entered.set()
            assert release.wait(3)
        return aggregate(facts, metadata, period, **kwargs)
    monkeypatch.setattr(cards, "aggregate_annual_facts", blocked)
    class Cache:
        def get_or_load(self, _identity, loader, **_kwargs):
            assert engine.pool.checkedout() == 0
            return loader()
    client, database = setup_app(monkeypatch, tmp_path, engine, cache=Cache() if cached else None)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            requests = [workers.submit(client.get, "/api/jira/cards/self-test/statistics") for _ in range(2)]
            try:
                assert entered.wait(3)
                # This goes through create_app.authenticated_session/current_session/sessions.get.
                dependency_probe = client.get("/api/jira/cards/self-test/tasks/not-owned")
                assert dependency_probe.status_code == 404
                auth = client.get("/api/auth/session")
                assert auth.status_code == 200 and auth.json()["authenticated"] is True
            finally:
                release.set()
            assert all(response.result(timeout=5).status_code == 200 for response in requests)
        assert engine.pool.checkedout() == 0
    finally:
        client.close()
        database.close()


@pytest.mark.parametrize("error", [ValueError("failed"), TaskCancelled()])
@pytest.mark.parametrize("phase", ["read", "aggregate"])
def test_statistics_exception_or_cancellation_returns_connection_before_auth(monkeypatch, tmp_path, error, phase):
    engine = PooledReads()
    if phase == "read":
        engine.fact_error = error
    def fail(*_args, **_kwargs):
        assert engine.pool.checkedout() == 0
        raise error
    monkeypatch.setattr(cards, "aggregate_annual_facts", fail)
    client, database = setup_app(monkeypatch, tmp_path, engine)
    try:
        assert client.get("/api/jira/cards/self-test/statistics").status_code == 500
        assert engine.pool.checkedout() == 0
        assert client.get("/api/auth/session").status_code == 200
    finally:
        client.close()
        database.close()


def test_cache_hit_has_no_fact_reads_or_checked_out_connection():
    engine = PooledReads()
    database = WebDatabase.__new__(WebDatabase)
    database._key, database._engine = object(), engine
    saved = {"saved": True}
    class Cache:
        def get_or_load(self, _identity, _loader, **_kwargs):
            assert engine.pool.checkedout() == 0
            return saved
    try:
        result = JiraAnalyticsRepository(database, statistics_cache=Cache(), statistics_cache_ttl_seconds=60).local_statistics("snapshot", "month")
        assert result is saved and engine.fact_reads == []
    finally:
        database.close()


def test_cache_miss_anchors_immutable_sources_before_releasing_metadata_connection():
    engine = PooledReads(with_source=True)
    database = WebDatabase.__new__(WebDatabase)
    database._key, database._engine = object(), engine
    class Cache:
        def get_or_load(self, _identity, loader, **_kwargs):
            assert engine.pool.checkedout() == 0
            engine.source = "new-source"
            return loader()
    try:
        result = JiraAnalyticsRepository(database, statistics_cache=Cache(), statistics_cache_ttl_seconds=60).local_statistics("snapshot", "month")
        assert result["teamTotal"] == 0
        assert [identity for identity, _connection in engine.fact_reads] == ["snapshot", "old-source"]
    finally:
        database.close()


def test_pending_facts_are_extracted_in_the_same_consistent_read_before_aggregation(monkeypatch):
    engine = PooledReads(pending=True)
    database = WebDatabase.__new__(WebDatabase)
    database._key, database._engine = object(), engine
    aggregate = cards.aggregate_annual_facts
    def verify(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        assert engine.fact_reads[0][1] is engine.metadata_connections[0]
        return aggregate(*args, **kwargs)
    monkeypatch.setattr(cards, "aggregate_annual_facts", verify)
    class NoCache:
        def get_or_load(self, *_args, **_kwargs):
            pytest.fail("pending facts must not enter shared cache")
    try:
        result = JiraAnalyticsRepository(database, statistics_cache=NoCache(), statistics_cache_ttl_seconds=60).local_statistics("snapshot", "month")
        assert result["teamTotal"] == 0
    finally:
        database.close()


@pytest.mark.parametrize("removed", ["snapshot", "old-source"])
def test_deleted_anchored_snapshot_is_not_cached_as_a_valid_empty_result(removed):
    engine = PooledReads(with_source=True)
    database = WebDatabase.__new__(WebDatabase)
    database._key, database._engine = object(), engine
    from smarttest_web.cache import RedisCache
    cache = RedisCache.__new__(RedisCache)
    cache._client = MagicMock()
    def miss(_key):
        assert engine.pool.checkedout() == 0
        engine.removed.add(removed)
        return None
    cache._client.get.side_effect = miss
    try:
        repository = JiraAnalyticsRepository(database, statistics_cache=cache, statistics_cache_ttl_seconds=60)
        with pytest.raises(LookupError, match="query_snapshot_unavailable"):
            repository.local_statistics("snapshot", "month")
        cache._client.set.assert_not_called()
        assert engine.fact_reads == []
        assert engine.pool.checkedout() == 0
    finally:
        database.close()
