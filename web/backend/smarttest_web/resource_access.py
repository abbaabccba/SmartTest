from __future__ import annotations

from sqlalchemy import select, literal, delete, text
from .schema import web_sessions, web_resource_access


from .database import upsert

import time
from core.product_lines import product_line_name, confluence_space_key



class ResourceAccess:
    """Last remotely confirmed resources for one account and one live Web session."""

    def __init__(self, database, account, platform, session_hash, *, now=time.time):
        self.database, self.account, self.platform = database, account, platform
        self.session_hash, self._now = session_hash, now

    def require_active(self):
        with self.database.connect() as connection:
            valid = connection.execute(select(literal(1)).select_from(web_sessions).where(
                web_sessions.c.token_hash == self.session_hash, web_sessions.c.username == self.account,
                web_sessions.c.revoked_at.is_(None), web_sessions.c.expires_at > self._now()).with_for_update()).first()
        if not valid:
            raise PermissionError('reauthentication_required')

    def ids(self, kind, capability):
        self.require_active()
        with self.database.connect() as connection:
            values = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT resource_id FROM web_resource_access WHERE account=:account AND platform=:platform AND kind=:kind AND capability=:capability"
                    ),
                    {
                        "account": self.account,
                        "platform": self.platform,
                        "kind": kind,
                        "capability": capability,
                    },
                )
            }
        return {confluence_space_key(value) for value in values} if self.platform.startswith('confluence:') and kind == 'catalog' else values

    def allows(self, kind, resource_id, capability):
        return str(resource_id) in self.ids(kind, capability)

    def scopes(self, kind, resource_id, capability):
        self.require_active()
        if self.platform.startswith('confluence:') and kind == 'catalog':
            resource_id = product_line_name(str(resource_id))
        with self.database.connect() as connection:
            values = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT scope FROM web_resource_access WHERE account=:account AND platform=:platform AND kind=:kind AND resource_id=:resource_id AND capability=:capability"
                    ),
                    {
                        "account": self.account,
                        "platform": self.platform,
                        "kind": kind,
                        "resource_id": str(resource_id),
                        "capability": capability,
                    },
                )
            }
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
                removed.update(
                    (
                        row[0]
                        for row in connection.execute(
                            text(
                                "SELECT resource_id FROM web_resource_access WHERE account=:account AND platform=:platform AND kind='project' AND capability='catalog' AND scope=:scope"
                            ),
                            {"account": self.account, "platform": self.platform, "scope": scope},
                        )
                    )
                )
            for key in removed - retained:
                connection.execute(
                    text(
                        "DELETE FROM web_resource_access WHERE account=:account AND platform=:platform AND ((kind='project' AND resource_id=:resource_id) OR scope=:scope)"
                    ),
                    {
                        "account": self.account,
                        "platform": self.platform,
                        "resource_id": key,
                        "scope": f"{key}:evidence",
                    },
                )
            _batch = [{"account": self.account, "platform": self.platform, "scope": scope} for scope in replace_scopes]
            if _batch:
                connection.execute(
                    text("DELETE FROM web_resource_access WHERE account=:account AND platform=:platform AND scope=:scope"),
                    _batch,
                )
            result = write()
            for kind, key, capability, scope in grants:
                upsert(
                    connection,
                    web_resource_access,
                    {
                        "account": self.account,
                        "platform": self.platform,
                        "kind": kind,
                        "resource_id": str(key),
                        "capability": capability,
                        "scope": scope,
                        "confirmed_at": self._now(),
                    },
                    conflict_columns=("account", "platform", "kind", "resource_id", "capability", "scope"),
                    updates=("confirmed_at",),
                )
            return result

    def revoke(self, kind, resource_id, capability=None):
        if self.platform.startswith('confluence:') and kind == 'catalog':
            resource_id = product_line_name(str(resource_id))
        with self.database.transaction() as connection:
            self.require_active()
            conditions = [web_resource_access.c.account == self.account, web_resource_access.c.platform == self.platform,
                          web_resource_access.c.kind == kind, web_resource_access.c.resource_id == str(resource_id)]
            if capability:
                conditions.append(web_resource_access.c.capability == capability)
            connection.execute(delete(web_resource_access).where(*conditions))


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
