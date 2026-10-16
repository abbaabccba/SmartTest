"""Real Redis validation uses only keys created by this test."""
from dataclasses import replace
import os
from uuid import uuid4

import pytest

from smarttest_web.cache import CacheIdentity, RedisCache
from smarttest_web.config import RedisCacheSettings


def test_real_redis_json_hit_version_isolation_expiry_and_reconstruction(monkeypatch):
    if not os.environ.get('SMARTTEST_TEST_REDIS_HOST'):
        pytest.skip('Set SMARTTEST_TEST_REDIS_* for an isolated Redis instance')
    env = {name.replace('SMARTTEST_TEST_REDIS_', 'SMARTTEST_REDIS_'): value
           for name, value in os.environ.items() if name.startswith('SMARTTEST_TEST_REDIS_')}
    cache = RedisCache(RedisCacheSettings.from_environment(env))
    keys = []
    original_set = cache._client.set
    def record_set(key, value, **kwargs):
        keys.append(key)
        return original_set(key, value, **kwargs)
    monkeypatch.setattr(cache._client, 'set', record_set)
    identity = CacheIdentity(1, 'foundation-test', uuid4().hex, 'revision-1', (), 'fixed')
    calls = []
    def load():
        calls.append(1)
        return {'total': len(calls), 'label': '中文'}
    try:
        assert cache.get_or_load(identity, load, ttl_seconds=60)['total'] == 1
        assert cache.get_or_load(identity, load, ttl_seconds=60)['total'] == 1
        assert cache._client.ttl(keys[0]) in range(1, 61)
        assert cache.get_or_load(replace(identity, dependency_revision='revision-2'), load, ttl_seconds=60)['total'] == 2
        assert cache.get_or_load(replace(identity, scope=uuid4().hex), load, ttl_seconds=60)['total'] == 3
        cache._client.expire(keys[0], 0)
        assert cache.get_or_load(identity, load, ttl_seconds=60)['total'] == 4
    finally:
        if keys:
            cache._client.delete(*keys)
        cache.close()
