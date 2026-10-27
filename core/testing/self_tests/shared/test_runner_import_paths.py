import pytest

from core.testing.runner.config import RunConfig
from core.testing.runner.execution import start_pytest_run


@pytest.mark.parametrize("absolute", [False, True])
def test_runner_preserves_nodeids_without_shadowing_standard_email(tmp_path, absolute, monkeypatch):
    monkeypatch.setenv("SMARTTEST_LOG_DIR", str(tmp_path / "logs"))
    core_root = tmp_path / "core"
    email_dir = core_root / "email"
    email_dir.mkdir(parents=True)
    (email_dir / "__init__.py").write_text("", encoding="utf-8")
    case_file = core_root / "testing" / "tests" / "test_import.py"
    case_file.parent.mkdir(parents=True)
    case_file.write_text(
        "def test_email():\n    import email.message\n    assert email.message.Message\n",
        encoding="utf-8",
    )
    relative_nodeid = "testing/tests/test_import.py::test_email"
    nodeid = str(case_file) + "::test_email" if absolute else relative_nodeid
    session = start_pytest_run(root_dir=tmp_path, run_config=RunConfig(nodeids=[nodeid]))
    try:
        output, _ = session.process.communicate(timeout=30)
        assert session.process.returncode == 0, output
        assert "1 passed" in output
    finally:
        if session.process.poll() is None:
            session.process.kill()
            session.process.wait()
        session.cleanup()
