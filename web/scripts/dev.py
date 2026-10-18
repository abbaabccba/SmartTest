"""Start the Web API and frontend development servers together."""

import os
from pathlib import Path
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logging import smart_log


HOST = "127.0.0.1"
PORT = 8000
HEALTH_URL = f"http://{HOST}:{PORT}/health"
MANAGED_PYTHON = ROOT / ".venv" / (
    "Scripts/python.exe" if sys.platform.startswith("win") else "bin/python"
)
MACOS_DB_PASSWORD_SERVICE = "SmartTest/WebStorage/mysql-local-app"
MACOS_WEB_CREDENTIAL_KEY_SERVICE = "SmartTest/WebStorage/web-credential-key-local"
MACOS_KEYCHAIN_SECRETS = (
    ("SMARTTEST_DB_PASSWORD", "smarttest_web", MACOS_DB_PASSWORD_SERVICE),
    ("SMARTTEST_WEB_CREDENTIAL_KEY", "smarttest_web", MACOS_WEB_CREDENTIAL_KEY_SERVICE),
)


def web_backend_dependencies_available():
    return all(importlib.util.find_spec(name) is not None for name in ("fastapi", "uvicorn"))


def resolve_runtime_tools(
    *, python_executable=None, managed_python=MANAGED_PYTHON,
    backend_dependencies_available=web_backend_dependencies_available,
    which=shutil.which,
):
    python = Path(python_executable or sys.executable).resolve()
    if not backend_dependencies_available() and Path(managed_python).is_file():
        python = Path(managed_python).resolve()
    npm_name = "npm.cmd" if sys.platform.startswith("win") else "npm"
    adjacent_npm = python.parent / npm_name
    if adjacent_npm.is_file():
        return python, adjacent_npm
    system_npm = which(npm_name)
    if system_npm:
        return python, Path(system_npm).resolve()
    raise FileNotFoundError(f"{npm_name} was not found beside Python or on PATH")


def resolve_backend_environment(
    *, environment=None, platform: str = sys.platform, run=subprocess.run,
) -> dict[str, str]:
    resolved = dict(os.environ if environment is None else environment)
    if not platform.startswith("darwin"):
        return resolved
    for variable, account, service in MACOS_KEYCHAIN_SECRETS:
        if resolved.get(variable):
            continue
        try:
            result = run(
                [
                    "/usr/bin/security", "find-generic-password",
                    "-a", account, "-s", service, "-w",
                ],
                check=True, capture_output=True, text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise OSError(
                f"{variable} is missing and its macOS Keychain item is unavailable"
            ) from error
        value = result.stdout.strip()
        if not value:
            raise OSError(
                f"{variable} is missing and its macOS Keychain item is unavailable"
            )
        resolved[variable] = value
    return resolved


def _log(message: str, *, level: str = "info", **extra) -> None:
    smart_log(
        message, platform="web", domain="dev", source="web_dev", level=level,
        extra=extra, emit_runtime_event=False,
    )


def backend_process_kwargs() -> dict[str, int]:
    """Keep Uvicorn reload control events inside its own Windows process group."""
    if not sys.platform.startswith("win"):
        return {}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def is_backend_port_in_use(*, timeout_seconds: float = 0.2) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout_seconds)
        return probe.connect_ex((HOST, PORT)) == 0


def find_backend_port_owners(*, run=subprocess.run):
    if not sys.platform.startswith("win"):
        return []
    command = (
        f"$owners = @(Get-NetTCPConnection -LocalAddress '{HOST}' -LocalPort {PORT} "
        "-State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty "
        "OwningProcess -Unique); $all = @(Get-CimInstance Win32_Process); "
        "$rows = foreach ($ownerPid in $owners) { "
        "$root = @($all | Where-Object ProcessId -EQ $ownerPid); "
        "$targets = if ($root) { @($ownerPid) } else { "
        "@($all | Where-Object ParentProcessId -EQ $ownerPid | "
        "Select-Object -ExpandProperty ProcessId) }; "
        "[pscustomobject]@{owner_pid=[int]$ownerPid; target_pids=@($targets)} }; "
        "@($rows) | ConvertTo-Json -Compress"
    )
    result = run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        values = json.loads(result.stdout)
        if isinstance(values, dict):
            values = [values]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    owners = []
    for value in values:
        try:
            owner_pid = int(value.get("owner_pid", 0))
            target_pids = sorted({int(pid) for pid in value.get("target_pids", []) if int(pid) > 0})
        except (AttributeError, TypeError, ValueError):
            continue
        if owner_pid > 0 and target_pids:
            owners.append({"owner_pid": owner_pid, "target_pids": target_pids})
    return owners


def terminate_process_tree_by_pid(pid: int, *, run=subprocess.run) -> None:
    run(
        ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
        check=False, capture_output=True, text=True,
    )


def clear_existing_backend_processes(
    *, port_in_use=is_backend_port_in_use, port_owners=find_backend_port_owners,
    terminate_pid=terminate_process_tree_by_pid, sleep=time.sleep, max_rounds: int = 16,
) -> bool:
    cleaned = set()
    for _ in range(max_rounds):
        if not port_in_use():
            return True
        owners = port_owners()
        targets = sorted({
            pid for owner in owners for pid in owner["target_pids"] if pid not in cleaned
        })
        if not targets:
            return False
        owner_pids = sorted(owner["owner_pid"] for owner in owners)
        _log(
            f"Web development port occupied: owner_pids={owner_pids} "
            f"cleanup_pids={targets}",
            level="warning", host=HOST, port=PORT,
            owner_pids=owner_pids, cleanup_pids=targets,
        )
        for pid in targets:
            terminate_pid(pid)
            cleaned.add(pid)
        sleep(0.25)
    return not port_in_use()


