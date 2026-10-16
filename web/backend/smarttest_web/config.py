from __future__ import annotations

import os
import sys
from pathlib import Path
from math import isfinite
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy.engine import URL
from core.config.jsonTool import app_data_dir, read_json
from core.credentials.windows import WindowsCredentialStore


class ConfigurationError(RuntimeError):
    pass


def storage_environment(environment=os.environ, *, config_path='web/storage.json',
                        platform_name=sys.platform, credential_store=None):
    """Resolve non-secret deployment settings and explicit platform secret references."""
    stored = read_json(config_path, {})
    if not isinstance(stored, dict) or any('PASSWORD' in key for key in stored):
        raise ConfigurationError('Storage configuration must contain only non-secret settings')
    resolved = {**stored, **environment}
    for prefix in ('SMARTTEST_DB', 'SMARTTEST_REDIS'):
        reference = resolved.get(prefix + '_CREDENTIAL_REF')
        if reference and not environment.get(prefix + '_PASSWORD'):
            if platform_name != 'win32':
                raise ConfigurationError('Windows storage credential references require Windows')
            owner = credential_store or WindowsCredentialStore(target_prefix='SmartTest/WebStorage/')
            _username, password = owner.read(reference)
            resolved[prefix + '_PASSWORD'] = password
    return resolved


def statistics_cache_ttl_seconds(environment):
    return _number(environment, 'SMARTTEST_STATISTICS_CACHE_TTL_SECONDS', 300)


def reports_root(environment):
    return Path(environment.get('SMARTTEST_REPORTS_ROOT') or app_data_dir() / 'web' / 'audit-email-reports')


def _required(environment, name):
    if not environment.get(name):
        raise ConfigurationError(f'Missing environment variable: {name}')
    return environment[name]


def _number(environment, name, default, *, integer=True, minimum=1, maximum=None):
    try:
        value = (int if integer else float)(environment.get(name, str(default)))
        if not isfinite(value) or not minimum <= value or (maximum is not None and value > maximum):
            raise ValueError()
    except (ValueError, TypeError):
        raise ConfigurationError(f'Invalid environment variable: {name}') from None
    return value


@dataclass(frozen=True)
class ApplicationDatabaseSettings:
    host: str
    user: str
    password: str = field(repr=False)
    database: str
    port: int = 3306
    pool_size: int = 5
    max_overflow: int = 0
    pool_timeout_seconds: float = 5
    connect_timeout_seconds: int = 5

    @property
    def url(self):
        return URL.create('mysql+mysqlconnector', username=self.user, password=self.password,
                          host=self.host, port=self.port, database=self.database,
                          query={'charset': 'utf8mb4'})

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] = os.environ):
        return cls(host=_required(environment, 'SMARTTEST_DB_HOST'),
            user=_required(environment, 'SMARTTEST_DB_USER'),
            password=_required(environment, 'SMARTTEST_DB_PASSWORD'),
            database=_required(environment, 'SMARTTEST_DB_NAME'),
            port=_number(environment, 'SMARTTEST_DB_PORT', 3306, maximum=65535),
            pool_size=_number(environment, 'SMARTTEST_DB_POOL_SIZE', 5),
            max_overflow=_number(environment, 'SMARTTEST_DB_MAX_OVERFLOW', 0, minimum=0),
            pool_timeout_seconds=_number(environment, 'SMARTTEST_DB_POOL_TIMEOUT_SECONDS', 5, integer=False),
            connect_timeout_seconds=_number(environment, 'SMARTTEST_DB_CONNECT_TIMEOUT_SECONDS', 5))


@dataclass(frozen=True)
class RedisCacheSettings:
    host: str
    port: int = 6379
    database: int = 0
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    max_connections: int = 10
    socket_timeout_seconds: float = 1
    connect_timeout_seconds: float = 1

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] = os.environ):
        return cls(host=_required(environment, 'SMARTTEST_REDIS_HOST'),
            port=_number(environment, 'SMARTTEST_REDIS_PORT', 6379, maximum=65535),
            database=_number(environment, 'SMARTTEST_REDIS_DB', 0, minimum=0),
            username=environment.get('SMARTTEST_REDIS_USER') or None,
            password=environment.get('SMARTTEST_REDIS_PASSWORD') or None,
            max_connections=_number(environment, 'SMARTTEST_REDIS_MAX_CONNECTIONS', 10),
            socket_timeout_seconds=_number(environment, 'SMARTTEST_REDIS_SOCKET_TIMEOUT_SECONDS', 1, integer=False, minimum=0.001),
            connect_timeout_seconds=_number(environment, 'SMARTTEST_REDIS_CONNECT_TIMEOUT_SECONDS', 1, integer=False, minimum=0.001))


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_size: int

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] = os.environ) -> "DatabaseSettings":
        required = ("WIFI_DB_HOST", "WIFI_DB_USER", "WIFI_DB_PASSWORD", "WIFI_DB_NAME")
        missing = [name for name in required if not environment.get(name)]
        if missing:
            raise ConfigurationError(f"Missing database environment variables: {', '.join(missing)}")
        return cls(
            host=environment["WIFI_DB_HOST"],
            port=int(environment.get("WIFI_DB_PORT", "3306")),
            user=environment["WIFI_DB_USER"],
            password=environment["WIFI_DB_PASSWORD"],
            database=environment["WIFI_DB_NAME"],
            pool_size=int(environment.get("WIFI_DB_POOL_SIZE", "10")),
        )
