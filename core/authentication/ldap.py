from __future__ import annotations

import errno
import os
import socket
import ssl
import time
from sys import platform as sys_platform
from typing import Any

from core.logging import smart_log

try:
    from ldap3 import ALL, NTLM, SUBTREE, Connection, Server
    from ldap3.core.exceptions import LDAPResponseTimeoutError, LDAPSessionTerminatedByServerError
except ImportError:  # pragma: no cover - runtime dependency
    ALL = NTLM = SUBTREE = Connection = Server = None

DIRECT_ATTEMPTS = 3
DIRECT_RETRY_INTERVAL_SECONDS = 2
CONNECT_TIMEOUT_SECONDS = 5
RECEIVE_TIMEOUT_SECONDS = 5
PAC_TIMEOUT_SECONDS = 2
PROXY_ATTEMPTS = 1


class WindowsPacError(Exception):
    """Safe machine-readable PAC/transport failure detail."""


def _network_failure(error):
    # ldap3's communication_exception_factory preserves socket exception types;
    # multi-address failures retain their individual exceptions in args.
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, (ConnectionError, TimeoutError, socket.gaierror,
                          LDAPResponseTimeoutError, LDAPSessionTerminatedByServerError)):
        return True
    if isinstance(error, OSError) and (error.errno in {
        errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EPIPE,
    } or getattr(error, "winerror", None) in {
        10050, 10051, 10052, 10053, 10054, 10060, 10061, 10064, 10065, 11001, 11002,
    }):
        return True
    nested = [item for arg in error.args if isinstance(arg, (tuple, list)) for item in arg]
    causes = [item[0] if isinstance(item, (tuple, list)) and item else item for item in nested]
    exceptions = [item for item in causes if isinstance(item, BaseException)]
    if exceptions:
        return all(_network_failure(item) for item in exceptions)
    cause = error.__cause__ or error.__context__
    return cause is not None and _network_failure(cause)


