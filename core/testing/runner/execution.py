from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.testing.cases.catalog import is_packaged_runtime
from core.testing.runner.config import RUN_CONFIG_ENV, RunConfig, run_config_to_json
from core.logging import smart_log


def _subprocess_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _bundled_python_executable(root_dir: Path) -> Path:
    executable = root_dir / "python" / ("python.exe" if os.name == "nt" else "python")
    if not executable.exists():
        raise FileNotFoundError(
            "Bundled Python runtime was not found for packaged pytest execution.\n"
            f"expected={executable}"
        )
    return executable


@dataclass
class TestRunSession:
    process: Any
    event_file: Path
    tempdir: tempfile.TemporaryDirectory[str]
    adb_serial: str | None = None

    def cleanup(self) -> None:
        self.tempdir.cleanup()

    def cleanup_failed_run(self, reason: str = "pytest run failed") -> None:
        if not self.adb_serial:
            return
        try:
            from core.testing.runner.apk_client import _force_stop_apk, stop_apk_run
            import shutil

            stop_apk_run(adb_serial=self.adb_serial, reason=reason)
            adb_executable = shutil.which("adb")
            if adb_executable:
                _force_stop_apk(adb_executable=adb_executable, adb_serial=self.adb_serial)
        except Exception as exc:  # noqa: BLE001
            smart_log(
                f"failed-run APK cleanup failed: {exc}",
                level="warning",
                domain="runner",
                source="execution",
            )

    def stop(self, reason: str = "UI stop button") -> None:
        if self.process.poll() is not None:
            return
        if self.adb_serial:
            try:
                import time
                from core.testing.runner.apk_client import _force_stop_apk, stop_apk_run
                import shutil

                stop_apk_run(adb_serial=self.adb_serial, reason=reason)
                time.sleep(1.0)
                adb_executable = shutil.which("adb")
                if adb_executable:
                    _force_stop_apk(adb_executable=adb_executable, adb_serial=self.adb_serial)
            except Exception:
                pass
        self.process.terminate()
        try:
            self.process.wait(timeout=5.0)
        except (subprocess.TimeoutExpired, TimeoutError):
            self.process.kill()


def start_pytest_run(
    *,
    root_dir: Path,
    run_config: RunConfig,
) -> TestRunSession:
    nodeids = list(run_config.nodeids)
    adb_serial = run_config.dut_serial
    packaged = is_packaged_runtime()
    root_dir = Path(getattr(sys, "_MEIPASS", root_dir)).resolve() if packaged else root_dir.resolve()
    core_root = root_dir / "core"
    command_nodeids = []
    for nodeid in nodeids:
        case_path, separator, selector = nodeid.partition("::")
        path = Path(case_path)
        if not path.is_absolute():
            path = core_root / path
        command_nodeids.append(str(path) + separator + selector)
    tempdir = tempfile.TemporaryDirectory(prefix="smarttest_pytest_run_")
    event_file = Path(tempdir.name) / "events.jsonl"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    env["SMARTTEST_STEP_EVENTS_OUT"] = str(event_file)
    env[RUN_CONFIG_ENV] = run_config_to_json(run_config)
    python_executable = Path(sys.executable)
    if packaged:
        python_executable = _bundled_python_executable(root_dir)
    cmd = [
        str(python_executable),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-s",
        "-q",
        "--disable-warnings",
        "--rootdir",
        str(core_root),
        *command_nodeids,
    ]
    smart_log(
        "pytest command prepared",
        domain="runner",
        source="execution",
        extra={
            "packaged": packaged,
            "root_dir": str(root_dir),
            "python": str(python_executable),
            "cmd": cmd,
            "event_file": str(event_file),
        },
    )
    smart_log(
        f"popen start packaged={packaged} cwd={root_dir}",
        domain="runner",
        source="execution",
    )
    process = subprocess.Popen(
        cmd,
        cwd=str(root_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=_subprocess_creationflags(),
    )
    smart_log(
        f"popen started pid={getattr(process, 'pid', '<unknown>')}",
        domain="runner",
        source="execution",
    )
    return TestRunSession(
        process=process,
        event_file=event_file,
        tempdir=tempdir,
        adb_serial=adb_serial,
    )

