from pathlib import Path
import importlib
import json

from client.app.data_sources.common import AuthenticatedCredentials
from client.app.ui.example.bridge.AuthBridge import AuthBridge
from client.app.ui.example.bridge.auth_accounts import AuthAccountStore, account_id_for_username


ROOT = Path(__file__).resolve().parents[4]
LOGIN_QML = ROOT / "client/app/ui/example/imports/example/qml/window/LoginWindow.qml"
AUTH_ACCOUNTS = ROOT / "client/app/ui/example/bridge/auth_accounts.py"
AUTH_BRIDGE = ROOT / "client/app/ui/example/bridge/AuthBridge.py"
TEST_PAGE_BRIDGE = ROOT / "client/app/ui/example/bridge/TestPageBridge.py"
REDMINE_BRIDGE = ROOT / "client/app/ui/example/bridge/RedmineBridge.py"


class Credentials:
    def __init__(self):
        self.values = {}

    def read(self, account_id):
        if account_id not in self.values:
            raise KeyError(account_id)
        return self.values[account_id]

    def write(self, account_id, username, password):
        self.values[account_id] = (username, password)

    def delete(self, account_id):
        self.values.pop(account_id, None)


def successful_auth(username, _password):
    return {
        "success": True,
        "username": username,
        "display_name": username.title(),
        "avatar_bytes": b"",
        "detail": "",
    }


def test_remembered_active_account_restores_without_ldap_on_startup(tmp_path):
    credentials = Credentials()
    store = AuthAccountStore(tmp_path)
    account_id = store.record_login("alice", "Alice", True)
    store.set_active_account(account_id)
    credentials.values[account_id] = ("alice", "saved-secret")
    jobs = []
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=jobs.append,
    )
    bridge._ldap_authenticate = successful_auth

    bridge.restoreStartupSession()

    assert bridge.authState == "authenticated"
    assert bridge.authenticated is True
    assert jobs == []
    assert bridge.runtime_credentials() == AuthenticatedCredentials("alice", "saved-secret")


def test_active_account_without_remembered_password_is_not_restored(tmp_path):
    credentials = Credentials()
    store = AuthAccountStore(tmp_path)
    account_id = store.record_login("alice", "Alice", False)
    store.set_active_account(account_id)
    jobs = []
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=jobs.append,
    )

    bridge.restoreStartupSession()

    assert bridge.selectedAccountId == account_id
    assert bridge.authState == "credential_required"
    assert bridge.authenticated is False
    assert bridge.runtime_credentials() is None
    assert jobs == []


def test_remembered_active_account_missing_saved_credential_requires_password(tmp_path):
    store = AuthAccountStore(tmp_path)
    account_id = store.record_login("alice", "Alice", True)
    store.set_active_account(account_id)
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=Credentials(),
        authentication_runner=lambda _job: (_ for _ in ()).throw(AssertionError("must not authenticate")),
    )

    bridge.restoreStartupSession()

    assert bridge.selectedAccountId == account_id
    assert bridge.authState == "credential_required"
    assert bridge.authenticated is False
    assert bridge.runtime_credentials() is None


def test_mismatched_saved_credential_is_not_restored_or_sent_to_ldap(tmp_path):
    credentials = Credentials()
    store = AuthAccountStore(tmp_path)
    account_id = store.record_login("alice", "Alice", True)
    store.set_active_account(account_id)
    credentials.values[account_id] = ("bob", "wrong-account-secret")
    jobs = []
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=jobs.append,
    )

    bridge.restoreStartupSession()

    assert bridge.authState == "credential_required"
    assert bridge.runtime_credentials() is None
    assert jobs == []
    assert credentials.values == {}
    assert bridge._account_store.get(account_id)["remember_password"] is False


def test_no_remember_login_requires_credential_after_restart(tmp_path):
    credentials = Credentials()
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=lambda job: job(),
    )
    bridge._ldap_authenticate = successful_auth
    bridge.login("alice", "session-only", False)
    restarted = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=lambda _job: (_ for _ in ()).throw(AssertionError("must not authenticate")),
    )

    restarted.restoreStartupSession()

    assert restarted.selectedAccountId == account_id_for_username("alice")
    assert restarted.authState == "credential_required"
    assert restarted.runtime_credentials() is None


