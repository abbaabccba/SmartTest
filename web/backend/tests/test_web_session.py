from fastapi.testclient import TestClient
import pytest

from smarttest_web.app import create_app
from smarttest_web.background_refresh import BackgroundFactsRefresh
from smarttest_web.credentials import CredentialStoreError
from smarttest_web.project_facts_api import ProjectFactsWebOwner
from smarttest_web.session import PersistentSessionStore


class FakeAuthenticator:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"success": True, "username": "coco", "display_name": "Coco Chen", "avatar_bytes": b"avatar"}

    def authenticate(self, username, password):
        self.calls.append((username, password))
        return dict(self.result)


class FakeFactsOwner:
    def __init__(self, initial=None):
        self.snapshot = initial
        self.refresh_calls = []

    def query(self, username, *, filters=None, search=""):
        if self.snapshot is None:
            return ProjectFactsWebOwner._state("no_snapshot")
        return {"state": "ready", "projects": self.snapshot["projects"], "facets": []}

    def refresh(self, username, password):
        self.refresh_calls.append((username.account, password))
        self.snapshot = {"projects": [{"project_id": "A"}]}
        return self.query(username)


def make_client(tmp_path, authenticator=None, sessions=None, facts=None):
    store = sessions or PersistentSessionStore(tmp_path / "web.db")
    return TestClient(create_app(
        authenticator=lambda: authenticator or FakeAuthenticator(), session_store=lambda: store,
        project_facts_owner=lambda: facts or FakeFactsOwner({"projects": []}),
    ), base_url="https://testserver"), store


def test_sqlite_session_survives_restart_and_recovers_password_outside_database(tmp_path):
    client, store = make_client(tmp_path)
    response = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    token = client.cookies.get("smarttest_session")
    assert response.status_code == 200
    assert "secret" not in (tmp_path / "web.db").read_bytes().decode("latin1")
    value = PersistentSessionStore(tmp_path / "web.db").get(token)
    assert value.username == "coco" and value.password == "secret"
    assert store.journal_mode == "wal"


def test_login_cookie_is_90_days_and_database_only_contains_hash(tmp_path):
    client, store = make_client(tmp_path)
    response = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    token = client.cookies.get("smarttest_session")
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax", "max-age=7776000"))
    assert store.contains_token_hash(token)
    assert not store.contains_raw_token(token)


def test_sliding_expiry_is_renewed_only_after_refresh_interval(tmp_path):
    clock = [1_000.0]
    store = PersistentSessionStore(tmp_path / "web.db", ttl_seconds=100, refresh_interval_seconds=20, now=lambda: clock[0])
    token = store.create("coco", "secret")
    initial = store.get(token).expires_at
    clock[0] += 10
    assert store.get(token).expires_at == initial
    clock[0] += 11
    assert store.get(token).expires_at == clock[0] + 100
    clock[0] += 101
    assert store.get(token) is None


def test_eligible_activity_renews_browser_cookie_with_database_expiry(tmp_path):
    clock = [1_000.0]
    store = PersistentSessionStore(tmp_path / "web.db", ttl_seconds=100, refresh_interval_seconds=20, now=lambda: clock[0])
    client, _ = make_client(tmp_path, sessions=store)
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    clock[0] += 10
    assert "set-cookie" not in client.get("/api/auth/session").headers
    clock[0] += 11
    renewed = client.get("/api/auth/session")
    assert "max-age=100" in renewed.headers["set-cookie"].lower()
    assert "httponly" in renewed.headers["set-cookie"].lower()
    clock[0] += 10
    assert "set-cookie" not in client.get("/api/auth/session").headers


def test_https_login_renew_and_logout_keep_secure_cookie_attribute(tmp_path):
    clock = [1_000.0]
    store = PersistentSessionStore(
        tmp_path / "https-cookie.db", ttl_seconds=100,
        refresh_interval_seconds=20, now=lambda: clock[0],
    )
    client, _ = make_client(tmp_path, sessions=store)
    login = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    assert "secure" in login.headers["set-cookie"].lower()
    clock[0] += 21
    renewed = client.get("/api/auth/session")
    assert renewed.json()["authenticated"] is True
    assert "secure" in renewed.headers["set-cookie"].lower()
    logout = client.post("/api/auth/logout")
    assert "secure" in logout.headers["set-cookie"].lower()
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    logout_all = client.post("/api/auth/logout-all")
    assert "secure" in logout_all.headers["set-cookie"].lower()


def test_http_login_renew_and_logout_variants_omit_secure_cookie_attribute(tmp_path):
    clock = [1_000.0]
    store = PersistentSessionStore(
        tmp_path / "http-cookie.db", ttl_seconds=100,
        refresh_interval_seconds=20, now=lambda: clock[0],
    )
    client = TestClient(create_app(
        authenticator=FakeAuthenticator,
        session_store=lambda: store,
        project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
    ), base_url="http://testserver")

    login = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    assert "secure" not in login.headers["set-cookie"].lower()
    clock[0] += 21
    renewed = client.get("/api/auth/session")
    assert renewed.json()["authenticated"] is True
    assert "secure" not in renewed.headers["set-cookie"].lower()
    logout = client.post("/api/auth/logout")
    assert "secure" not in logout.headers["set-cookie"].lower()
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    logout_all = client.post("/api/auth/logout-all")
    assert "secure" not in logout_all.headers["set-cookie"].lower()


