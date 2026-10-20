from fastapi.testclient import TestClient

from smarttest_web.app import create_app
from smarttest_web.session import PersistentSessionStore
from test_web_session import FakeAuthenticator, FakeFactsOwner


def client_for(tmp_path, username="coco", *, database_factory):
    store = PersistentSessionStore(database=database_factory())
    auth = FakeAuthenticator(
        {
            "success": True,
            "username": username,
            "display_name": username,
            "avatar_bytes": b"",
        }
    )
    client = TestClient(create_app(authenticator=lambda: auth, session_store=lambda: store,
                                   project_facts_owner=lambda: FakeFactsOwner({"projects": []})),
                        base_url="https://testserver")
    client.post("/api/auth/login", json={"username": username, "password": "secret"})
    return client, store


def test_preferences_are_account_scoped_and_last_write_wins(
    tmp_path, *, database_factory
):
    coco, store = client_for(tmp_path, "coco", database_factory=database_factory)
    atlas, _ = client_for(tmp_path, "atlas", database_factory=database_factory)
    payload = {"items": {"theme": "dark", "filters": ["ready"]}, "schemaVersion": 1}
    assert coco.put("/api/preferences/global", json=payload).status_code == 200
    assert coco.get("/api/preferences/global").json()["items"] == payload["items"]
    assert atlas.get("/api/preferences/global").json()["items"] == {}
    coco.put("/api/preferences/global", json={"items": {"theme": "light"}})
    assert coco.get("/api/preferences/global").json()["items"]["theme"] == "light"
    assert store.database.dialect == "mysql"


def test_preference_reset_and_authentication_boundary(tmp_path, *, database_factory):
    client, store = client_for(tmp_path, database_factory=database_factory)
    client.put("/api/preferences/wifi.rvr", json={"items": {"standards": ["11be"]}})
    assert client.delete("/api/preferences/wifi.rvr").json()["deleted"] == 1
    assert client.get("/api/preferences/wifi.rvr").json()["items"] == {}
    anonymous = TestClient(create_app(session_store=lambda: store), base_url="https://testserver")
    assert anonymous.get("/api/preferences/global").status_code == 401


def test_sensitive_invalid_and_oversized_preferences_are_rejected(
    tmp_path, *, database_factory
):
    client, _ = client_for(tmp_path, database_factory=database_factory)
    for items in (
        {"password": "secret"},
        {"apiToken": "secret"},
        {"cookie_value": "secret"},
        {"form": {"credential": "secret"}},
    ):
        assert client.put("/api/preferences/global", json={"items": items}).status_code == 422
    assert client.put("/api/preferences/global", json={"items": {"x": "x" * 70000}}).status_code == 413


def test_schema_gate_is_idempotent_and_upsert_is_atomic(tmp_path, *, database_factory):
    path = tmp_path / "web.db"
    first = PersistentSessionStore(database=database_factory())
    first.upsert_preferences("coco", "global", {"theme": "dark"}, 1)
    second = PersistentSessionStore(database=database_factory())
    second.upsert_preferences("coco", "global", {"theme": "light", "compact": True}, 2)
    assert second.get_preferences("coco", "global")["items"] == {"compact": True, "theme": "light"}
    from smarttest_web.schema import verify_schema_revision

    verify_schema_revision(second.database)
    verify_schema_revision(second.database)
