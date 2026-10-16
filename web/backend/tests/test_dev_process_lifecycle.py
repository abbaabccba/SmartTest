import os
from pathlib import Path
import subprocess
import sys

import web.scripts.dev as dev


class FakeProcess:
    def __init__(self, pid, polls=None, returncode=None):
        self.pid = pid
        self._polls = list(polls or [])
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        if self._polls:
            value = self._polls.pop(0)
            if value is not None:
                self.returncode = value
            return value
        return self.returncode

    def terminate(self): self.terminated = True
    def kill(self): self.killed = True
    def wait(self, timeout=None): return self.returncode


def test_windows_backend_keeps_process_group_and_inherits_development_console():
    kwargs = dev.backend_process_kwargs()

    if sys.platform.startswith("win"):
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert not kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert kwargs == {}


def test_runtime_tools_use_adjacent_npm_for_managed_environment(tmp_path):
    python = tmp_path / "Scripts" / "python.exe"
    npm = python.parent / "npm.cmd"
    python.parent.mkdir()
    python.touch()
    npm.touch()

    assert dev.resolve_runtime_tools(
        python_executable=python,
        backend_dependencies_available=lambda: True,
        which=lambda _name: "system/npm.cmd",
    ) == (python.resolve(), npm.resolve())


def test_runtime_tools_use_path_npm_for_system_environment(tmp_path):
    python = tmp_path / "python.exe"
    npm = tmp_path / "node" / "npm.cmd"
    python.touch()
    npm.parent.mkdir()
    npm.touch()

    assert dev.resolve_runtime_tools(
        python_executable=python,
        backend_dependencies_available=lambda: True,
        which=lambda _name: str(npm),
    ) == (python.resolve(), npm.resolve())


def test_runtime_tools_use_managed_python_when_system_environment_lacks_backend(tmp_path):
    system_python = tmp_path / "system" / "python.exe"
    managed_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    npm = managed_python.parent / "npm.cmd"
    system_python.parent.mkdir()
    managed_python.parent.mkdir(parents=True)
    system_python.touch()
    managed_python.touch()
    npm.touch()

    assert dev.resolve_runtime_tools(
        python_executable=system_python,
        managed_python=managed_python,
        backend_dependencies_available=lambda: False,
        which=lambda _name: None,
    ) == (managed_python.resolve(), npm.resolve())


def test_direct_script_loads_core_logging_outside_repository_cwd(tmp_path):
    script = Path(dev.__file__).resolve()
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", f"import runpy; runpy.run_path({str(script)!r})"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_external_port_owner_is_terminated_automatically_before_start():
    backend = FakeProcess(10, polls=[None, 1], returncode=1)
    frontend = FakeProcess(20, polls=[None, None])
    processes = iter([backend, frontend])
    starts = []
    kills = []
    occupied = iter([True, True, False])
    result = dev.run(
        popen=lambda *args, **kwargs: starts.append((args, kwargs)) or next(processes),
        port_in_use=lambda: next(occupied),
        port_owner=lambda: [{"owner_pid": 4321, "target_pids": [4321, 4322]}],
        terminate_port_owner=lambda pid: kills.append(pid),
        wait_for_health=lambda *_args, **_kwargs: True,
        terminate_tree=lambda process: kills.append(process.pid),
        wait_for_port_release=lambda: True,
        sleep=lambda _seconds: None,
    )
    assert result == 1
    assert kills[:2] == [4321, 4322]
    assert len(starts) == 2


def test_occupied_port_without_resolvable_owner_is_not_killed():
    kills = []
    result = dev.run(
        popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not start")),
        port_in_use=lambda: True,
        port_owner=lambda: [],
        terminate_port_owner=lambda pid: kills.append(pid),
    )
    assert result == 2
    assert kills == []


def test_port_owner_resolver_returns_live_workers_for_stale_listener_parent():
    class Result:
        returncode = 0
        stdout = '[{"owner_pid":2184,"target_pids":[19992]}]'

    owners = dev.find_backend_port_owners(run=lambda *_args, **_kwargs: Result())

    assert owners == [{"owner_pid": 2184, "target_pids": [19992]}]


def test_port_cleanup_repeats_for_each_orphan_listener_until_port_is_free():
    occupied = iter([True, True, True, False])
    owners = iter([
        [{"owner_pid": 2184, "target_pids": [19992]}],
        [{"owner_pid": 35920, "target_pids": [39188]}],
        [{"owner_pid": 43808, "target_pids": [41920]}],
    ])
    killed = []

    assert dev.clear_existing_backend_processes(
        port_in_use=lambda: next(occupied),
        port_owners=lambda: next(owners),
        terminate_pid=lambda pid: killed.append(pid),
        sleep=lambda _seconds: None,
    ) is True
    assert killed == [19992, 39188, 41920]