def test_multiple_devices_current_and_all_logout(tmp_path):
    client1, store = make_client(tmp_path)
    client2, _ = make_client(tmp_path, sessions=store)
    client1.post("/api/auth/login", json={"username": "coco", "password": "one"})
    client2.post("/api/auth/login", json={"username": "coco", "password": "two"})
    assert store.count == 2
    client1.post("/api/auth/logout")
    assert client1.get("/api/auth/session").json() == {"authenticated": False}
    assert client2.get("/api/auth/session").json()["authenticated"] is True
    assert client2.post("/api/auth/logout-all").status_code == 200
    assert client2.get("/api/auth/session").json() == {"authenticated": False}


def test_restored_session_recovers_server_credential_for_page_catalog_load(tmp_path):
    client, _ = make_client(tmp_path, facts=FakeFactsOwner())
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    token = client.cookies.get("smarttest_session")
    restarted_facts = FakeFactsOwner()
    restarted, _ = make_client(
        tmp_path, sessions=PersistentSessionStore(tmp_path / "web.db"), facts=restarted_facts,
    )
    restarted.cookies.set("smarttest_session", token)
    assert restarted.get("/api/auth/session").json()["authenticated"] is True
    response = restarted.get("/api/confluence/project-facts")
    assert response.status_code == 200
    assert response.json()["state"] == "no_snapshot"
    assert restarted_facts.refresh_calls == []


def test_invalid_login_returns_safe_failure(tmp_path):
    auth = FakeAuthenticator({"success": False, "code": "invalid_credentials", "detail": "secret"})
    response = make_client(tmp_path, authenticator=auth)[0].post("/api/auth/login", json={"username": "coco", "password": "secret"})
    assert response.status_code == 401
    assert response.json() == {"detail": {"state": "invalid_credentials"}}
    assert auth.calls == [("coco", "secret")]
    assert "secret" not in response.text


def test_browser_login_reuses_saved_account_credential_without_ldap(tmp_path):
    first_auth = FakeAuthenticator()
    first, store = make_client(tmp_path, authenticator=first_auth)
    initial = first.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    assert initial.status_code == 200
    assert initial.json()["avatarUrl"] == "/api/auth/avatar"
    assert first_auth.calls == [("coco", "secret")]

    unavailable_ldap = FakeAuthenticator({"success": False, "code": "ldap_unavailable"})
    restarted, _ = make_client(tmp_path, authenticator=unavailable_ldap, sessions=store)
    response = restarted.post("/api/auth/login", json={"username": "coco"})

    assert response.status_code == 200
    assert response.json()["avatarUrl"] == "/api/auth/avatar"
    assert restarted.get("/api/auth/avatar").content == b"avatar"
    assert unavailable_ldap.calls == []


@pytest.mark.parametrize("cached_avatar", [False, True])
def test_browser_login_replaces_saved_credential_with_verified_new_password(tmp_path, cached_avatar):
    store = PersistentSessionStore(tmp_path / "web.db")
    store.create("coco", "old-password", avatar_bytes=b"avatar" if cached_avatar else b"")
    auth = FakeAuthenticator()
    client, _ = make_client(tmp_path, authenticator=auth, sessions=store)

    response = client.post("/api/auth/login", json={"username": "coco", "password": "new-password"})

    assert response.status_code == 200
    assert auth.calls == [("coco", "new-password")]
    assert store.get(client.cookies.get("smarttest_session")).password == "new-password"
    assert PersistentSessionStore(tmp_path / "web.db").saved_credentials("coco") == ("coco", "new-password")


@pytest.mark.parametrize("cached_avatar", [False, True])
def test_browser_login_rejects_new_password_without_reusing_saved_credential(tmp_path, cached_avatar):
    store = PersistentSessionStore(tmp_path / "web.db")
    store.create("coco", "old-password", avatar_bytes=b"avatar" if cached_avatar else b"")
    auth = FakeAuthenticator({"success": False, "code": "invalid_credentials"})
    client, _ = make_client(tmp_path, authenticator=auth, sessions=store)

    response = client.post("/api/auth/login", json={"username": "coco", "password": "wrong-password"})

    assert response.status_code == 401
    assert response.json() == {"detail": {"state": "invalid_credentials"}}
    assert auth.calls == [("coco", "wrong-password")]
    assert store.saved_credentials("coco") == ("coco", "old-password")
    assert client.get("/api/auth/session").json() == {"authenticated": False}