def test_auth_preferences_do_not_emit_business_session_change(tmp_path):
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=Credentials(),
        authentication_runner=lambda job: job(),
    )
    bridge._ldap_authenticate = successful_auth
    bridge.login("alice", "secret", False)
    auth_events = []
    preference_events = []
    bridge.authChanged.connect(lambda: auth_events.append(True))
    bridge.preferencesChanged.connect(lambda: preference_events.append(True))

    bridge.setRememberPassword(True)

    assert auth_events == []
    assert preference_events == [True]
    assert bridge.runtime_credentials() == AuthenticatedCredentials("alice", "secret")


def test_single_python_runtime_credential_contract_and_no_qml_password_api(tmp_path):
    bridge = AuthBridge(project_root=ROOT, state_root=tmp_path, credential_store=Credentials())
    bridge._set_auth_state(username="alice", authenticated=True, password="secret", display_name="Alice")
    assert bridge.runtime_credentials() == AuthenticatedCredentials("alice", "secret")
    assert bridge.metaObject().indexOfMethod("runtime_credentials()") == -1
    for removed in (
        "currentPassword", "transientCredential", "authenticated_credentials",
        "acquireRuntimeCredential", "supplyRuntimeCredential", "cancelRuntimeCredentialSupply",
        "isAuthenticated",
    ):
        assert not hasattr(bridge, removed)
    for source_path in (TEST_PAGE_BRIDGE, REDMINE_BRIDGE):
        source = source_path.read_text(encoding="utf-8")
        assert "runtime_credentials" in source
        assert "transientCredential" not in source
        assert "authenticated_credentials" not in source


def test_redmine_login_consumes_runtime_credentials_contract():
    from client.app.ui.example.bridge.RedmineBridge import RedmineBridge

    class RuntimeAuth:
        def __init__(self):
            self.calls = 0

        def runtime_credentials(self):
            self.calls += 1
            return AuthenticatedCredentials("alice", "ldap-secret")

    class DummyBridge:
        def __init__(self):
            self._auth = RuntimeAuth()
            self._account = ""
            self.launched = None

        def _launch(self, operation):
            self.launched = operation

    dummy = DummyBridge()

    RedmineBridge.startLogin(dummy)

    assert dummy._auth.calls == 1
    assert dummy._account == "alice"
    assert callable(dummy.launched)


def test_login_window_has_no_runtime_supply_mode_and_logout_requires_authenticated_state():
    qml = LOGIN_QML.read_text(encoding="utf-8")
    assert "credentialSupplyMode" not in qml
    assert "supplyRuntimeCredential" not in qml
    logout = qml.split('objectName: "accountLogoutButton"', 1)[1].split("onClicked", 1)[0]
    assert "AuthBridge.authenticated" in logout


def test_auto_login_concept_is_absent_from_auth_production_and_qml():
    for path in (AUTH_BRIDGE, LOGIN_QML):
        source = path.read_text(encoding="utf-8")
        assert "auto_login" not in source
        assert "autoLogin" not in source
        assert "Auto login" not in source
    account_store_source = AUTH_ACCOUNTS.read_text(encoding="utf-8")
    assert account_store_source.count('"auto_login"') == 1
    assert "set_auto_login" not in account_store_source


def test_legacy_auto_login_field_is_removed_from_account_storage(tmp_path):
    account_id = account_id_for_username("alice")
    path = tmp_path / "auth_accounts.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "last_account_id": account_id,
        "active_account_id": account_id,
        "accounts": [{
            "account_id": account_id,
            "username": "alice",
            "display_name": "Alice",
            "remember_password": True,
            "auto_login": True,
            "last_login_at": "2026-09-01T00:00:00+08:00",
        }],
        "pending_credential_cleanup": [],
    }), encoding="utf-8")

    AuthAccountStore(tmp_path)

    assert "auto_login" not in path.read_text(encoding="utf-8")


