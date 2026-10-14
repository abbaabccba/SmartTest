"""Desktop credentials stored exclusively in macOS Keychain / Linux Secret Service."""
from __future__ import annotations

import json
import sys

from keyring.errors import KeyringError, PasswordDeleteError

from . import CredentialError
from .windows import CredentialNotFoundError, TARGET_PREFIX, _target


class KeyringCredentialStore:
    def __init__(self, backend=None, *, target_prefix: str = TARGET_PREFIX):
        self._backend = backend
        self._storage_errors = (KeyringError,)
        self._platform = sys.platform
        self._target_prefix = str(target_prefix)

    def _get_backend(self):
        if self._backend is None:
            try:
                if self._platform == "darwin":
                    from keyring.backends.macOS import Keyring
                else:
                    from secretstorage.exceptions import SecretStorageException
                    from keyring.backends.SecretService import Keyring
                    self._storage_errors = (KeyringError, SecretStorageException)
                backend = Keyring()
                backend.priority  # Validate the explicit backend, without selecting a fallback.
            except (ImportError, RuntimeError, KeyringError):
                raise CredentialError("System credential backend is unavailable.") from None
            self._backend = backend
        return self._backend

    def write(self, credential_ref: str, username: str, password: str) -> None:
        target = _target(credential_ref, self._target_prefix)
        value = json.dumps([str(username), str(password)])
        try:
            self._get_backend().set_password(target, credential_ref, value)
        except self._storage_errors:
            raise CredentialError(f"System credential write failed for {target}.") from None

    def read(self, credential_ref: str) -> tuple[str, str]:
        target = _target(credential_ref, self._target_prefix)
        try:
            value = self._get_backend().get_password(target, credential_ref)
        except self._storage_errors:
            raise CredentialError(f"System credential read failed for {target}.") from None
        if value is None:
            raise CredentialNotFoundError(f"Credential not found: {target}")
        try:
            username, password = json.loads(value)
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError
        except (ValueError, TypeError):
            raise CredentialError(f"Invalid system credential: {target}") from None
        return username, password

    def delete(self, credential_ref: str) -> None:
        target = _target(credential_ref, self._target_prefix)
        try:
            self._get_backend().delete_password(target, credential_ref)
        except PasswordDeleteError as exc:
            # Secret Service uses this only for a missing item; macOS also wraps
            # native failures, so preserve those as errors unless it was NotFound.
            if self._platform == "linux":
                raise CredentialNotFoundError(f"Credential not found: {target}") from None
            from keyring.backends.macOS import api
            if isinstance(exc.__cause__, api.NotFound):
                raise CredentialNotFoundError(f"Credential not found: {target}") from None
            raise CredentialError(f"System credential delete failed for {target}.") from None
        except self._storage_errors:
            raise CredentialError(f"System credential delete failed for {target}.") from None
