import sys

import pytest
pytest.importorskip("keyring")
from keyring.errors import KeyringLocked, PasswordDeleteError

from core.credentials import CredentialError
from core.credentials.keyring_store import KeyringCredentialStore
from core.credentials.windows import CredentialNotFoundError


class Keychain:
    priority = 5

    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        if (service, account) not in self.values:
            if sys.platform == 'darwin':
                from keyring.backends.macOS import api
                raise PasswordDeleteError("No such password") from api.NotFound()
            raise PasswordDeleteError("No such password")
        del self.values[service, account]


def test_keychain_roundtrip_account_isolation_and_missing_delete():
    backend = Keychain()
    store = KeyringCredentialStore(backend, target_prefix='SmartTest/LDAP/')
    store.write('alice', 'AMLOGIC\\alice', 'synthetic-密码')
    store.write('bob', 'AMLOGIC\\bob', 'different-secret')
    assert KeyringCredentialStore(backend, target_prefix='SmartTest/LDAP/').read('alice') == (
        'AMLOGIC\\alice', 'synthetic-密码',
    )
    store.delete('alice')
    assert store.read('bob') == ('AMLOGIC\\bob', 'different-secret')
    with pytest.raises(CredentialNotFoundError):
        store.read('alice')
    with pytest.raises(CredentialNotFoundError):
        store.delete('alice')


@pytest.mark.parametrize('operation', ['read', 'write', 'delete'])
def test_keychain_access_errors_are_storage_errors_without_secret_disclosure(operation):
    class LockedKeychain(Keychain):
        def get_password(self, service, account):
            if operation == 'read':
                raise KeyringLocked('synthetic-secret')
            return super().get_password(service, account)

        def set_password(self, service, account, value):
            if operation == 'write':
                raise KeyringLocked('synthetic-secret')
            super().set_password(service, account, value)

        def delete_password(self, service, account):
            raise KeyringLocked('synthetic-secret')

    backend = LockedKeychain()
    store = KeyringCredentialStore(backend)
    if operation != 'write':
        backend.values['SmartTest/ProjectWeeklyAudit/alice', 'alice'] = '["alice", "synthetic-secret"]'
    with pytest.raises(CredentialError) as failure:
        if operation == 'write':
            store.write('alice', 'alice', 'synthetic-secret')
        else:
            getattr(store, operation)('alice')
    assert 'synthetic-secret' not in str(failure.value)
    assert backend.values or operation == 'write'


def test_desktop_factory_selects_macos_keychain(monkeypatch):
    import core.credentials as credentials
    import keyring.backends.macOS as macos

    backend = Keychain()
    monkeypatch.setattr(credentials.sys, 'platform', 'darwin')
    monkeypatch.setattr(macos, 'Keyring', lambda: backend)
    store = credentials.desktop_credential_store(target_prefix='SmartTest/LDAP/')
    store.write('alice', 'alice', 'synthetic-secret')
    assert ('SmartTest/LDAP/alice', 'alice') in backend.values


def test_malformed_record_can_be_deleted_without_reading():
    backend = Keychain()
    backend.values['SmartTest/ProjectWeeklyAudit/alice', 'alice'] = 'invalid-json'
    KeyringCredentialStore(backend).delete('alice')
    assert backend.values == {}


def test_linux_explicit_secret_service_backend_and_native_errors(monkeypatch):
    import sys
    import types
    import core.credentials as credentials
    import keyring.backends.SecretService as secret_service

    class SecretStorageException(Exception):
        pass

    exceptions = types.ModuleType('secretstorage.exceptions')
    exceptions.SecretStorageException = SecretStorageException
    monkeypatch.setitem(sys.modules, 'secretstorage.exceptions', exceptions)

    class SecretService(Keychain):
        priority = 5

        def get_password(self, service, account):
            raise SecretStorageException('synthetic-secret')

        def delete_password(self, service, account):
            raise PasswordDeleteError('No such password')

    backend = SecretService()
    monkeypatch.setattr(secret_service, 'Keyring', lambda: backend)
    monkeypatch.setattr(credentials.sys, 'platform', 'linux')
    store = credentials.desktop_credential_store(target_prefix='SmartTest/LDAP/')
    store.write('alice', 'alice', 'synthetic-secret')
    assert ('SmartTest/LDAP/alice', 'alice') in backend.values
    with pytest.raises(CredentialError) as failure:
        store.read('alice')
    assert 'synthetic-secret' not in str(failure.value)
    with pytest.raises(CredentialNotFoundError):
        store.delete('unknown')


def test_linux_missing_backend_does_not_fail_store_construction(monkeypatch):
    import sys
    import core.credentials as credentials

    monkeypatch.setattr(credentials.sys, 'platform', 'linux')
    monkeypatch.setitem(sys.modules, 'secretstorage.exceptions', None)
    store = credentials.desktop_credential_store(target_prefix='SmartTest/LDAP/')
    with pytest.raises(CredentialError, match='backend is unavailable'):
        store.write('alice', 'alice', 'synthetic-secret')


def test_linux_service_unavailable_is_a_storage_error(monkeypatch):
    import types
    import core.credentials as credentials
    import keyring.backends.SecretService as secret_service

    class SecretStorageException(Exception):
        pass

    exceptions = types.ModuleType('secretstorage.exceptions')
    exceptions.SecretStorageException = SecretStorageException
    monkeypatch.setitem(sys.modules, 'secretstorage.exceptions', exceptions)

    class UnavailableService:
        @property
        def priority(self):
            raise RuntimeError('Secret Service daemon unavailable')

    monkeypatch.setattr(secret_service, 'Keyring', UnavailableService)
    monkeypatch.setattr(credentials.sys, 'platform', 'linux')
    store = credentials.desktop_credential_store(target_prefix='SmartTest/LDAP/')
    with pytest.raises(CredentialError, match='backend is unavailable'):
        store.read('alice')