def wait_for_backend_health(
    processes, *, timeout_seconds: float = 30.0, urlopen=None,
    monotonic=time.monotonic, sleep=time.sleep,
) -> bool:
    deadline = monotonic() + timeout_seconds
    open_health = urlopen if urlopen is not None else build_opener(ProxyHandler({})).open
    last_error_type, last_http_status = '', None
    while True:
        if any(process.poll() is not None for process in processes):
            return False
        try:
            with open_health(HEALTH_URL, timeout=0.5) as response:
                if response.status == 200:
                    return True
                last_error_type, last_http_status = '', response.status
        except (OSError, URLError, TimeoutError) as error:
            last_error_type, last_http_status = type(error).__name__, getattr(error, 'code', None)
        if monotonic() >= deadline:
            _log(f"Web backend health deadline expired: url={HEALTH_URL} "
                 f"last_error_type={last_error_type} last_http_status={last_http_status}",
                 level="error", health_url=HEALTH_URL, timeout_seconds=timeout_seconds,
                 last_error_type=last_error_type, last_http_status=last_http_status)
            return False
        sleep(0.1)


def wait_for_backend_port_release(
    *, timeout_seconds: float = 15.0, port_in_use=is_backend_port_in_use,
    monotonic=time.monotonic, sleep=time.sleep,
) -> bool:
    deadline = monotonic() + timeout_seconds
    while port_in_use():
        if monotonic() >= deadline:
            return False
        sleep(0.1)
    return True


def terminate_owned_process(
    process, *, platform: str = sys.platform, run=subprocess.run,
) -> None:
    pid = getattr(process, "pid", None)
    if pid is None:
        return
    if platform.startswith("win"):
        run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False, capture_output=True, text=True,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(
    *, popen=subprocess.Popen, port_in_use=is_backend_port_in_use,
    port_owner=find_backend_port_owners, terminate_port_owner=terminate_process_tree_by_pid,
    wait_for_health=wait_for_backend_health,
    terminate_tree=terminate_owned_process,
    wait_for_port_release=wait_for_backend_port_release, sleep=time.sleep,
    backend_environment=None,
) -> int:
    if port_in_use():
        cleared = clear_existing_backend_processes(
            port_in_use=port_in_use,
            port_owners=port_owner,
            terminate_pid=terminate_port_owner,
            sleep=sleep,
        )
        if not cleared:
            _log(
                f"Web development start rejected: {HOST}:{PORT} is already in use "
                "and its remaining owner could not be cleaned",
                level="error", host=HOST, port=PORT,
            )
            return 2
        _log(
            f"Previous Web process trees stopped: port={PORT}",
            port=PORT,
        )

    processes = []
    try:
        python, npm = resolve_runtime_tools()
        backend_env = dict(
            os.environ if backend_environment is None else backend_environment
        )
        npm_env = os.environ.copy()
        npm_env["PATH"] = os.pathsep.join(
            part for part in (str(npm.parent), npm_env.get("PATH", "")) if part
        )
        backend = popen(
            [
                str(python), "-m", "uvicorn", "smarttest_web.app:app",
                "--app-dir", "web/backend", "--reload", "--reload-dir",
                "web/backend", "--port", str(PORT), "--no-access-log",
            ],
            cwd=ROOT,
            env=backend_env,
            **backend_process_kwargs(),
        )
        processes.append(backend)
        frontend = popen(
            [str(npm), "run", "dev", "--", "--host", "0.0.0.0"],
            cwd=ROOT / "web/frontend",
            env=npm_env,
        )
        processes.append(frontend)
        backend_pid = getattr(backend, "pid", 0)
        frontend_pid = getattr(frontend, "pid", 0)
        _log(
            f"Web development children started: backend_pid={backend_pid} "
            f"frontend_pid={frontend_pid}",
            backend_pid=backend_pid, frontend_pid=frontend_pid,
        )
        if not wait_for_health(processes):
            failed = next(
                (process.returncode for process in processes if process.returncode is not None),
                None,
            )
            result = failed if failed not in (None, 0) else 3
            _log(
                f"Web backend health check failed: url={HEALTH_URL} result={result}",
                level="error", health_url=HEALTH_URL, result=result,
            )
            return result
        _log(
            f"Web backend ready: url={HEALTH_URL} backend_pid={backend_pid}",
            health_url=HEALTH_URL, backend_pid=backend_pid,
        )
        while all(process.poll() is None for process in processes):
            sleep(0.25)
        result = next(
            (process.returncode for process in processes if process.returncode is not None),
            1,
        )
        return int(result or 0)
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        _log(
            f"Web development child start failed: error_type={type(error).__name__}",
            level="error", error_type=type(error).__name__,
        )
        return 1
    finally:
        for process in processes:
            terminate_tree(process)
        if processes:
            released = wait_for_port_release()
            _log(
                f"Web development children stopped: backend_port_released="
                f"{str(released).lower()}",
                level="info" if released else "error",
                backend_port_released=released,
                backend_pid=getattr(processes[0], "pid", 0),
            )


def main() -> int:
    try:
        backend_environment = resolve_backend_environment()
    except OSError as error:
        _log(
            f"Web development environment unavailable: error_type={type(error).__name__}",
            level="error", error_type=type(error).__name__,
        )
        return 1
    return run(popen=subprocess.Popen, backend_environment=backend_environment)


if __name__ == "__main__":
    raise SystemExit(main())
