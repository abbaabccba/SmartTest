from dataclasses import replace
from unittest.mock import Mock

import pytest

from smarttest_web.config import ApplicationDatabaseSettings, ConfigurationError, RedisCacheSettings
from smarttest_web.cache import CacheIdentity, RedisCache


def test_application_settings_are_separate_and_do_not_reveal_password():
    env = {'SMARTTEST_DB_HOST': 'localhost', 'SMARTTEST_DB_USER': 'app',
           'SMARTTEST_DB_PASSWORD': 'private/@secret', 'SMARTTEST_DB_NAME': 'application'}
    settings = ApplicationDatabaseSettings.from_environment(env)
    assert settings.port == 3306 and settings.pool_size == 5
    assert 'private/@secret' not in repr(settings)
    assert settings.url.password == env['SMARTTEST_DB_PASSWORD']
    assert settings.url.drivername == 'mysql+mysqlconnector'
    with pytest.raises(ConfigurationError, match='SMARTTEST_DB_HOST'):
        ApplicationDatabaseSettings.from_environment({'WIFI_DB_HOST': 'other'})


def test_redis_settings_bound_waits_and_hide_credentials():
    settings = RedisCacheSettings.from_environment({'SMARTTEST_REDIS_HOST': 'localhost',
        'SMARTTEST_REDIS_PASSWORD': 'private-cache-secret'})
    assert settings.socket_timeout_seconds == 1 and settings.max_connections == 10
    assert 'private-cache-secret' not in repr(settings)
    with pytest.raises(ConfigurationError, match='SMARTTEST_REDIS_PORT'):
        RedisCacheSettings.from_environment({'SMARTTEST_REDIS_HOST': 'localhost', 'SMARTTEST_REDIS_PORT': '0'})


def identity(**kwargs):
    return CacheIdentity(format_version=1, namespace='aggregate', scope='account-hash',
        dependency_revision='snapshot-1:metric-2', dimensions=(('period', 'month'),),
        time_boundary='2026-10-16', **kwargs)


@pytest.fixture
def cache(monkeypatch):
    client = Mock()
    client.get.return_value = None
    monkeypatch.setattr('smarttest_web.cache.Redis', Mock(return_value=client))
    return RedisCache(RedisCacheSettings.from_environment({'SMARTTEST_REDIS_HOST': 'localhost'})), client


def test_cache_identity_is_stable_complete_and_opaque(cache):
    owner, client = cache
    first = identity()
    for current in (first, replace(first, scope='another'), replace(first, dependency_revision='new'),
                    replace(first, time_boundary='next'), replace(first, dimensions=(('period', 'year'),)),
                    replace(first, namespace='other'), replace(first, format_version=2)):
        owner.get_or_load(current, lambda: {'count': 1}, ttl_seconds=30)
    keys = [call.args[0] for call in client.get.call_args_list]
    assert len(set(keys)) == 7
    assert all('account-hash' not in key and 'snapshot-1' not in key for key in keys)
    reordered = CacheIdentity(1, 'aggregate', 'account-hash', 'snapshot-1:metric-2',
                              (('b', '2'), ('a', '1')), '2026-10-16')
    owner.get_or_load(reordered, lambda: {}, ttl_seconds=30)
    owner.get_or_load(replace(reordered, dimensions=tuple(reversed(reordered.dimensions))), lambda: {}, ttl_seconds=30)
    assert client.get.call_args_list[-1] == client.get.call_args_list[-2]


def test_cache_hit_miss_ttl_and_close(cache):
    owner, client = cache
    loader = Mock(return_value={'count': 2})
    assert owner.get_or_load(identity(), loader, ttl_seconds=30) == {'count': 2}
    assert client.set.call_args.kwargs['ex'] == 30
    client.get.return_value = b'{"count":2}'
    assert owner.get_or_load(identity(), loader, ttl_seconds=30) == {'count': 2}
    loader.assert_called_once()
    owner.close()
    client.close.assert_called_once()


def test_cache_connection_budget_disables_hidden_retries(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr('smarttest_web.cache.Redis', constructor)
    cache = RedisCache(RedisCacheSettings(host='localhost', socket_timeout_seconds=.2,
                                        connect_timeout_seconds=.3, max_connections=2))
    kwargs = constructor.call_args.kwargs
    assert kwargs['socket_timeout'] == .2 and kwargs['socket_connect_timeout'] == .3
    assert kwargs['max_connections'] == 2
    attempts = []
    from redis.exceptions import ConnectionError
    def unavailable():
        attempts.append(1)
        raise ConnectionError('unavailable')
    with pytest.raises(ConnectionError):
        kwargs['retry'].call_with_retry(unavailable, lambda error: None)
    assert len(attempts) == 1
    cache.close()


def test_cache_failures_only_fall_back_at_cache_boundary(cache, monkeypatch, tmp_path):
    from redis.exceptions import ConnectionError
    monkeypatch.setenv('SMARTTEST_LOG_DIR', str(tmp_path))
    owner, client = cache
    for failure in (ConnectionError('PRIVATE ENDPOINT'), None):
        client.get.side_effect = failure
        client.get.return_value = b'invalid-json'
        client.set.side_effect = ConnectionError('PRIVATE ENDPOINT')
        assert owner.get_or_load(identity(), lambda: {'same': 'version'}, ttl_seconds=30) == {'same': 'version'}
    assert 'PRIVATE ENDPOINT' not in (tmp_path / 'smarttest.log').read_text(encoding='utf-8')
    client.get.side_effect = None
    client.get.return_value = None
    with pytest.raises(ConnectionError, match='business loader'):
        owner.get_or_load(identity(), Mock(side_effect=ConnectionError('business loader')), ttl_seconds=30)
    with pytest.raises(TypeError):
        owner.get_or_load(identity(), lambda: object(), ttl_seconds=30)
    with pytest.raises(ValueError):
        owner.get_or_load(identity(), lambda: {}, ttl_seconds=0)
