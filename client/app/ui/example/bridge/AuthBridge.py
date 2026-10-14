from __future__ import annotations

import os
import base64
import ctypes
import hashlib
import json
from ctypes import wintypes
from pathlib import Path
from sys import platform
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from core.config import jsonTool
from core.logging import smart_log
from .QtTaskAdapter import QtTaskAdapter
from .desktop_tasks import DESKTOP_TASKS
from core.authentication import LdapAuthenticator, ldap_identity_from_attributes
from core.credentials import CredentialError, desktop_credential_store
from core.credentials.windows import CredentialNotFoundError
from .ToolBridge import amlogic_employees
from .auth_accounts import AuthAccountStore, account_id_for_username
from client.app.data_sources.common import AuthenticatedCredentials

try:
    from example.helper.AppPaths import app_data_dir
except ImportError:  # pragma: no cover - direct unit-test imports may use the ui.example package path
    from client.app.ui.example.helper.AppPaths import app_data_dir

try:
    from ldap3 import ALL, NTLM, SUBTREE, Connection, Server
    from ldap3.core.exceptions import LDAPException
except ImportError:  # pragma: no cover - runtime dependency
    ALL = None
    NTLM = None
    SUBTREE = None
    Connection = None
    Server = None

    class LDAPException(Exception):
        pass


LDAP_HOST = os.getenv("AMLOGIC_LDAP_HOST", "ldap.amlogic.com")
LDAP_DOMAIN = os.getenv("AMLOGIC_LDAP_DOMAIN", "AMLOGIC")
AUTH_STATE_FILENAME = "auth_state.json"
AUTH_SECRET_FILENAME = "auth_secret.json"
_AUTH_SECRET_ENTROPY = b"SmartTest.Auth.SecretStore.v1"


