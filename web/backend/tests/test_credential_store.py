from sqlalchemy import text
import base64

import pytest

from smarttest_web.credentials import CredentialStoreError, create_credential_store
from smarttest_web.session import PersistentSessionStore
from smarttest_web.schema import verify_schema_revision

class NativeCredentials:
    def __init__(self): self.values = {}
    def write_generic(self, target, username, blob): self.values[target] = (username, bytes(blob))
    def read_generic(self, target):
        username, blob = self.values[target]
        return username, bytearray(blob)
    def delete_generic(self, target): self.values.pop(target, None)
    @staticmethod
    def clear(blob):
        for index in range(len(blob)): blob[index] = 0


def test_windows_backend_reuses_core_credential_manager_owner(
    tmp_path, *, database_factory
):
    native = NativeCredentials()
    store = create_credential_store(None, platform_name="win32", windows_native=native)
    store.write("abc_123", "coco", "secret")
    assert store.read("abc_123") == ("coco", "secret")
    assert next(iter(native.values)).startswith("SmartTest/WebSession/")


def test_linux_backend_encrypts_credentials_without_storing_plaintext_or_master_key(
    tmp_path, *, database_factory
):
    database = tmp_path / "web.db"
    verify_schema_revision(database_factory())
    master = b"k" * 32
    store = create_credential_store(
        database_factory(),
        platform_name="linux",
        environ={
            "SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(master).decode()
        },
    )
    store.write("abc_123", "coco", "top-secret-password")
    assert store.read("abc_123") == ("coco", "top-secret-password")
    with database_factory().connect() as connection:
        raw = connection.execute(
            text("SELECT ciphertext FROM web_credentials")
        ).scalar_one()
    assert b"top-secret-password" not in raw
    assert master not in raw
    with database_factory().transaction() as connection:
        assert connection.execute(
            text("SELECT key_version FROM web_credentials")
        ).fetchone() == (1,)


def test_account_credential_survives_restart_and_session_logout(
    tmp_path, *, database_factory
):
    database = tmp_path / "web.db"
    environment = {"SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"m" * 32).decode()}
    first = PersistentSessionStore(
        credential_store=create_credential_store(
            database_factory(), platform_name="linux", environ=environment
        ),
        database=database_factory(),
    )
    one = first.create("coco", "one")
    two = first.create("coco", "two")
    restarted = PersistentSessionStore(
        credential_store=create_credential_store(
            database_factory(), platform_name="linux", environ=environment
        ),
        database=database_factory(),
    )
    assert restarted.get(one).password == "two"
    assert restarted.get(two).password == "two"
    restarted.delete(one)
    assert restarted.get(one) is None
    assert restarted.get(two).password == "two"
    restarted.delete_all("coco")
    assert restarted.get(two) is None
    restored = restarted.create_from_saved("coco")
    assert restarted.get(restored).password == "two"


def test_expired_session_keeps_account_credential_for_new_session(
    tmp_path, *, database_factory
):
    database = tmp_path / "web.db"
    environment = {"SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"e" * 32).decode()}
    clock = [100.0]
    credentials = create_credential_store(
        database_factory(), platform_name="linux", environ=environment
    )
    sessions = PersistentSessionStore(
        credential_store=credentials,
        ttl_seconds=10,
        now=lambda: clock[0],
        database=database_factory(),
    )
    token = sessions.create("coco", "secret")
    clock[0] = 111.0
    assert sessions.get(token) is None
    with database_factory().transaction() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM web_credentials")
        ).fetchone() == (1,)
    restored = sessions.create_from_saved("coco")
    assert sessions.get(restored).password == "secret"


def test_explicit_invalid_credentials_deletes_account_credential_and_revokes_sessions(
    tmp_path, *, database_factory
):
    database = tmp_path / "web.db"
    environment = {"SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"i" * 32).decode()}
    sessions = PersistentSessionStore(
        credential_store=create_credential_store(
            database_factory(), platform_name="linux", environ=environment
        ),
        database=database_factory(),
    )
    one = sessions.create("coco", "secret")
    two = sessions.create_from_saved("coco")

    assert sessions.invalidate_credentials("coco") == 2
    assert sessions.get(one) is None
    assert sessions.get(two) is None
    with pytest.raises(CredentialStoreError):
        sessions.create_from_saved("coco")


def test_legacy_per_session_credential_is_migrated_to_account_owner(
    tmp_path, *, database_factory
):
    database = tmp_path / "web.db"
    environment = {"SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"l" * 32).decode()}
    credentials = create_credential_store(
        database_factory(), platform_name="linux", environ=environment
    )
    sessions = PersistentSessionStore(
        credential_store=credentials, database=database_factory()
    )
    token = sessions.create("coco", "secret")
    token_hash = sessions._hash(token)
    credentials.delete(sessions._account_key("coco"))
    credentials.write(token_hash, "coco", "secret")
    with database_factory().transaction() as connection:
        connection.execute(
            text(
                "UPDATE web_sessions SET credential_ref=:credential_ref WHERE token_hash=:token_hash"
            ),
            {"credential_ref": token_hash, "token_hash": token_hash},
        )

    restarted = PersistentSessionStore(
        credential_store=credentials, database=database_factory()
    )
    assert restarted.get(token).password == "secret"
    with database_factory().transaction() as connection:
        assert connection.execute(
            text(
                "SELECT credential_ref FROM web_sessions WHERE token_hash=:token_hash"
            ),
            {"token_hash": token_hash},
        ).fetchone() == (None,)
    assert restarted.create_from_saved("coco")


@pytest.mark.parametrize("configured", [None, "not-base64", base64.urlsafe_b64encode(b"short").decode()])
def test_linux_backend_fails_safely_for_missing_or_invalid_master_key(
    tmp_path, configured, *, database_factory
):
    environment = {} if configured is None else {"SMARTTEST_WEB_CREDENTIAL_KEY": configured}
    store = create_credential_store(
        database_factory(), platform_name="linux", environ=environment
    )
    with pytest.raises(CredentialStoreError, match="credential key"):
        store.write("abc_123", "coco", "never-stored")
    with database_factory().connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM web_credentials")
            ).scalar_one()
            == 0
        )


def test_restart_with_wrong_key_logs_only_safe_failure_and_keeps_session_authenticated(
    tmp_path, monkeypatch, *, database_factory
):
    import smarttest_web.session as session_module
    records = []
    monkeypatch.setattr(session_module, "smart_log", lambda message, **kwargs: records.append((message, kwargs)))
    database = tmp_path / "web.db"
    correct = {"SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"a" * 32).decode()}
    wrong = {"SMARTTEST_WEB_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"b" * 32).decode()}
    first = PersistentSessionStore(
        credential_store=create_credential_store(
            database_factory(), platform_name="linux", environ=correct
        ),
        database=database_factory(),
    )
    token = first.create("coco", "do-not-log-this")
    restarted = PersistentSessionStore(
        credential_store=create_credential_store(
            database_factory(), platform_name="linux", environ=wrong
        ),
        database=database_factory(),
    )

    value = restarted.get(token)
    assert value.username == "coco" and value.password is None
    assert records[0][0] == "Persistent Web credential recovery failed"
    assert records[0][1]["extra"] == {"exception_type": "CredentialStoreError"}
    assert "do-not-log-this" not in repr(records)
