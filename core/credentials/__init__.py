"""Shared credential storage owners."""

import sys


class CredentialError(RuntimeError):
    """Platform credential storage operation failed."""


def desktop_credential_store(*, target_prefix: str):
    if sys.platform in ("darwin", "linux"):
        from .keyring_store import KeyringCredentialStore

        return KeyringCredentialStore(target_prefix=target_prefix)
    from .windows import WindowsCredentialStore

    return WindowsCredentialStore(target_prefix=target_prefix)