def test_windows_tree_cleanup_targets_only_the_recorded_parent_pid():
    commands = []
    process = FakeProcess(4321, returncode=1)

    dev.terminate_owned_process(
        process,
        platform="win32",
        run=lambda command, **kwargs: commands.append((command, kwargs)),
    )

    assert commands == [
        (["taskkill", "/PID", "4321", "/T", "/F"],
         {"check": False, "capture_output": True, "text": True}),
    ]


def test_backend_failure_cleans_the_other_child_and_checks_port_release():
    backend = FakeProcess(10, polls=[None, 1], returncode=1)
    frontend = FakeProcess(20, polls=[None, None])
    processes = iter([backend, frontend])
    cleaned = []
    release_checks = []

    result = dev.run(
        popen=lambda *_args, **_kwargs: next(processes),
        port_in_use=lambda: False,
        wait_for_health=lambda *_args, **_kwargs: True,
        terminate_tree=lambda process: cleaned.append(process.pid),
        wait_for_port_release=lambda: release_checks.append(True) or True,
        sleep=lambda _seconds: None,
    )

    assert result == 1
    assert cleaned == [10, 20]
    assert release_checks == [True]


def test_keyboard_interrupt_returns_zero_and_cleans_both_children():
    processes = iter([FakeProcess(10), FakeProcess(20)])
    cleaned = []

    result = dev.run(
        popen=lambda *_args, **_kwargs: next(processes),
        port_in_use=lambda: False,
        wait_for_health=lambda *_args, **_kwargs: True,
        terminate_tree=lambda process: cleaned.append(process.pid),
        wait_for_port_release=lambda: True,
        sleep=lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert result == 0
    assert cleaned == [10, 20]


def test_health_readiness_reports_success_timeout_and_child_failure(monkeypatch):
    responses = iter([OSError("starting"), object()])

    class ReadyResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    def open_ready(*_args, **_kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return ReadyResponse()

    clock = iter([0.0, 0.1, 0.2, 0.3])
    assert dev.wait_for_backend_health(
        [FakeProcess(1), FakeProcess(2)], timeout_seconds=1,
        urlopen=open_ready, monotonic=lambda: next(clock), sleep=lambda _seconds: None,
    ) is True
    assert dev.wait_for_backend_health(
        [FakeProcess(1), FakeProcess(2)], timeout_seconds=0,
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
        monotonic=lambda: 1.0, sleep=lambda _seconds: None,
    ) is False
    assert dev.wait_for_backend_health(
        [FakeProcess(1, returncode=3), FakeProcess(2)], timeout_seconds=1,
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
        monotonic=lambda: 0.0, sleep=lambda _seconds: None,
    ) is False


def test_real_loopback_health_ignores_proxy_that_returns_503(monkeypatch):
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from urllib import request

    @contextmanager
    def server(status, calls):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                self.send_response(status)
                self.end_headers()
            def log_message(self, *_args): pass
        http = HTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{http.server_port}'
        finally:
            http.shutdown(); http.server_close(); thread.join()

    proxy_calls, health_calls = [], []
    with server(503, proxy_calls) as proxy, server(200, health_calls) as health:
        for key in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            monkeypatch.setenv(key, proxy)
        for key in ('NO_PROXY', 'no_proxy'): monkeypatch.setenv(key, '')
        monkeypatch.setattr(request, '_opener', None)
        monkeypatch.setattr(dev, 'HEALTH_URL', health + '/health')
        from pytest import raises
        from urllib.error import HTTPError
        with raises(HTTPError) as failure:
            request.urlopen(dev.HEALTH_URL, timeout=.5)
        assert failure.value.code == 503
        assert len(proxy_calls) == 1 and health_calls == []
        proxy_calls.clear()
        assert dev.wait_for_backend_health([FakeProcess(1)], timeout_seconds=.5)
    assert health_calls == ['/health']
    assert proxy_calls == []


def test_health_deadline_records_safe_error_status_without_changing_timeout(tmp_path, monkeypatch):
    import json
    from urllib.error import HTTPError
    monkeypatch.setenv('SMARTTEST_LOG_DIR', str(tmp_path))
    def unavailable(*_args, **_kwargs):
        raise HTTPError(dev.HEALTH_URL, 503, 'private proxy detail', None, None)
    assert not dev.wait_for_backend_health([FakeProcess(1)], timeout_seconds=0,
        urlopen=unavailable, monotonic=lambda: 1, sleep=lambda _seconds: None)
    records = [json.loads(line) for line in (tmp_path / 'smarttest.log').read_text(encoding='utf-8').splitlines()]
    assert len(records) == 1
    assert records[0]['source'] == 'web_dev'
    assert records[0]['extra']['last_error_type'] == 'HTTPError'
    assert records[0]['extra']['last_http_status'] == 503
    assert 'private proxy detail' not in json.dumps(records)