class LdapAuthenticator:
    """Shared LDAP bind and identity owner used by Client and Web."""

    def __init__(self, *, host=None, domain=None, server_factory=Server,
                 connection_factory=Connection, authentication=NTLM,
                 get_info=ALL, subtree=SUBTREE, platform="client", windows_pac=False):
        self.host = (host or os.getenv("AMLOGIC_LDAP_HOST", "ldap.amlogic.com")).strip()
        self.domain = domain or os.getenv("AMLOGIC_LDAP_DOMAIN", "AMLOGIC")
        self._server = server_factory
        self._connection = connection_factory
        self._authentication = authentication
        self._get_info = get_info
        self._subtree = subtree
        self._platform = platform
        self._windows_pac = windows_pac and platform == "client" and sys_platform == "win32"

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        clean_username = (username or "").strip()
        if not clean_username or not password:
            return {"success": False, "username": "", "code": "invalid_credentials", "detail": "username_or_password_empty"}
        if not all((self._server, self._connection, self._authentication, self._get_info)):
            return {"success": False, "username": "", "code": "ldap_unavailable", "detail": "ldap3_not_installed"}
        attempts = DIRECT_ATTEMPTS if self._windows_pac else 1
        for attempt in range(attempts):
            result, retry = self._attempt(clean_username, password)
            if not retry:
                return result
            smart_log("LDAP direct network failure (attempt=%s/%s)", attempt + 1, attempts,
                      platform=self._platform, domain="auth", source="LdapAuthenticator", level="warning")
            if attempt + 1 < attempts:
                time.sleep(DIRECT_RETRY_INTERVAL_SECONDS)
        if self._windows_pac:
            smart_log("LDAP direct attempts exhausted; trying Windows PAC SOCKS5",
                      platform=self._platform, domain="auth", source="LdapAuthenticator")
            for _ in range(PROXY_ATTEMPTS):
                result, _retry = self._attempt(clean_username, password, use_pac=True)
        return result

    def _attempt(self, clean_username, password, *, use_pac=False):
        domain_user = clean_username if "\\" in clean_username or "@" in clean_username else f"{self.domain}\\{clean_username}"
        connection = None
        bound = False
        try:
            server_options = {"connect_timeout": CONNECT_TIMEOUT_SECONDS} if self._windows_pac else {}
            connection_options = {"receive_timeout": RECEIVE_TIMEOUT_SECONDS} if self._windows_pac else {}
            server = self._server(self.host, get_info=self._get_info, **server_options)
            connection = self._connection(server, user=domain_user, password=password,
                                          authentication=self._authentication, **connection_options)
            if use_pac:
                from .windows_pac import configure_connection
                configure_connection(connection, connect_timeout=CONNECT_TIMEOUT_SECONDS,
                                     receive_timeout=RECEIVE_TIMEOUT_SECONDS, pac_timeout=PAC_TIMEOUT_SECONDS)
            elif self._windows_pac and not server.candidate_addresses():
                # ldap3 discards getaddrinfo errors; preserve DNS failure classification.
                raise socket.gaierror(socket.EAI_NONAME, "LDAP hostname resolution failed")
            if not connection.bind():
                smart_log("LDAP bind failed (username=%s, server=%s)", domain_user, self.host,
                          platform=self._platform, domain="auth", source="LdapAuthenticator", level="warning")
                result = connection.result or {}
                detail = " | ".join(str(result.get(key) or "").strip() for key in ("description", "message") if str(result.get(key) or "").strip())
                code = "invalid_credentials" if not self._windows_pac or result.get("result") == 49 else "ldap_unavailable"
                return {"success": False, "username": "", "code": code, "detail": detail or "ldap_bind_failed"}, False
            bound = True
            identity = self._identity(connection, clean_username)
            smart_log("LDAP bind success (username=%s, server=%s)", domain_user, self.host,
                      platform=self._platform, domain="auth", source="LdapAuthenticator")
            return {"success": True, "username": clean_username, "detail": "", **identity}, False
        except Exception as exc:  # noqa: BLE001
            detail = str(exc) if isinstance(exc, WindowsPacError) else type(exc).__name__
            smart_log("LDAP connection failed (username=%s, server=%s, error=%s)", clean_username, self.host,
                      detail, platform=self._platform, domain="auth", source="LdapAuthenticator", level="error")
            return ({"success": False, "username": "", "code": "ldap_unavailable", "detail": detail},
                    self._windows_pac and not use_pac and not bound
                    and not bool(getattr(connection, "bound", False)) and _network_failure(exc))
        finally:
            if connection is not None:
                try:
                    connection.unbind()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    if self._windows_pac and getattr(connection, "socket", None) is not None:
                        try:
                            connection.socket.close()
                        except OSError:
                            pass

    def _identity(self, connection, username):
        if self._subtree is None:
            return {"display_name": "", "avatar_bytes": b""}
        contexts = list((connection.server.info.other or {}).get("defaultNamingContext") or [])
        if not contexts:
            return {"display_name": "", "avatar_bytes": b""}
        account = username.split("\\")[-1].split("@")[0].strip()
        escaped_account = _escape(account)
        escaped_username = _escape(username)
        if not connection.search(search_base=str(contexts[0]),
                                 search_filter=f"(|(sAMAccountName={escaped_account})(userPrincipalName={escaped_username})(mail={escaped_username}))",
                                 search_scope=self._subtree,
                                 attributes=["displayName", "thumbnailPhoto", "jpegPhoto"], size_limit=1) or not connection.entries:
            return {"display_name": "", "avatar_bytes": b""}
        entry = connection.entries[0]
        attrs = {name: entry[name].value if name in entry else None for name in ("displayName", "thumbnailPhoto", "jpegPhoto")}
        return ldap_identity_from_attributes(attrs)


def _escape(value):
    return str(value).replace("\\", r"\5c").replace("*", r"\2a").replace("(", r"\28").replace(")", r"\29").replace("\0", r"\00")


def ldap_identity_from_attributes(attributes):
    avatar = next((value[0] if isinstance(value, list) and value and isinstance(value[0], bytes) else value
                   for value in (attributes.get("thumbnailPhoto"), attributes.get("jpegPhoto"))
                   if isinstance(value, bytes) or (isinstance(value, list) and value and isinstance(value[0], bytes))), b"")
    return {"display_name": str(attributes.get("displayName") or "").strip(), "avatar_bytes": avatar}
