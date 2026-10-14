from __future__ import annotations

import re

from . import CredentialError


TARGET_PREFIX = "SmartTest/ProjectWeeklyAudit/"


class WindowsCredentialError(CredentialError):
    pass


class CredentialNotFoundError(WindowsCredentialError):
    pass


class WindowsCredentialStore:
    def __init__(self, native=None, *, target_prefix: str = TARGET_PREFIX):
        self._native = native or _PyWin32CredentialAdapter()
        self._target_prefix = str(target_prefix)

    def write(self, credential_ref: str, username: str, password: str) -> None:
        target = _target(credential_ref, self._target_prefix)
        blob = bytearray(str(password).encode("utf-16-le"))
        try:
            self._native.write_generic(target, str(username), blob)
        finally:
            self._native.clear(blob)

    def read(self, credential_ref: str) -> tuple[str, str]:
        target = _target(credential_ref, self._target_prefix)
        try:
            username, blob = self._native.read_generic(target)
        except CredentialNotFoundError:
            raise
        try:
            return str(username), bytes(blob).decode("utf-16-le")
        finally:
            self._native.clear(blob)

    def delete(self, credential_ref: str) -> None:
        self._native.delete_generic(_target(credential_ref, self._target_prefix))


class _PyWin32CredentialAdapter:
    def __init__(self):
        try:
            import win32cred
            import pywintypes
        except ImportError as exc:
            raise WindowsCredentialError(
                "Windows Credential Manager requires the declared pywin32 dependency.",
            ) from exc
        self._api = win32cred
        self._error = pywintypes.error

    def write_generic(self, target: str, username: str, password_blob: bytearray):
        try:
            self._api.CredWrite({
                "Type": self._api.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": username,
                "CredentialBlob": bytes(password_blob).decode("utf-16-le"),
                "Persist": self._api.CRED_PERSIST_LOCAL_MACHINE,
            }, 0)
        except self._error as exc:
            raise WindowsCredentialError(
                f"Credential Manager write failed for {target} (code {exc.winerror}).",
            ) from None

    def read_generic(self, target: str):
        try:
            value = self._api.CredRead(target, self._api.CRED_TYPE_GENERIC, 0)
        except self._error as exc:
            if exc.winerror == 1168:
                raise CredentialNotFoundError(
                    f"Credential not found: {target}",
                ) from None
            raise WindowsCredentialError(
                f"Credential Manager read failed for {target} (code {exc.winerror}).",
            ) from None
        blob = value.get("CredentialBlob") or b""
        if isinstance(blob, str):
            blob = blob.encode("utf-16-le")
        return value.get("UserName", ""), bytearray(blob)

    def delete_generic(self, target: str):
        try:
            self._api.CredDelete(target, self._api.CRED_TYPE_GENERIC, 0)
        except self._error as exc:
            if exc.winerror == 1168:
                raise CredentialNotFoundError(
                    f"Credential not found: {target}",
                ) from None
            raise WindowsCredentialError(
                f"Credential Manager delete failed for {target} (code {exc.winerror}).",
            ) from None

    @staticmethod
    def clear(blob):
        for index in range(len(blob)):
            blob[index] = 0


def _target(credential_ref, prefix: str = TARGET_PREFIX) -> str:
    value = str(credential_ref)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid credential reference")
    return prefix + value
