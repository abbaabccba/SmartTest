from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
import time

from core.config.jsonTool import app_data_dir
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


def default_web_database_path() -> Path:
    return app_data_dir() / "web" / "smarttest-web.db"


class PersistentSessionStore:
    def __init__(self, path=None, *, ttl_seconds=SESSION_TTL_SECONDS,
                 refresh_interval_seconds=24 * 60 * 60, now=time.time, credential_store=None):
        self.path = Path(path or default_web_database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._refresh_interval = refresh_interval_seconds
        self._now = now
        self._credentials: dict[str, str] = {}
        self._lock = RLock()
        self._credential_store = credential_store or create_credential_store(self.path)

    @property
    def ttl_seconds(self):
        return self._ttl

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @property
    def journal_mode(self):
        with self._connect() as connection:
            return connection.execute("PRAGMA journal_mode").fetchone()[0]

    @staticmethod
    def _hash(token):
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _account_key(username):
        normalized = str(username or "").strip().casefold()
        return "account-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()

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
        cached_display_name, cached_avatar = self._identity(normalized)
        return self._create_session(
            normalized,
            cached_display_name or display_name,
            cached_avatar or avatar_bytes,
        )

    def _store_identity(self, username, display_name, avatar_bytes):
        clean_name = str(display_name or username).strip() or username
        avatar = bytes(avatar_bytes) if isinstance(avatar_bytes, (bytes, bytearray)) else b""
        with self._lock, self._connect() as connection:
            connection.execute("""
                INSERT INTO web_account_identities(username,display_name,avatar,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(username) DO UPDATE SET
                  display_name=excluded.display_name,
                  avatar=CASE WHEN excluded.avatar IS NOT NULL THEN excluded.avatar
                              ELSE web_account_identities.avatar END,
                  updated_at=excluded.updated_at
            """, (username, clean_name, avatar or None, self._now()))

    def _identity(self, username):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT display_name,avatar FROM web_account_identities WHERE username=?", (username,),
            ).fetchone()
        return (str(row[0] or "") if row else "", bytes(row[1] or b"") if row else b"")

    def has_cached_avatar(self, username):
        normalized = str(username or "").strip().casefold()
        _display_name, avatar = self._identity(normalized)
        return bool(avatar)

    def _create_session(self, username, display_name="", avatar_bytes=b""):
        token = secrets.token_urlsafe(32)
        token_hash = self._hash(token)
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO web_sessions(token_hash,username,display_name,avatar,created_at,last_seen_at,expires_at,credential_ref) VALUES(?,?,?,?,?,?,?,?)",
                (token_hash, str(username).strip().lower(), display_name or username, avatar_bytes or None,
                 now, now, now + self._ttl, None),
            )
        return token

    def get(self, token):
        if not token:
            return None
        token_hash = self._hash(token)
        now = self._now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT username,display_name,avatar,last_seen_at,expires_at,credential_ref FROM web_sessions WHERE token_hash=? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row[4] <= now:
                connection.execute("DELETE FROM web_sessions WHERE token_hash=?", (token_hash,))
                connection.execute("DELETE FROM web_query_snapshots WHERE session_hash=?", (token_hash,))
                connection.commit()
                return None
            expires_at = row[4]
            renewed = False
            if now - row[3] >= self._refresh_interval:
                expires_at = now + self._ttl
                connection.execute("UPDATE web_sessions SET last_seen_at=?,expires_at=? WHERE token_hash=?",
                                   (now, expires_at, token_hash))
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
                                "UPDATE web_sessions SET credential_ref=NULL WHERE token_hash=?",
                                (token_hash,),
                            )
                        except CredentialStoreError as exc:
                            smart_log("Persistent Web credential recovery failed", platform="web", domain="auth",
                                      source="PersistentSessionStore", level="warning",
                                      extra={"exception_type": type(exc).__name__})
                except CredentialStoreError as exc:
                    smart_log("Persistent Web credential recovery failed", platform="web", domain="auth",
                              source="PersistentSessionStore", level="warning",
                              extra={"exception_type": type(exc).__name__})
            return WebSession(row[0], row[1], row[2] or b"", expires_at, password, renewed)

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
            connection.execute("UPDATE web_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                               (self._now(), token_hash))
            connection.execute("DELETE FROM web_query_snapshots WHERE session_hash=?", (token_hash,))

    def delete_all(self, username):
        now = self._now()
        with self._lock, self._connect() as connection:
            token_hashes = [row[0] for row in connection.execute(
                "SELECT token_hash FROM web_sessions WHERE username=? AND revoked_at IS NULL", (username,))]
            cursor = connection.execute("UPDATE web_sessions SET revoked_at=? WHERE username=? AND revoked_at IS NULL",
                                        (now, username))
            connection.executemany("DELETE FROM web_query_snapshots WHERE session_hash=?",
                                   ((token_hash,) for token_hash in token_hashes))
        return cursor.rowcount

    def invalidate_credentials(self, username):
        normalized = str(username or "").strip().casefold()
        now = self._now()
        with self._lock, self._connect() as connection:
            token_hashes = [row[0] for row in connection.execute(
                "SELECT token_hash FROM web_sessions WHERE username=? AND revoked_at IS NULL",
                (normalized,),
            )]
            cursor = connection.execute(
                "UPDATE web_sessions SET revoked_at=?,revoked_reason='invalid_credentials' "
                "WHERE username=? AND revoked_at IS NULL",
                (now, normalized),
            )
            connection.executemany("DELETE FROM web_query_snapshots WHERE session_hash=?",
                                   ((token_hash,) for token_hash in token_hashes))
        self._credentials.pop(normalized, None)
        self._credential_store.delete(self._account_key(normalized))
        return cursor.rowcount

    def rejection_state(self, token):
        token_hash = self._hash(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revoked_reason FROM web_sessions WHERE token_hash=?", (token_hash,),
            ).fetchone()
        return str(row[0] or "") if row else ""

    def cleanup(self, limit=100):
        with self._connect() as connection:
            rows = list(connection.execute(
                "SELECT id,token_hash FROM web_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL LIMIT ?",
                (self._now(), limit),
            ))
            cursor = connection.execute(
                "DELETE FROM web_sessions WHERE id IN (SELECT id FROM web_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL LIMIT ?)",
                (self._now(), limit),
            )
        with self._connect() as connection:
            connection.executemany("DELETE FROM web_query_snapshots WHERE session_hash=?",
                                   ((token_hash,) for _id, token_hash in rows))
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
            return connection.execute("SELECT count(*) FROM web_sessions WHERE revoked_at IS NULL AND expires_at>?", (self._now(),)).fetchone()[0]

    def contains_token_hash(self, token):
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM web_sessions WHERE token_hash=?", (self._hash(token),)).fetchone() is not None

    def contains_raw_token(self, token):
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM web_sessions WHERE token_hash=?", (token,)).fetchone() is not None

    def get_preferences(self, username, scope):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key,value_json,schema_version,updated_at FROM user_preferences WHERE username=? AND scope=?",
                (username, scope),
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
            connection.executemany("""
                INSERT INTO user_preferences(username,scope,key,value_json,schema_version,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(username,scope,key) DO UPDATE SET
                  value_json=excluded.value_json,schema_version=excluded.schema_version,updated_at=excluded.updated_at
            """, [(username, scope, key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), schema_version, now)
                    for key, value in items.items()])
        return self.get_preferences(username, scope)

    def delete_preferences(self, username, scope):
        with self._connect() as connection:
            return connection.execute("DELETE FROM user_preferences WHERE username=? AND scope=?", (username, scope)).rowcount


# Compatibility name for existing imports; the implementation is persistent.
InMemorySessionStore = PersistentSessionStore
