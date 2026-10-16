from fastapi.testclient import TestClient

from smarttest_web.app import create_app
from smarttest_web.credentials import CredentialStoreError
from smarttest_web.session import PersistentSessionStore
from test_web_session import FakeAuthenticator, FakeFactsOwner


def api(tmp_path, username="coco", *, database_factory):
    store = PersistentSessionStore(database=database_factory())
    auth = FakeAuthenticator({"success": True, "username": username,
                              "display_name": username.title(), "avatar_bytes": b""})
    client = TestClient(create_app(authenticator=lambda: auth, session_store=lambda: store,
                                   project_facts_owner=lambda: FakeFactsOwner({"projects": []})),
                        base_url="https://testserver")
    client.post("/api/auth/login", json={"username": username, "password": "secret"})
    return client


def test_suite_api_auth_visibility_copy_and_owner_boundary(
    tmp_path, *, database_factory
):
    coco, atlas = api(tmp_path, "coco", database_factory=database_factory), api(
        tmp_path, "atlas", database_factory=database_factory
    )
    created = coco.post("/api/test-suites", json={"name": "Mine", "description": "d",
                        "visibility": "private", "orderedNodeids": ["a"],
                        "ownerUsername": "atlas"})
    assert created.status_code == 200
    suite = created.json()
    assert suite["ownerUsername"] == "coco"
    assert atlas.get(f"/api/test-suites/{suite['id']}").status_code == 404
    updated = coco.put(f"/api/test-suites/{suite['id']}", json={"revision": 1, "name": "Mine",
                       "description": "d", "visibility": "shared", "orderedNodeids": ["a"]})
    assert updated.status_code == 200
    assert "orderedNodeids" not in atlas.get("/api/test-suites?scope=shared").json()[0]
    assert atlas.put(f"/api/test-suites/{suite['id']}", json={"revision": 2, "name": "x",
                     "description": "", "visibility": "shared", "orderedNodeids": ["a"]}).status_code == 404
    copied = atlas.post(f"/api/test-suites/{suite['id']}/copy", json={"name": "Copy", "visibility": "private"})
    assert copied.status_code == 200 and copied.json()["ownerUsername"] == "atlas"


def test_suite_api_validation_conflicts_and_authentication(
    tmp_path, *, database_factory
):
    client = api(tmp_path, database_factory=database_factory)
    payload = {"name": "Suite", "description": "", "visibility": "private", "orderedNodeids": ["a"]}
    assert client.post("/api/test-suites", json=payload).status_code == 200
    assert client.post("/api/test-suites", json=payload).status_code == 409
    suite = client.get("/api/test-suites?scope=mine").json()[0]
    assert client.put(f"/api/test-suites/{suite['id']}", json={**payload, "revision": 99}).status_code == 409
    assert client.post("/api/test-suites", json={**payload, "name": "", "orderedNodeids": []}).status_code == 422
    anonymous = TestClient(create_app(), base_url="https://testserver")
    assert anonymous.get("/api/test-suites?scope=mine").status_code == 401


def test_http_login_cookie_is_returned_by_real_cookie_jar_for_suite_list(
    tmp_path, *, database_factory
):
    store = PersistentSessionStore(database=database_factory())
    auth = FakeAuthenticator({"success": True, "username": "coco",
                              "display_name": "Coco", "avatar_bytes": b""})
    client = TestClient(create_app(
        authenticator=lambda: auth,
        session_store=lambda: store,
        project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
    ), base_url="http://testserver")

    login = client.post("/api/auth/login", json={"username": "coco", "password": "secret"})
    assert login.status_code == 200
    assert "secure" not in login.headers["set-cookie"].lower()
    suites = client.get("/api/test-suites?scope=mine")
    assert suites.status_code == 200
    assert suites.json() == []


def test_client_session_skips_ldap_and_authenticates_suite_list(
    tmp_path, *, database_factory
):
    store = PersistentSessionStore(database=database_factory())
    auth = FakeAuthenticator({"success": False, "code": "ldap_unavailable"})
    client = TestClient(create_app(
        authenticator=lambda: auth,
        session_store=lambda: store,
        project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
    ), base_url="https://testserver")

    session = client.post(
        "/api/auth/client-session",
        json={"username": "coco", "password": "secret"},
    )

    assert session.status_code == 200
    assert auth.calls == []
    assert "smarttest_session=" in session.headers["set-cookie"]
    assert client.get("/api/test-suites?scope=mine").status_code == 200


def test_client_session_rejects_empty_credentials_without_ldap(
    tmp_path, *, database_factory
):
    auth = FakeAuthenticator()
    client = TestClient(
        create_app(
            authenticator=lambda: auth,
            session_store=lambda: PersistentSessionStore(database=database_factory()),
            project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
        ),
        base_url="https://testserver",
    )

    for payload in ({"username": "", "password": "secret"},
                    {"username": "coco", "password": ""}):
        response = client.post("/api/auth/client-session", json=payload)
        assert response.status_code == 422
        assert response.json()["detail"]["state"] == "invalid_input"
    assert auth.calls == []


def test_client_session_reports_credential_store_failure_without_ldap_or_secret(
    tmp_path, *, database_factory
):
    class FailingCredentialStore:
        def write(self, *_args):
            raise CredentialStoreError("secret-marker")

    auth = FakeAuthenticator()
    store = PersistentSessionStore(
        credential_store=FailingCredentialStore(), database=database_factory()
    )
    client = TestClient(create_app(
        authenticator=lambda: auth,
        session_store=lambda: store,
        project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
    ), base_url="https://testserver")

    response = client.post(
        "/api/auth/client-session",
        json={"username": "coco", "password": "secret-marker"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["state"] == "credential_store_unavailable"
    assert "secret-marker" not in response.text
    assert auth.calls == []


def test_http_suite_chain_emits_safe_readable_cookie_and_session_logs(
    tmp_path, monkeypatch, *, database_factory
):
    import smarttest_web.app as app_module

    messages = []
    monkeypatch.setattr(app_module, "smart_log",
                        lambda message, *args, **kwargs: messages.append(message % args))
    store = PersistentSessionStore(database=database_factory())
    auth = FakeAuthenticator({"success": True, "username": "coco",
                              "display_name": "Coco", "avatar_bytes": b""})
    client = TestClient(app_module.create_app(
        authenticator=lambda: auth,
        session_store=lambda: store,
        project_facts_owner=lambda: FakeFactsOwner({"projects": []}),
    ), base_url="http://testserver")
    login = client.post("/api/auth/login", json={"username": "coco", "password": "secret"},
                        headers={"x-request-id": "login-visible"})
    suites = client.get("/api/test-suites?scope=mine",
                        headers={"x-request-id": "list-visible"})
    assert login.status_code == suites.status_code == 200
    assert any("cookie_action=set secure=false request_id=login-visible" in line
               for line in messages)
    assert any("cookie_present=true session_found=true avatar_present=false request_id=list-visible" in line
               for line in messages)
    assert any("GET /api/test-suites 200 request_id=list-visible" in line
               for line in messages)
    rendered = repr(messages).lower()
    assert "secret" not in rendered
    assert "scope=mine" not in rendered
