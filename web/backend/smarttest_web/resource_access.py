from __future__ import annotations

import time
from core.product_lines import product_line_name, confluence_space_key



class ResourceAccess:
    """Last remotely confirmed resources for one account and one live Web session."""

    def __init__(self, database, account, platform, session_hash, *, now=time.time):
        self.database, self.account, self.platform = database, account, platform
        self.session_hash, self._now = session_hash, now

    def require_active(self):
        with self.database.connect() as connection:
            valid = connection.execute(
                """SELECT 1 FROM web_sessions WHERE token_hash=? AND username=?
                   AND revoked_at IS NULL AND expires_at>?""",
                (self.session_hash, self.account, self._now()),
            ).fetchone()
        if not valid:
            raise PermissionError("reauthentication_required")

    def ids(self, kind, capability):
        self.require_active()
        with self.database.connect() as connection:
            values = {row[0] for row in connection.execute(
                "SELECT resource_id FROM web_resource_access WHERE account=? AND platform=? AND kind=? AND capability=?",
                (self.account, self.platform, kind, capability),
            )}
        return {confluence_space_key(value) for value in values} if self.platform.startswith('confluence:') and kind == 'catalog' else values

    def allows(self, kind, resource_id, capability):
        return str(resource_id) in self.ids(kind, capability)

    def scopes(self, kind, resource_id, capability):
        self.require_active()
        if self.platform.startswith('confluence:') and kind == 'catalog':
            resource_id = product_line_name(str(resource_id))
        with self.database.connect() as connection:
            values = {row[0] for row in connection.execute(
                "SELECT scope FROM web_resource_access WHERE account=? AND platform=? AND kind=? AND resource_id=? AND capability=?",
                (self.account, self.platform, kind, str(resource_id), capability),
            )}
        return {confluence_space_key(value) for value in values} if self.platform.startswith('confluence:') else values

    def require(self, kind, resource_id, capability):
        if not self.allows(kind, resource_id, capability):
            raise PermissionError("permission_denied")

    def publish(self, grants, write, *, replace_scopes=()):
        grants = tuple(grants)
        if self.platform.startswith('confluence:'):
            grants = tuple((kind, product_line_name(str(key)) if kind == 'catalog' else key,
                            capability, product_line_name(scope)) for kind, key, capability, scope in grants)
            replace_scopes = tuple(product_line_name(scope) for scope in replace_scopes)
        with self.database.transaction() as connection:
            self.require_active()
            retained = {str(key) for kind, key, capability, _ in grants if kind == 'project' and capability == 'catalog'}
            removed = set()
            for scope in replace_scopes:
                removed.update(row[0] for row in connection.execute(
                    "SELECT resource_id FROM web_resource_access WHERE account=? AND platform=? AND kind='project' AND capability='catalog' AND scope=?",
                    (self.account, self.platform, scope)))
            for key in removed - retained:
                connection.execute(
                    "DELETE FROM web_resource_access WHERE account=? AND platform=? AND ((kind='project' AND resource_id=?) OR scope=?)",
                    (self.account, self.platform, key, f'{key}:evidence'))
            connection.executemany(
                "DELETE FROM web_resource_access WHERE account=? AND platform=? AND scope=?",
                ((self.account, self.platform, scope) for scope in replace_scopes),
            )
            result = write()
            connection.executemany(
                "INSERT OR REPLACE INTO web_resource_access VALUES(?,?,?,?,?,?,?)",
                ((self.account, self.platform, kind, str(key), capability, scope, self._now())
                 for kind, key, capability, scope in grants),
            )
            return result

    def revoke(self, kind, resource_id, capability=None):
        if self.platform.startswith('confluence:') and kind == 'catalog':
            resource_id = product_line_name(str(resource_id))
        with self.database.transaction() as connection:
            self.require_active()
            connection.execute(
                "DELETE FROM web_resource_access WHERE account=? AND platform=? AND kind=? AND resource_id=?"
                + (" AND capability=?" if capability else ""),
                (self.account, self.platform, kind, str(resource_id)) + ((capability,) if capability else ()),
            )


def remote_status(error):
    while error is not None:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status is not None:
            return status
        error = error.__cause__
    return None


def remote_credentials_rejected(error):
    while error is not None:
        response = getattr(error, "response", None)
        if getattr(response, "status_code", None) == 401:
            headers = getattr(response, "headers", {}) or {}
            challenge = next((str(value) for key, value in headers.items()
                              if str(key).casefold() == "www-authenticate"), "")
            return challenge.strip().casefold().startswith("basic")
        error = error.__cause__
    return False