def load_personnel(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    return payload if isinstance(payload, dict) else {}


def initials_from_name(display_name: str) -> str:
    words = str(display_name or "").split()
    return "".join(word[0].upper() for word in words[:2] if word)


def match_employee_profile(
    personnel: dict[str, Any], ldap_display_name: str, *, username: str = ""
) -> dict[str, Any]:
    display_name = str(ldap_display_name or "").strip()
    employees = amlogic_employees(personnel)
    employee = next(
        (item for item in employees if str(item.get("display_name", "")).strip() == display_name),
        None,
    ) if display_name else None
    if employee is None and username:
        account_matches = [
            item
            for item in employees
            if str(item.get("account", "") or "") == username
        ]
        employee = account_matches[0] if len(account_matches) == 1 else None
    if employee is None:
        return {}
    employment = employee.get("employment", {}) or {}
    organization = employee.get("organization", {}) or {}
    grade = str(employment.get("grade", "") or "")
    career_level = next(
        (
            item
            for item in (personnel.get("amlogic") or {}).get("career_levels", [])
            if isinstance(item, dict) and str(item.get("grade", "") or "") == grade
        ),
        {},
    )
    product_names = {
        str(item.get("id", "") or ""): str(item.get("name", "") or "")
        for item in (personnel.get("amlogic") or {}).get("product_lines", [])
        if isinstance(item, dict)
    }
    assignments = employee.get("assignments", []) or []
    return {
        "display_name": str(employee.get("display_name", "") or ""),
        "grade": grade,
        "job_title": str(
            employment.get("job_title_override", "") or career_level.get("job_title", "") or ""
        ),
        "department": str(organization.get("department", "") or ""),
        "team": str(organization.get("team", "") or ""),
        "division": str(organization.get("division", "") or ""),
        "employee_type": str(employment.get("employee_type", "") or ""),
        "product_lines": [
            product_names.get(str(item.get("product_line_id", "") or ""), "")
            for item in assignments
            if isinstance(item, dict) and product_names.get(str(item.get("product_line_id", "") or ""), "")
        ],
        "roles": [str(role) for role in employee.get("system_roles", []) or []],
        "reports_to": str(employee.get("reports_to", "") or ""),
    }


LDAP_CREDENTIAL_PREFIX = "SmartTest/Auth/"


class AuthBridge(QObject):
    authChanged = Signal()
    preferencesChanged = Signal()
    authenticationCompleted = Signal("QVariantMap")
    _authenticationFinished = Signal(object)

    def __init__(
        self,
        project_root: Path | None = None,
        state_root: Path | None = None,
        credential_store=None,
        authentication_runner=None,
        task_adapter=None,
    ):
        super().__init__(QGuiApplication.instance())
        self._project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[5]
        self._state_root = Path(state_root) if state_root else app_data_dir()
        self._personnel = load_personnel(self._project_root / "core" / "config" / "personnel.json")
        self._username = ""
        self._authenticated = False
        self._password = ""
        self._display_name = ""
        self._profile: dict[str, Any] = {}
        self._avatar_url = ""
        self._account_store = AuthAccountStore(self._state_root)
        self._credential_store = credential_store or desktop_credential_store(target_prefix=LDAP_CREDENTIAL_PREFIX)
        self._migrate_legacy_state()
        self._selected_account_id = self._account_store.last_account_id
        self._auth_state = "credential_required" if self._selected_account_id else "signed_out"
        self._auth_busy = False
        self._remember_password = False
        self._startup_session_restored = False
        self._authentication_generation = 0
        self._pending_authentications: dict[int, dict[str, Any]] = {}
        self._authentication_runner = authentication_runner
        self._tasks = task_adapter or QtTaskAdapter(manager=DESKTOP_TASKS)
        self._ldap_owner = LdapAuthenticator(
            host=LDAP_HOST, domain=LDAP_DOMAIN, server_factory=Server,
            connection_factory=Connection, authentication=NTLM, get_info=ALL,
            subtree=SUBTREE,
        )
        self._authenticationFinished.connect(self._finish_authentication)
        selected = self._account_store.get(self._selected_account_id)
        if selected:
            self._remember_password = bool(selected["remember_password"])
            self._username = str(selected["username"])
        self._resolve_profile()
        self._avatar_url = self._avatar_url_for_username(self._username)
        self._log_auth_event(
            "state_restored",
            account=self._username or "<none>",
            remember=self._remember_password,
            authenticated=self._authenticated,
        )

    def _log_auth_event(self, event: str, **fields: Any) -> None:
        extra = {"event": event, **fields}
        visible = ", ".join(
            f"{key}={fields[key]}" for key in ("code", "source", "success", "reason")
            if key in fields
        )
        smart_log(
            "Authentication transition (event=%s, account=%s%s)",
            event,
            fields.get("account", self._username or "<none>"),
            f", {visible}" if visible else "",
            domain="ui",
            source="AuthBridge",
            extra=extra,
        )

    def _auth_state_path(self) -> Path:
        return self._state_root / AUTH_STATE_FILENAME

    def _auth_secret_path(self) -> Path:
        return self._state_root / AUTH_SECRET_FILENAME

    def _avatar_dir(self) -> Path:
        return self._state_root / "avatars"

    def _resolve_profile(self) -> None:
        self._profile = match_employee_profile(
            self._personnel,
            self._display_name,
            username=self._username,
        )
        if self._profile:
            self._display_name = self._profile["display_name"]

    def _migrate_legacy_state(self) -> None:
        state_path = self._auth_state_path()
        if not state_path.exists():
            return
        wrote_new_credential = False
        account_id = ""
        try:
            data = jsonTool.read_json(state_path, {})
            username = str(data.get("username", "") or "").strip()
            if not username:
                return
            password = self._load_password_secret()
            remember_password = False
            account_id = account_id_for_username(username)
            if password:
                try:
                    self._credential_store.read(account_id)
                except (CredentialNotFoundError, KeyError):
                    self._credential_store.write(account_id, username, password)
                    wrote_new_credential = True
                remember_password = True
            self._account_store.record_login(
                username,
                str(data.get("display_name", "") or username),
                remember_password,
            )
            state_path.unlink(missing_ok=True)
            self._auth_secret_path().unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - migration must preserve source on any failure
            if wrote_new_credential and account_id:
                self._credential_delete(account_id)
            smart_log(
                "Legacy authentication state migration deferred (account=%s, error=%s)",
                str(data.get("username", "") or "<unknown>") if isinstance(locals().get("data"), dict) else "<unknown>",
                type(exc).__name__,
                level="warning",
                domain="ui",
                source="AuthBridge",
            )
    def _load_password_secret(self) -> str:
        path = self._auth_secret_path()
        if not path.exists():
            return ""
        try:
            payload = jsonTool.read_json(path, {})
            encrypted_value = str(payload.get("encrypted_password", "") or "").strip()
            if not encrypted_value:
                return ""
            protected_bytes = base64.b64decode(encrypted_value.encode("ascii"))
            return _dpapi_unprotect(protected_bytes).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            smart_log("Failed to load auth secret file %s: %s", path, exc, level="warning")
            return ""

    def _avatar_path_for_username(self, username: str) -> Path:
        digest = hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()[:24]
        return self._avatar_dir() / f"{digest}.jpg"

    def _avatar_url_for_username(self, username: str) -> str:
        clean_username = (username or "").strip()
        if not clean_username:
            return ""
        path = self._avatar_path_for_username(clean_username)
        return path.as_uri() if path.exists() else ""

    def _set_avatar_bytes(self, username: str, avatar_bytes: bytes) -> str:
        if not username or not avatar_bytes:
            return ""
        path = self._avatar_path_for_username(username)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(avatar_bytes)
            return path.as_uri()
        except Exception as exc:  # noqa: BLE001
            smart_log("Failed to cache LDAP avatar %s: %s", path, exc, level="warning")
            return ""

    def _set_auth_state(
        self, *, username: str, authenticated: bool, password: str = "", display_name: str = ""
    ) -> None:
        next_username = (username or "").strip() if authenticated else ""
        next_password = password if authenticated else ""
        next_display_name = (display_name or "").strip() if authenticated else ""
        changed = (
            self._username != next_username
            or self._authenticated != authenticated
            or self._display_name != next_display_name
        )
        self._username = next_username
        self._password = next_password if authenticated else ""
        self._authenticated = authenticated and bool(next_username)
        self._display_name = next_display_name or next_username
        self._resolve_profile()
        if self._profile:
            self._display_name = self._profile["display_name"]
        self._avatar_url = self._avatar_url_for_username(next_username) if self._authenticated else ""
        if self._authenticated:
            self._auth_state = "authenticated"
        else:
            self._auth_state = "signed_out"
        if changed:
            self.authChanged.emit()

    def _ldap_authenticate(self, username: str, password: str) -> dict[str, Any]:
        return self._ldap_owner.authenticate(username, password)

    def _get_authenticated(self) -> bool:
        return self._authenticated

    def runtime_credentials(self) -> AuthenticatedCredentials | None:
        if not self._authenticated or not self._username or not self._password:
            return None
        return AuthenticatedCredentials(self._username, self._password)

    def invalidate_runtime_credentials(self, code: str) -> bool:
        if str(code or "") != "invalid_credentials":
            return False
        account_id = self._selected_account_id
        item = self._account_store.get(account_id) if account_id else None
        if (not self._authenticated and not self._password
                and not self._account_store.active_account_id
                and not bool(item and item.get("remember_password"))):
            return False
        self._authentication_generation += 1
        self._pending_authentications.clear()
        self._auth_busy = False
        cleanup_ok = self._credential_delete(account_id) if account_id else True
        if account_id:
            self._account_store.set_remember_password(account_id, False)
            self._account_store.clear_active_account()
            if cleanup_ok:
                self._account_store.clear_credential_cleanup(account_id)
            else:
                self._account_store.mark_credential_cleanup(account_id)
        self._clear_session()
        self._remember_password = False
        if item:
            self._username = str(item["username"])
            self._display_name = str(item.get("display_name", "") or self._username)
            self._resolve_profile()
            self._avatar_url = self._avatar_url_for_username(self._username)
            self._auth_state = "credential_required"
        else:
            self._auth_state = "signed_out"
        self.authChanged.emit()
        self.preferencesChanged.emit()
        self._log_auth_event(
            "runtime_credential_invalidated",
            account=str(item.get("username", "")) if item else "<none>",
            code="invalid_credentials",
            success=cleanup_ok,
        )
        return True

    @Slot(result=str)
    def currentUsername(self) -> str:
        return self._username

    def _get_username(self) -> str:
        return self._username

    def _get_page_state_account(self) -> str:
        if not self._authenticated:
            return ""
        account = str(self._username or "").strip().casefold().rsplit("\\", 1)[-1]
        return account.split("@", 1)[0].strip()

    def _get_avatar_url(self) -> str:
        return self._avatar_url

    def _get_display_name(self) -> str:
        return self._display_name or self._username

    def _get_initials(self) -> str:
        return initials_from_name(self._get_display_name())

    def _profile_value(self, key: str) -> str:
        return str(self._profile.get(key, "") or "")

    def _get_grade(self) -> str:
        return self._profile_value("grade")

    def _get_job_title(self) -> str:
        return self._profile_value("job_title")

    def _get_department(self) -> str:
        return self._profile_value("department")

    def _get_team(self) -> str:
        return self._profile_value("team")

    def _get_reports_to(self) -> str:
        return self._profile_value("reports_to")

    def _get_product_lines(self) -> list[str]:
        return list(self._profile.get("product_lines", []) or [])

    def _get_role_text(self) -> str:
        roles = self._profile.get("roles", []) or []
        return self._get_job_title() or (str(roles[0]) if roles else self._profile_value("employee_type"))

    @Slot(result=str)
    def ldapServer(self) -> str:
        return LDAP_HOST

    def _account_models(self) -> list[dict[str, Any]]:
        return [
            {
                "accountId": item["account_id"],
                "username": item["username"],
                "displayName": item["display_name"],
                "label": f'{item["display_name"]} ({item["username"]})' + ("  🔒" if item["remember_password"] else ""),
                "rememberPassword": item["remember_password"],
                "avatarUrl": self._avatar_url_for_username(item["username"]),
                "isCurrent": self._authenticated and item["account_id"] == self._selected_account_id,
                "isLastUsed": item["account_id"] == self._account_store.last_account_id,
            }
            for item in self._account_store.accounts()
        ]

    def _get_accounts(self) -> list[dict[str, Any]]:
        return self._account_models()

    def _get_selected_account_id(self) -> str:
        return self._selected_account_id

    def _get_auth_state_name(self) -> str:
        return self._auth_state

    def _get_auth_busy(self) -> bool:
        return self._auth_busy

    def _get_remember_password(self) -> bool:
        return self._remember_password

    def _get_has_saved_credential(self) -> bool:
        item = self._account_store.get(self._selected_account_id) if self._selected_account_id else None
        return bool(item and item.get("remember_password"))

    def _credential_delete(self, account_id: str) -> bool:
        try:
            self._credential_store.delete(account_id)
            return True
        except (CredentialNotFoundError, KeyError):
            return True
        except CredentialError:
            return False

    def _clear_session(self, *, keep_selection: bool = True) -> None:
        self._password = ""
        self._authenticated = False
        self._display_name = ""
        self._profile = {}
        self._avatar_url = ""
        if not keep_selection:
            self._username = ""
            self._selected_account_id = ""

    def _begin_authentication(
        self,
        username: str,
        password: str,
        remember_password: bool,
        *,
        saved_credential: bool = False,
        source: str = "manual",
    ) -> dict[str, Any]:
        self._authentication_generation += 1
        generation = self._authentication_generation
        self._auth_busy = True
        self._auth_state = "authenticating"
        self.authChanged.emit()
        completed = {}
        target_account_id = account_id_for_username(username)
        self._pending_authentications[generation] = {
            "username": username,
            "password": password,
            "remember_password": remember_password,
            "saved_credential": saved_credential,
            "source": source,
            "completed": completed,
            "target_account_id": target_account_id,
            "previously_authenticated": self._authenticated,
        }
        self._log_auth_event("authentication_started", account=username, source=source)

        def work() -> dict[str, Any]:
            return self._ldap_authenticate(username, password)

        def finish(auth_result: dict[str, Any]) -> None:
            self._authenticationFinished.emit({
                "generation": generation,
                "auth_result": auth_result,
            })

        if self._authentication_runner is not None:
            def emit_result() -> None:
                finish(work())
            self._authentication_runner(emit_result)
        else:
            self._tasks.submit("ldap-authentication", work, on_success=finish)
        return completed.get("result") or {
            "success": True,
            "code": "authenticating",
            "message": self.tr("Signing in..."),
            "requiresPassword": False,
        }

    @Slot(object)
    def _finish_authentication(self, request: dict[str, Any]) -> None:
        generation = int(request["generation"])
        pending = self._pending_authentications.pop(generation, None)
        if generation != self._authentication_generation or pending is None:
            self._log_auth_event("authentication_stale", source="worker")
            return
        username = str(pending["username"])
        password = str(pending["password"])
        remember_password = bool(pending["remember_password"])
        saved_credential = bool(pending["saved_credential"])
        source = str(pending["source"])
        auth_result = request["auth_result"]
        self._auth_busy = False
        if not auth_result["success"]:
            previously_authenticated = bool(pending["previously_authenticated"])
            preference_changed = False
            if not previously_authenticated:
                self._clear_session()
            self._auth_state = "authenticated" if previously_authenticated else "credential_required"
            failure_code = str(auth_result.get("code", "") or "invalid_credentials")
            target_account_id = str(pending["target_account_id"])
            if saved_credential and failure_code == "invalid_credentials" and target_account_id:
                self._credential_delete(target_account_id)
                self._account_store.set_remember_password(target_account_id, False)
                if target_account_id == self._selected_account_id:
                    self._remember_password = False
                    preference_changed = True
            self.authChanged.emit()
            if preference_changed:
                self.preferencesChanged.emit()
            message = (
                self.tr("Unable to connect to LDAP. Please try again later.")
                if failure_code == "ldap_unavailable"
                else self.tr("Account or password is incorrect.")
            )
            result = {
                "success": False,
                "code": failure_code,
                "message": message,
                "requiresPassword": True,
                "source": source,
            }
            pending["completed"]["result"] = result
            self._log_auth_event(
                "authentication_completed", account=username, code=failure_code,
                source=source, success=False,
            )
            self.authenticationCompleted.emit(dict(result))
            return
        validated_username = str(auth_result.get("username", "") or username).strip()
        display_name = str(auth_result.get("display_name", "") or validated_username).strip()
        account_id = account_id_for_username(validated_username)
        saved = bool(remember_password)
        if saved:
            try:
                self._credential_store.write(account_id, validated_username, password)
            except CredentialError:
                saved = False
        else:
            self._credential_delete(account_id)
        account_id = self._account_store.record_login(validated_username, display_name, saved)
        self._account_store.set_active_account(account_id)
        self._selected_account_id = account_id
        self._remember_password = saved
        self.preferencesChanged.emit()
        avatar_bytes = auth_result.get("avatar_bytes", b"")
        if isinstance(avatar_bytes, bytes) and avatar_bytes:
            self._set_avatar_bytes(validated_username, avatar_bytes)
        self._set_auth_state(
            username=validated_username,
            authenticated=True,
            password=password,
            display_name=display_name,
        )
        message = self.tr("Sign-in successful. Welcome, {username}").format(username=validated_username)
        if remember_password and not saved:
            message = self.tr("Signed in, but the password could not be saved.")
        result = {
            "success": True,
            "code": "signed_in_password_not_saved" if remember_password and not saved else "signed_in",
            "message": message,
            "requiresPassword": False,
            "source": source,
        }
        pending["completed"]["result"] = result
        self._log_auth_event(
            "authentication_completed", account=validated_username,
            code=result["code"], source=source, success=True,
        )
        self.authenticationCompleted.emit(dict(result))

    @Slot(str, str, bool, result="QVariantMap")
    def login(self, username: str, password: str, remember_password: bool = False) -> dict[str, Any]:
        if self._auth_busy:
            return {
                "success": False,
                "code": "busy",
                "message": self.tr("Sign-in is already in progress."),
                "requiresPassword": False,
            }
        clean_username = (username or "").strip()
        clean_password = password or ""
        if not clean_username or not clean_password:
            return {
                "success": False,
                "message": self.tr("Account or password cannot be empty."),
                "code": "account_or_password_empty", "requiresPassword": True,
            }
        if Connection is None:
            return {
                "success": False,
                "message": self.tr("Unable to connect to LDAP. Please try again later."),
                "code": "ldap_unavailable", "requiresPassword": True,
            }

        return self._begin_authentication(
            clean_username,
            clean_password,
            remember_password,
            source="manual",
        )

    @Slot(result="QVariantMap")
    def loginWithSavedCredential(self) -> dict[str, Any]:
        if self._auth_busy:
            return {"success": False, "code": "busy", "message": self.tr("Sign-in is already in progress."), "requiresPassword": False}
        item = self._account_store.get(self._selected_account_id) if self._selected_account_id else None
        if not item or not item.get("remember_password"):
            return {"success": False, "code": "credential_required", "message": self.tr("Please enter the password again."), "requiresPassword": True}
        try:
            username, password = self._credential_store.read(self._selected_account_id)
        except (CredentialNotFoundError, KeyError, CredentialError):
            self._account_store.set_remember_password(self._selected_account_id, False)
            self._remember_password = False
            self.preferencesChanged.emit()
            return {"success": False, "code": "credential_required", "message": self.tr("Please enter the password again."), "requiresPassword": True}
        return self._begin_authentication(username, password, True, saved_credential=True, source="manual")

    @Slot(str, result="QVariantMap")
    def selectAccount(self, account_id: str) -> dict[str, Any]:
        return self._select_account(account_id, source="switch")

    def _select_account(self, account_id: str, *, source: str) -> dict[str, Any]:
        if self._auth_busy:
            self._authentication_generation += 1
            self._auth_busy = False
        item = self._account_store.get(account_id)
        if not item:
            return {"success": False, "code": "account_not_found", "message": self.tr("The selected account no longer exists."), "requiresPassword": True}
        if account_id == self._selected_account_id and self._authenticated:
            return {"success": True, "code": "already_selected", "message": "", "requiresPassword": False}
        switching_authenticated_session = self._authenticated
        if not switching_authenticated_session:
            self._clear_session()
            self._selected_account_id = account_id
            self._username = item["username"]
            self._remember_password = bool(item["remember_password"])
            self._avatar_url = self._avatar_url_for_username(self._username)
            self.preferencesChanged.emit()
        if not item.get("remember_password"):
            if not switching_authenticated_session:
                self._auth_state = "credential_required"
                self.authChanged.emit()
            return {"success": False, "code": "credential_required", "message": "", "requiresPassword": True}
        try:
            credential_username, password = self._credential_store.read(account_id)
        except (CredentialNotFoundError, KeyError, CredentialError):
            self._account_store.set_remember_password(account_id, False)
            if not switching_authenticated_session:
                self._remember_password = False
                self._auth_state = "credential_required"
                self.authChanged.emit()
            return {"success": False, "code": "credential_required", "message": self.tr("Please enter the password again."), "requiresPassword": True}
        return self._begin_authentication(
            credential_username,
            password,
            True,
            saved_credential=True,
            source=source,
        )

    @Slot(bool, result="QVariantMap")
    def setRememberPassword(self, enabled: bool) -> dict[str, Any]:
        self._remember_password = bool(enabled)
        persisted = False
        if self._selected_account_id and not enabled:
            self._credential_delete(self._selected_account_id)
            self._account_store.set_remember_password(self._selected_account_id, False)
            persisted = True
        self.preferencesChanged.emit()
        self._log_auth_event(
            "remember_password_updated", enabled=bool(enabled), persisted=persisted,
            pending=bool(enabled and not persisted),
        )
        return {"success": True, "code": "updated", "message": "", "requiresPassword": not enabled}

    @Slot()
    def restoreStartupSession(self) -> None:
        if self._startup_session_restored:
            self._log_auth_event("startup_restore_skipped", reason="already_started")
            return
        self._startup_session_restored = True
        active_account_id = self._account_store.active_account_id
        selected = self._account_store.get(active_account_id) if active_account_id else None
        if selected is None:
            selected = self._account_store.get(self._account_store.last_account_id)
        if selected is None:
            self._log_auth_event("startup_selection_skipped", reason="no_account")
            return
        self._selected_account_id = selected["account_id"]
        self._username = selected["username"]
        self._remember_password = bool(selected.get("remember_password", False))
        self._resolve_profile()
        self._avatar_url = self._avatar_url_for_username(self._username)
        self.preferencesChanged.emit()
        self._log_auth_event(
            "startup_account_selected", account=selected["username"],
            restore_requested=selected["account_id"] == active_account_id,
        )
        if selected["account_id"] != active_account_id:
            self._auth_state = "credential_required"
            self.authChanged.emit()
            return
        if not self._remember_password:
            self._auth_state = "credential_required"
            self.authChanged.emit()
            self._log_auth_event("startup_credential_unavailable", account=selected["username"])
            return
        try:
            credential_username, password = self._credential_store.read(selected["account_id"])
        except (CredentialNotFoundError, KeyError, CredentialError):
            self._account_store.set_remember_password(selected["account_id"], False)
            self._remember_password = False
            self.preferencesChanged.emit()
            self._auth_state = "credential_required"
            self.authChanged.emit()
            self._log_auth_event("startup_credential_unavailable", account=selected["username"])
            return
        if account_id_for_username(credential_username) != selected["account_id"] or not password:
            self._credential_delete(selected["account_id"])
            self._account_store.set_remember_password(selected["account_id"], False)
            self._remember_password = False
            self.preferencesChanged.emit()
            self._auth_state = "credential_required"
            self.authChanged.emit()
            self._log_auth_event("startup_credential_unavailable", account=selected["username"])
            return
        self._log_auth_event("startup_credential_available", account=selected["username"])
        self._set_auth_state(
            username=credential_username,
            authenticated=True,
            password=password,
            display_name=str(selected.get("display_name", "") or credential_username),
        )
        self._log_auth_event("startup_session_restored", account=credential_username, success=True)

    @Slot()
    def useOtherAccount(self) -> None:
        if self._auth_busy:
            return
        if self._authenticated:
            return
        self._clear_session(keep_selection=False)
        self._authentication_generation += 1
        self._auth_busy = False
        self._remember_password = False
        self._auth_state = "signed_out"
        self.authChanged.emit()

    @Slot()
    def cancelAuthentication(self) -> None:
        if not self._auth_busy and not self._pending_authentications:
            return
        preserve_session = any(
            bool(pending.get("previously_authenticated"))
            for pending in self._pending_authentications.values()
        )
        self._authentication_generation += 1
        self._pending_authentications.clear()
        self._auth_busy = False
        if preserve_session:
            self._auth_state = "authenticated"
        else:
            self._clear_session()
            self._auth_state = "credential_required" if self._selected_account_id else "signed_out"
        self.authChanged.emit()
        self._log_auth_event("authentication_cancelled", result="cancelled")

    @Slot()
    def logout(self) -> None:
        self._authentication_generation += 1
        self._auth_busy = False
        account_id = self._selected_account_id
        item = self._account_store.get(account_id) if account_id else None
        self._account_store.clear_active_account()
        self._clear_session()
        if item:
            self._username = item["username"]
            self._remember_password = bool(item["remember_password"])
            self._avatar_url = self._avatar_url_for_username(self._username)
            if not self._remember_password:
                self._credential_delete(account_id)
            self._auth_state = "credential_required"
        else:
            self._auth_state = "signed_out"
        self.authChanged.emit()
        self.preferencesChanged.emit()
        self._log_auth_event(
            "logout", account=str(item.get("username", "")) if item else "<none>",
            result="signed_out", remember=self._remember_password,
        )

    @Slot(str, result="QVariantMap")
    def removeAccount(self, account_id: str) -> dict[str, Any]:
        if self._auth_busy:
            return {"success": False, "code": "busy", "message": self.tr("Sign-in is already in progress."), "requiresPassword": False}
        if account_id == self._selected_account_id:
            self._clear_session(keep_selection=False)
        cleanup_ok = self._credential_delete(account_id)
        item = self._account_store.get(account_id)
        self._account_store.remove(account_id)
        if cleanup_ok:
            self._account_store.clear_credential_cleanup(account_id)
        else:
            self._account_store.mark_credential_cleanup(account_id)
        if item:
            avatar = self._avatar_path_for_username(item["username"])
            try:
                avatar.unlink(missing_ok=True)
            except OSError:
                pass
        if not self._selected_account_id:
            self._selected_account_id = self._account_store.last_account_id
        selected = self._account_store.get(self._selected_account_id) if self._selected_account_id else None
        if selected:
            self._username = str(selected["username"])
            self._display_name = str(selected.get("display_name", "") or self._username)
            self._remember_password = bool(selected.get("remember_password", False))
            self._resolve_profile()
            self._avatar_url = self._avatar_url_for_username(self._username)
        else:
            self._username = ""
            self._display_name = ""
            self._profile = {}
            self._avatar_url = ""
            self._remember_password = False
        self._auth_state = "credential_required" if selected else "signed_out"
        self.authChanged.emit()
        self.preferencesChanged.emit()
        if not cleanup_ok:
            self._log_auth_event(
                "account_removed", account=str(item.get("username", "")) if item else "<none>",
                code="credential_cleanup_failed", success=False,
            )
            return {
                "success": False,
                "code": "credential_cleanup_failed",
                "message": self.tr("The account was removed, but its saved credential could not be deleted. Please retry."),
                "requiresPassword": True,
            }
        self._log_auth_event(
            "account_removed", account=str(item.get("username", "")) if item else "<none>",
            code="removed", success=True,
        )
        return {"success": True, "code": "removed", "message": "", "requiresPassword": True}

    authenticated = Property(bool, _get_authenticated, notify=authChanged)
    username = Property(str, _get_username, notify=authChanged)
    pageStateAccount = Property(str, _get_page_state_account, notify=authChanged)
    avatarUrl = Property(str, _get_avatar_url, notify=authChanged)
    displayName = Property(str, _get_display_name, notify=authChanged)
    initials = Property(str, _get_initials, notify=authChanged)
    grade = Property(str, _get_grade, notify=authChanged)
    jobTitle = Property(str, _get_job_title, notify=authChanged)
    department = Property(str, _get_department, notify=authChanged)
    team = Property(str, _get_team, notify=authChanged)
    reportsTo = Property(str, _get_reports_to, notify=authChanged)
    productLines = Property("QVariantList", _get_product_lines, notify=authChanged)
    roleText = Property(str, _get_role_text, notify=authChanged)
    accounts = Property("QVariantList", _get_accounts, notify=authChanged)
    selectedAccountId = Property(str, _get_selected_account_id, notify=authChanged)
    authState = Property(str, _get_auth_state_name, notify=authChanged)
    authBusy = Property(bool, _get_auth_busy, notify=authChanged)
    rememberPassword = Property(bool, _get_remember_password, notify=preferencesChanged)
    hasSavedCredential = Property(bool, _get_has_saved_credential, notify=preferencesChanged)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_unprotect(data: bytes) -> bytes:
    if platform != "win32":
        raise RuntimeError("Auth secret store requires Windows DPAPI")

    in_blob = _blob_from_bytes(data)
    entropy_blob = _blob_from_bytes(_AUTH_SECRET_ENTROPY)
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return _bytes_from_blob(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _blob_from_bytes(data: bytes) -> _DataBlob:
    if not data:
        return _DataBlob(0, None)
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)
