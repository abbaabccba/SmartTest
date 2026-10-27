from __future__ import annotations


PYWIN32_RUNTIME_MODULES = (
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "win32cred",
    "win32timezone",
)

WINDOWS_PAC_RUNTIME_MODULES = ("pypac", "dukpy", "dukpy._dukpy", "python_socks.sync")

TOOL_HIDDEN_IMPORTS = PYWIN32_RUNTIME_MODULES + WINDOWS_PAC_RUNTIME_MODULES + (
    "ldap3",
    "tenacity",
    "markdown",
    "jira2markdown",
    "FluentUI.FluentUI",
    "Crypto.Hash.MD4",
)

TOOL_SMOKE_MODULES = WINDOWS_PAC_RUNTIME_MODULES + (
    "win32com.client",
    "win32cred",
    "win32timezone",
    "ldap3",
    "tenacity",
    "qrcode",
    "core.markup",
    "jira2markdown",
)
