from __future__ import annotations

from sqlalchemy import delete, text, case, func

from .database import WebDatabase, upsert
from .config import ApplicationDatabaseSettings, storage_environment
from .schema import user_preferences, web_account_identities, web_sessions

from dataclasses import dataclass
import hashlib
import json
import secrets
from threading import RLock
import time

from core.logging import smart_log
from core.product_lines import product_line_name, confluence_space_key
from .credentials import CredentialMissingError, CredentialStoreError, create_credential_store


SESSION_TTL_SECONDS = 90 * 24 * 60 * 60


@dataclass(frozen=True)
class WebSession:
    username: str
    display_name: str
    avatar_bytes: bytes
    expires_at: float
    password: str | None = None
    cookie_renewal_required: bool = False


class PersistentSessionStore:
    def __init__(self, *, ttl_seconds=SESSION_TTL_SECONDS,
                 refresh_interval_seconds=24 * 60 * 60, now=time.time, credential_store=None, database=None):
        self.database = database if database is not None else WebDatabase.from_settings(ApplicationDatabaseSettings.from_environment(storage_environment()))
        self._ttl = ttl_seconds
        self._refresh_interval = refresh_interval_seconds
        self._now = now
        self._credentials: dict[str, str] = {}
        self._lock = RLock()
        self._credential_store = credential_store or create_credential_store(self.database)

    @property
    def ttl_seconds(self):
        return self._ttl

    def _connect(self):
        return self.database.transaction()

    @staticmethod
    def _hash(token):
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _account_key(username):
        normalized = str(username or "").strip().casefold()
        return "account-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def saved_accounts(self):
        with self._connect() as connection:
            return tuple(row[0] for row in connection.execute(text('SELECT username FROM web_account_identities ORDER BY username')))

    def saved_credentials(self, username):
        normalized = str(username or "").strip().casefold()
        if not normalized:
            raise CredentialMissingError("Server credential was not found.")
        cached = self._credentials.get(normalized)
        if cached is not None:
            return normalized, cached
        stored_username, password = self._credential_store.read(self._account_key(normalized))
        if str(stored_username).strip().casefold() != normalized:
            raise CredentialMissingError("Server credential was not found.")
        self._credentials[normalized] = password
        return normalized, password

    def create(self, username, password, display_name="", avatar_bytes=b""):
        self.cleanup()
        normalized = str(username).strip().casefold()
        self._credential_store.write(self._account_key(normalized), normalized, password)
        self._credentials[normalized] = password
        self._store_identity(normalized, display_name, avatar_bytes)
        return self._create_session(normalized, display_name, avatar_bytes)

    def create_from_saved(self, username, display_name="", avatar_bytes=b""):
        normalized, _password = self.saved_credentials(username)
        self.cleanup()
        return self._create_session(normalized, display_name, avatar_bytes)

    def _store_identity(self, username, display_name, avatar_bytes):
        clean_name = str(display_name or username).strip() or username
        avatar = bytes(avatar_bytes) if isinstance(avatar_bytes, (bytes, bytearray)) else b""
        with self._lock, self._connect() as connection:
            upsert(
                connection,
                web_account_identities,
                {
                    "username": username,
                    "display_name": clean_name,
                    "avatar": avatar or None,
                    "updated_at": self._now(),
                },
                conflict_columns=("username",),
                updates=lambda incoming: {
                    "display_name": case(
                        (incoming.display_name == incoming.username, web_account_identities.c.display_name),
                        else_=incoming.display_name,
                    ),
                    "avatar": func.coalesce(incoming.avatar, web_account_identities.c.avatar),
                    "updated_at": incoming.updated_at,
                },
            )

    def _identity(self, username):
        with self._connect() as connection:
            row = connection.execute(
                text("SELECT display_name,avatar FROM web_account_identities WHERE username=:username"),
                {"username": username},
            ).fetchone()
        return (str(row[0] or "") if row else "", bytes(row[1] or b"") if row else b"")

    def has_cached_avatar(self, username):
        normalized = str(username or "").strip().casefold()
        _display_name, avatar = self._identity(normalized)
        return bool(avatar)

    def _create_session(self, username, display_name="", avatar_bytes=b""):
        cached_display_name, cached_avatar = self._identity(username)
        display_name = cached_display_name or display_name
        avatar_bytes = cached_avatar or avatar_bytes
        token = secrets.token_urlsafe(32)
        token_hash = self._hash(token)
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute(
                web_sessions.insert().values(
                    {
                        "token_hash": token_hash,
                        "username": str(username).strip().lower(),
                        "display_name": display_name or username,
                        "avatar": avatar_bytes or None,
                        "created_at": now,
                        "last_seen_at": now,
                        "expires_at": now + self._ttl,
                        "credential_ref": None,
                    }
                )
            )
        return token

    def get(self, token):
        if not token:
            return None
        token_hash = self._hash(token)
        now = self._now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                text(
                    "SELECT username,display_name,avatar,last_seen_at,expires_at,credential_ref FROM web_sessions WHERE token_hash=:token_hash AND revoked_at IS NULL"
                ),
                {"token_hash": token_hash},
            ).fetchone()
            if row is None:
                return None
            if row[4] <= now:
                connection.execute(
                    text("DELETE FROM web_sessions WHERE token_hash=:token_hash"), {"token_hash": token_hash}
                )
                connection.execute(
                    text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash"),
                    {"session_hash": token_hash},
                )
                return None
            expires_at = row[4]
            renewed = False
            if now - row[3] >= self._refresh_interval:
                expires_at = now + self._ttl
                connection.execute(
                    text(
                        "UPDATE web_sessions SET last_seen_at=:last_seen_at,expires_at=:expires_at WHERE token_hash=:token_hash"
                    ),
                    {"last_seen_at": now, "expires_at": expires_at, "token_hash": token_hash},
                )
                renewed = True
            normalized = str(row[0]).strip().casefold()
            password = self._credentials.get(normalized)
            if password is None:
                try:
                    _stored_username, password = self.saved_credentials(normalized)
                except CredentialMissingError:
                    if row[5]:
                        try:
                            _stored_username, password = self._credential_store.read(row[5])
                            self._credential_store.write(
                                self._account_key(normalized), normalized, password,
                            )
                            self._credentials[normalized] = password
                            self._delete_legacy_credential(row[5])
                            connection.execute(
                                text("UPDATE web_sessions SET credential_ref=NULL WHERE token_hash=:token_hash"),
                                {"token_hash": token_hash},
                            )
                        except CredentialStoreError as exc:
                            smart_log("Persistent Web credential recovery failed", platform="web", domain="auth",
                                      source="PersistentSessionStore", level="warning",
                                      extra={"exception_type": type(exc).__name__})
                except CredentialStoreError as exc:
                    smart_log("Persistent Web credential recovery failed", platform="web", domain="auth",
                              source="PersistentSessionStore", level="warning",
                              extra={"exception_type": type(exc).__name__})
            display_name, avatar_bytes = self._identity(normalized)
            return WebSession(row[0], display_name or row[1], avatar_bytes or row[2] or b"",
                              expires_at, password, renewed)

    def resource_access(self, token, platform, database):
        from .resource_access import ResourceAccess

        value = self.get(token)
        if value is None:
            raise PermissionError("reauthentication_required")
        access = ResourceAccess(database, value.username, platform, self._hash(token), now=self._now)
        access.require_active()
        return access

    def delete(self, token):
        token_hash = self._hash(token)
        with self._lock, self._connect() as connection:
            connection.execute(
                text(
                    "UPDATE web_sessions SET revoked_at=:revoked_at WHERE token_hash=:token_hash AND revoked_at IS NULL"
                ),
                {"revoked_at": self._now(), "token_hash": token_hash},
            )
            connection.execute(
                text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash"),
                {"session_hash": token_hash},
            )

    def delete_all(self, username):
        now = self._now()
        with self._lock, self._connect() as connection:
            token_hashes = [
                row[0]
                for row in connection.execute(
                    text("SELECT token_hash FROM web_sessions WHERE username=:username AND revoked_at IS NULL"),
                    {"username": username},
                )
            ]
            cursor = connection.execute(
                text(
                    "UPDATE web_sessions SET revoked_at=:revoked_at WHERE username=:username AND revoked_at IS NULL"
                ),
                {"revoked_at": now, "username": username},
            )
            _batch = [{"session_hash": token_hash} for token_hash in token_hashes]
            if _batch:
                connection.execute(text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash"), _batch)
        return cursor.rowcount

    def invalidate_credentials(self, username):
        normalized = str(username or "").strip().casefold()
        now = self._now()
        with self._lock, self._connect() as connection:
            token_hashes = [
                row[0]
                for row in connection.execute(
                    text("SELECT token_hash FROM web_sessions WHERE username=:username AND revoked_at IS NULL"),
                    {"username": normalized},
                )
            ]
            cursor = connection.execute(
                text(
                    "UPDATE web_sessions SET revoked_at=:revoked_at,revoked_reason='invalid_credentials' WHERE username=:username AND revoked_at IS NULL"
                ),
                {"revoked_at": now, "username": normalized},
            )
            _batch = [{"session_hash": token_hash} for token_hash in token_hashes]
            if _batch:
                connection.execute(text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash"), _batch)
        self._credentials.pop(normalized, None)
        self._credential_store.delete(self._account_key(normalized))
        return cursor.rowcount

    def rejection_state(self, token):
        token_hash = self._hash(token)
        with self._connect() as connection:
            row = connection.execute(
                text("SELECT revoked_reason FROM web_sessions WHERE token_hash=:token_hash"),
                {"token_hash": token_hash},
            ).fetchone()
        return str(row[0] or "") if row else ""

    def cleanup(self, limit=100):
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    text(
                        "SELECT id,token_hash FROM web_sessions WHERE expires_at<=:expires_at OR revoked_at IS NOT NULL LIMIT :limit"
                    ),
                    {"expires_at": self._now(), "limit": limit},
                )
            )
            cursor = connection.execute(delete(web_sessions).where(web_sessions.c.id.in_([row[0] for row in rows])))
        with self._connect() as connection:
            _batch = [{"session_hash": token_hash} for (_id, token_hash) in rows]
            if _batch:
                connection.execute(text("DELETE FROM web_query_snapshots WHERE session_hash=:session_hash"), _batch)
        return cursor.rowcount

    def _delete_legacy_credential(self, credential_ref):
        try:
            self._credential_store.delete(credential_ref)
        except CredentialStoreError as exc:
            smart_log("Persistent Web credential deletion failed", platform="web", domain="auth",
                      source="PersistentSessionStore", level="warning",
                      extra={"exception_type": type(exc).__name__})

    @property
    def count(self):
        self.cleanup()
        with self._connect() as connection:
            return connection.execute(
                text("SELECT count(*) FROM web_sessions WHERE revoked_at IS NULL AND expires_at>:expires_at"),
                {"expires_at": self._now()},
            ).fetchone()[0]

    def contains_token_hash(self, token):
        with self._connect() as connection:
            return (
                connection.execute(
                    text("SELECT 1 FROM web_sessions WHERE token_hash=:token_hash"),
                    {"token_hash": self._hash(token)},
                ).fetchone()
                is not None
            )

    def contains_raw_token(self, token):
        with self._connect() as connection:
            return (
                connection.execute(
                    text("SELECT 1 FROM web_sessions WHERE token_hash=:token_hash"), {"token_hash": token}
                ).fetchone()
                is not None
            )

    def get_preferences(self, username, scope):
        with self._connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT `key`,value_json,schema_version,updated_at FROM user_preferences WHERE username=:username AND scope=:scope"
                ),
                {"username": username, "scope": scope},
            ).fetchall()
        items = {key: json.loads(value) for key, value, _, _ in rows}
        if scope in ('projects.html', '/projects.html', 'confluence.html', '/confluence.html') and 'field.__product_space__' in items:
            items['field.__product_space__'] = [confluence_space_key(value) for value in items['field.__product_space__']]
        return {"scope": scope, "items": items,
                "schemaVersion": max((row[2] for row in rows), default=1),
                "updatedAt": max((row[3] for row in rows), default=None)}

    def upsert_preferences(self, username, scope, items, schema_version=1):
        now = self._now()
        items = dict(items)
        if scope in ('projects.html', '/projects.html', 'confluence.html', '/confluence.html') and 'field.__product_space__' in items:
            items['field.__product_space__'] = [product_line_name(value) for value in items['field.__product_space__']]
        with self._connect() as connection:
            for _row_username, _row_scope, _row_key, _row_value_json, _row_schema_version, _row_updated_at in [
                (
                    username,
                    scope,
                    key,
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    schema_version,
                    now,
                )
                for (key, value) in items.items()
            ]:
                upsert(
                    connection,
                    user_preferences,
                    {
                        "username": _row_username,
                        "scope": _row_scope,
                        "key": _row_key,
                        "value_json": _row_value_json,
                        "schema_version": _row_schema_version,
                        "updated_at": _row_updated_at,
                    },
                    conflict_columns=("username", "scope", "key"),
                    updates=("value_json", "schema_version", "updated_at"),
                )
        return self.get_preferences(username, scope)

    def delete_preferences(self, username, scope):
        with self._connect() as connection:
            return connection.execute(
                text("DELETE FROM user_preferences WHERE username=:username AND scope=:scope"),
                {"username": username, "scope": scope},
            ).rowcount


# Compatibility name for existing imports; the implementation is persistent.
InMemorySessionStore = PersistentSessionStore