def test_logout_prevents_remembered_session_restore_on_next_start(tmp_path):
    credentials = Credentials()
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=lambda job: job(),
    )
    bridge._ldap_authenticate = successful_auth
    bridge.login("alice", "secret", True)
    bridge.logout()
    jobs = []
    restarted = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=jobs.append,
    )

    restarted.restoreStartupSession()

    assert restarted.authState == "credential_required"
    assert restarted.authenticated is False
    assert jobs == []


def test_explicit_business_invalid_credentials_clears_saved_session_once(tmp_path):
    credentials = Credentials()
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=lambda job: job(),
    )
    bridge._ldap_authenticate = successful_auth
    bridge.login("alice", "secret", True)
    account_id = account_id_for_username("alice")
    events = []
    bridge.authChanged.connect(lambda: events.append(bridge.authState))

    assert bridge.invalidate_runtime_credentials("service_unavailable") is False
    assert bridge.authenticated is True
    assert credentials.values[account_id] == ("alice", "secret")
    assert bridge.invalidate_runtime_credentials("invalid_credentials") is True

    assert bridge.authState == "credential_required"
    assert bridge.runtime_credentials() is None
    assert credentials.values == {}
    assert bridge._account_store.active_account_id == ""
    assert bridge._account_store.get(account_id)["remember_password"] is False
    assert events == ["credential_required"]
    assert bridge.invalidate_runtime_credentials("invalid_credentials") is False
    assert events == ["credential_required"]


def test_switch_authenticates_and_remove_current_account_invalidates_session(tmp_path):
    credentials = Credentials()
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=credentials,
        authentication_runner=lambda job: job(),
    )
    bridge._ldap_authenticate = successful_auth
    bridge.login("alice", "alice-secret", True)
    alice_id = account_id_for_username("alice")
    bridge.login("bob", "bob-secret", True)
    bridge.selectAccount(alice_id)
    assert bridge.runtime_credentials() == AuthenticatedCredentials("alice", "alice-secret")

    bridge.removeAccount(alice_id)

    assert bridge.selectedAccountId == account_id_for_username("bob")
    assert bridge.authState == "credential_required"
    assert bridge.runtime_credentials() is None


def test_auth_lifecycle_logs_do_not_expose_password(tmp_path, monkeypatch):
    records = []
    module = importlib.import_module("client.app.ui.example.bridge.AuthBridge")
    monkeypatch.setattr(module, "smart_log", lambda *args, **kwargs: records.append((args, kwargs)))
    bridge = AuthBridge(
        project_root=ROOT, state_root=tmp_path, credential_store=Credentials(),
        authentication_runner=lambda job: job(),
    )
    bridge._ldap_authenticate = successful_auth

    bridge.login("alice", "unique-secret-value", False)

    assert records
    assert "unique-secret-value" not in repr(records)


def test_default_platform_store_restores_macos_saved_account_without_ldap(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("keyring")
    from core.credentials.keyring_store import KeyringCredentialStore
    import core.credentials as credentials
    import keyring.backends.macOS as macos

    class Keychain:
        priority = 5

        def __init__(self):
            self.values = {}

        def set_password(self, service, account, value):
            self.values[service, account] = value

        def get_password(self, service, account):
            return self.values.get((service, account))

    backend = Keychain()
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(macos, "Keyring", lambda: backend)
    store = AuthAccountStore(tmp_path)
    account_id = store.record_login("alice", "Alice", True)
    store.set_active_account(account_id)
    module = importlib.import_module("client.app.ui.example.bridge.AuthBridge")
    KeyringCredentialStore(backend, target_prefix=module.LDAP_CREDENTIAL_PREFIX).write(
        account_id, "alice", "synthetic-secret",
    )
    jobs = []
    bridge = AuthBridge(project_root=ROOT, state_root=tmp_path, authentication_runner=jobs.append)
    bridge.restoreStartupSession()
    assert bridge.authenticated is True
    assert bridge.runtime_credentials() == AuthenticatedCredentials("alice", "synthetic-secret")
    assert jobs == []
