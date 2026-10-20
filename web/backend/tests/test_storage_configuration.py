"""Persistent deployment configuration uses existing JSON and Windows secret owners."""
import json
from unittest.mock import Mock

import pytest

from smarttest_web import config


def test_persistent_non_secret_settings_resolve_explicit_windows_references(tmp_path):
    path = tmp_path / 'storage.json'
    stored = {'SMARTTEST_DB_HOST': 'localhost', 'SMARTTEST_DB_NAME': 'smarttest_web', 'SMARTTEST_DB_USER': 'app',
        'SMARTTEST_DB_CREDENTIAL_REF': 'mysql', 'SMARTTEST_REDIS_HOST': 'localhost',
        'SMARTTEST_REDIS_CREDENTIAL_REF': 'redis'}
    path.write_text(json.dumps(stored), encoding='utf-8')
    credentials = Mock()
    credentials.read.side_effect = [('app', 'synthetic-db-secret'), ('default', 'synthetic-cache-secret')]
    environment = config.storage_environment({'SMARTTEST_DB_PORT': '13306'}, config_path=path,
        platform_name='win32', credential_store=credentials)
    assert config.ApplicationDatabaseSettings.from_environment(environment).port == 13306
    assert environment['SMARTTEST_DB_PASSWORD'] == 'synthetic-db-secret'
    assert environment['SMARTTEST_REDIS_PASSWORD'] == 'synthetic-cache-secret'
    assert 'SMARTTEST_REDIS_USER' not in environment
    assert config.statistics_cache_ttl_seconds(environment) == 300
    assert path.read_text(encoding='utf-8') == json.dumps(stored)
    assert [call.args for call in credentials.read.call_args_list] == [('mysql',), ('redis',)]


def test_configuration_rejects_plaintext_secrets_and_missing_or_unsupported_sources(tmp_path):
    path = tmp_path / 'storage.json'
    path.write_text(json.dumps({'SMARTTEST_DB_PASSWORD': 'must-not-load'}), encoding='utf-8')
    with pytest.raises(config.ConfigurationError, match='non-secret'):
        config.storage_environment({}, config_path=path)
    path.write_text(json.dumps({'SMARTTEST_DB_CREDENTIAL_REF': 'mysql'}), encoding='utf-8')
    with pytest.raises(config.ConfigurationError, match='require Windows'):
        config.storage_environment({}, config_path=path, platform_name='linux')
    environment = config.storage_environment({'SMARTTEST_DB_PASSWORD': 'external-secret'},
        config_path=path, platform_name='linux')
    assert environment['SMARTTEST_DB_PASSWORD'] == 'external-secret'
    with pytest.raises(config.ConfigurationError, match='SMARTTEST_DB_USER'):
        config.ApplicationDatabaseSettings.from_environment(environment)


def test_configured_asgi_assembly_uses_mysql_redis_and_explicit_policy(monkeypatch, tmp_path):
    import smarttest_web.app as app
    environment = {'SMARTTEST_DB_HOST': 'db', 'SMARTTEST_DB_USER': 'app',
        'SMARTTEST_DB_PASSWORD': 'db-secret', 'SMARTTEST_DB_NAME': 'smarttest_web',
        'SMARTTEST_REDIS_HOST': 'cache', 'SMARTTEST_STATISTICS_CACHE_TTL_SECONDS': '300',
        'SMARTTEST_REPORTS_ROOT': str(tmp_path / 'reports')}
    database, cache, result = Mock(), Mock(), object()
    monkeypatch.setattr(app, 'storage_environment', lambda: environment)
    monkeypatch.setattr(app.WebDatabase, 'from_settings', Mock(return_value=database))
    monkeypatch.setattr(app, 'RedisCache', Mock(return_value=cache))
    factory = Mock(return_value=result)
    monkeypatch.setattr(app, 'create_app', factory)
    assert app.create_configured_app() is result
    assert factory.call_args.kwargs == {'database': database, 'statistics_cache': cache,
        'statistics_cache_ttl_seconds': 300, 'reports_root': tmp_path / 'reports'}
    factory.side_effect = RuntimeError('synthetic assembly failure')
    with pytest.raises(RuntimeError, match='synthetic assembly failure'):
        app.create_configured_app()
    database.close.assert_called_once()
    cache.close.assert_called_once()


def test_storage_defaults_use_same_local_endpoints_without_host_or_port():
    environment = {'SMARTTEST_DB_USER': 'app', 'SMARTTEST_DB_PASSWORD': 'synthetic-secret',
                   'SMARTTEST_DB_NAME': 'smarttest_web'}
    database = config.ApplicationDatabaseSettings.from_environment(environment)
    cache = config.RedisCacheSettings.from_environment(environment)
    assert (database.host, database.port) == ('127.0.0.1', 13306)
    assert (cache.host, cache.port) == ('127.0.0.1', 16379)
    assert config.ApplicationDatabaseSettings('localhost', 'app', 'secret', 'db').port == 13306
    assert config.RedisCacheSettings('localhost').port == 16379


def test_storage_explicit_file_and_environment_endpoints_keep_precedence(tmp_path):
    path = tmp_path / 'storage.json'
    path.write_text(json.dumps({'SMARTTEST_DB_HOST': 'file-db', 'SMARTTEST_DB_PORT': 3306,
        'SMARTTEST_REDIS_HOST': 'file-cache', 'SMARTTEST_REDIS_PORT': 6379}), encoding='utf-8')
    environment = config.storage_environment({'SMARTTEST_DB_USER': 'app',
        'SMARTTEST_DB_PASSWORD': 'synthetic-secret', 'SMARTTEST_DB_NAME': 'smarttest_web',
        'SMARTTEST_DB_HOST': 'environment-db', 'SMARTTEST_REDIS_PORT': '16380'}, config_path=path)
    database = config.ApplicationDatabaseSettings.from_environment(environment)
    cache = config.RedisCacheSettings.from_environment(environment)
    assert (database.host, database.port) == ('environment-db', 3306)
    assert (cache.host, cache.port) == ('file-cache', 16380)


@pytest.mark.parametrize('missing', ['SMARTTEST_DB_USER', 'SMARTTEST_DB_PASSWORD', 'SMARTTEST_DB_NAME'])
def test_local_defaults_preserve_other_required_database_settings(missing):
    environment = {'SMARTTEST_DB_USER': 'app', 'SMARTTEST_DB_PASSWORD': 'synthetic-secret',
                   'SMARTTEST_DB_NAME': 'smarttest_web'}
    del environment[missing]
    with pytest.raises(config.ConfigurationError, match=missing):
        config.ApplicationDatabaseSettings.from_environment(environment)
