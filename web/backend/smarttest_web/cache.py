"""Rebuildable JSON cache; callers own authorization and dependency versions."""
from dataclasses import dataclass
from hashlib import sha256
import json

from redis import Redis
from redis.exceptions import RedisError
from redis.backoff import NoBackoff
from redis.retry import Retry

from core.logging import smart_log
from .config import RedisCacheSettings


@dataclass(frozen=True)
class CacheIdentity:
    format_version: int
    namespace: str
    scope: str
    dependency_revision: str
    dimensions: tuple[tuple[str, str], ...]
    time_boundary: str

    def __post_init__(self):
        object.__setattr__(self, 'dimensions', tuple(sorted(tuple(pair) for pair in self.dimensions)))
        if self.format_version < 1 or not self.namespace or not self.scope or not self.dependency_revision:
            raise ValueError('Cache identity requires a format, namespace, scope and dependency revision')
        if len(dict(self.dimensions)) != len(self.dimensions):
            raise ValueError('Cache dimensions must have unique names')


class RedisCache:
    def __init__(self, settings: RedisCacheSettings):
        self._client = Redis(host=settings.host, port=settings.port, db=settings.database,
            username=settings.username, password=settings.password, max_connections=settings.max_connections,
            socket_timeout=settings.socket_timeout_seconds,
            socket_connect_timeout=settings.connect_timeout_seconds, retry=Retry(NoBackoff(), 0))

    def get_or_load(self, identity: CacheIdentity, loader, *, ttl_seconds: int):
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError('Cache TTL must be a positive integer')
        encoded = json.dumps([identity.format_version, identity.namespace, identity.scope,
            identity.dependency_revision, identity.dimensions, identity.time_boundary],
            ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        key = 'smarttest:cache:' + sha256(encoded.encode('utf-8')).hexdigest()
        try:
            raw = self._client.get(key)
        except RedisError as error:
            self._record('read_failed', error)
        else:
            if raw is not None:
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError) as error:
                    self._record('decode_failed', error)
                else:
                    self._record('hit')
                    return value
        self._record('miss')
        value = loader()
        payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        try:
            self._client.set(key, payload, ex=ttl_seconds)
        except RedisError as error:
            self._record('write_failed', error)
        return value

    @staticmethod
    def _record(event, error=None):
        smart_log('Redis cache ' + event, platform='web', domain='storage', source='RedisCache',
            level='warning' if error else 'debug',
            extra={'event': event, **({'exception_type': type(error).__name__} if error else {})},
            emit_runtime_event=False)

    def close(self):
        self._client.close()