@pytest.mark.parametrize("cached_avatar", [False, True])
def test_browser_login_reuses_same_saved_password_without_ldap(tmp_path, cached_avatar):
    store = PersistentSessionStore(tmp_path / "web.db")
    avatar = b"avatar" if cached_avatar else b""
    store.create("coco", "secret", display_name="Coco", avatar_bytes=avatar)
    auth = FakeAuthenticator({"success": False, "code": "ldap_unavailable"})
    client, _ = make_client(tmp_path, authenticator=auth, sessions=store)

    response = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    assert response.status_code == 200
    assert response.json()["displayName"] == "Coco"
    assert response.json()["avatarUrl"] == ("/api/auth/avatar" if cached_avatar else "")
    assert store.get(client.cookies.get("smarttest_session")).avatar_bytes == avatar
    assert store.saved_credentials("coco") == ("coco", "secret")
    assert auth.calls == []


def test_explicit_downstream_basic_auth_rejection_invalidates_account_and_sessions(tmp_path):
    class ExplicitRejection(RuntimeError):
        response = type("Response", (), {
            "status_code": 401,
            "headers": {"WWW-Authenticate": 'Basic realm="Atlassian"'},
        })()

    class RejectingFacts(FakeFactsOwner):
        def refresh_and_sync_details(self, *_args, **_kwargs):
            raise ExplicitRejection("safe rejection")

    store = PersistentSessionStore(tmp_path / "explicit-rejection.db")
    refresh = BackgroundFactsRefresh(submit=lambda work: work())
    client = TestClient(create_app(
        authenticator=lambda: FakeAuthenticator(),
        session_store=lambda: store,
        project_facts_owner=RejectingFacts,
        facts_refresh=lambda: refresh,
    ), base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    rejected = client.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""})

    assert rejected.status_code == 200
    assert rejected.json()["state"] == "invalid_credentials"
    follow_up = client.get("/api/test-suites?scope=mine")
    assert follow_up.status_code == 401
    assert follow_up.json()["detail"]["state"] == "invalid_credentials"
    with pytest.raises(CredentialStoreError):
        store.create_from_saved("coco")


def test_general_downstream_401_does_not_invalidate_account_credentials(tmp_path):
    class General401(RuntimeError):
        response = type("Response", (), {"status_code": 401, "headers": {}})()

    class RejectingFacts(FakeFactsOwner):
        def refresh_and_sync_details(self, *_args, **_kwargs):
            raise General401("not an explicit credential rejection")

    store = PersistentSessionStore(tmp_path / "general-401.db")
    refresh = BackgroundFactsRefresh(submit=lambda work: work())
    client = TestClient(create_app(
        authenticator=lambda: FakeAuthenticator(),
        session_store=lambda: store,
        project_facts_owner=RejectingFacts,
        facts_refresh=lambda: refresh,
    ), base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    response = client.put("/api/confluence/filter-snapshot", json={"filters": {}, "search": ""})

    assert response.json()["state"] == "failed"
    assert client.get("/api/test-suites?scope=mine").status_code == 200
    assert store.create_from_saved("coco")


def test_explicit_jira_basic_auth_rejection_invalidates_account(tmp_path):
    class ExplicitRejection(RuntimeError):
        response = type("Response", (), {
            "status_code": 401,
            "headers": {"www-authenticate": 'Basic realm="Atlassian"'},
        })()

    class RejectingJira:
        def list_issues(self, *_args):
            raise ExplicitRejection("safe rejection")

    store = PersistentSessionStore(tmp_path / "jira-rejection.db")
    client = TestClient(create_app(
        authenticator=lambda: FakeAuthenticator(),
        session_store=lambda: store,
        project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
        jira_cache_owner=lambda *_args: RejectingJira(),
    ), base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    response = client.get("/api/jira/issues")

    assert response.status_code == 401
    assert response.json()["detail"]["state"] == "invalid_credentials"
    with pytest.raises(CredentialStoreError):
        store.create_from_saved("coco")


def test_existing_fact_cache_is_read_after_login(tmp_path):
    facts = FakeFactsOwner({"projects": [{"project_id": "cached"}]})
    client, _ = make_client(tmp_path, facts=facts)
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    response = client.get("/api/confluence/project-facts")
    assert response.json()["projects"][0]["project_id"] == "cached"
    assert facts.refresh_calls == []


def test_get_without_cache_does_not_start_remote_refresh(tmp_path):
    facts = FakeFactsOwner()
    client, _ = make_client(tmp_path, facts=facts)
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    response = client.get("/api/confluence/project-facts")

    assert response.json()["state"] == "no_snapshot"
    assert facts.refresh_calls == []


def test_get_details_parameter_does_not_start_confluence_detail_sync(tmp_path):
    class Facts(FakeFactsOwner):
        sync_calls = 0
        def refresh_and_sync_details(self, _access, _password, **_kwargs):
            self.sync_calls += 1
            return {"state": "ready", "projects": []}

    store = PersistentSessionStore(tmp_path / "web.db")
    first = create_app(authenticator=FakeAuthenticator, session_store=lambda: store,
                       project_facts_owner=lambda: Facts({"projects": []}))
    with TestClient(first, base_url="https://testserver"):
        pass
    second_facts = Facts({"projects": []})
    second = create_app(authenticator=FakeAuthenticator, session_store=lambda: store,
                        project_facts_owner=lambda: second_facts)
    with TestClient(second, base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
        response = client.get("/api/confluence/project-facts?details=1")

    assert response.status_code == 200
    assert second_facts.sync_calls == 0
