from types import SimpleNamespace
import shlex

import pytest

from core.testing.tool.dut_tool.features import iptv_middle_screen as feature


@pytest.mark.parametrize("failure", [None, "launch", "wrong_activity", "crashed"])
@pytest.mark.parametrize("selected", [["jarvis"], ["droidlogic"], ["jarvis", "droidlogic"]])
def test_image_viewers_require_successful_launch_and_exact_resumed_activity(monkeypatch, failure, selected):
    commands = []
    evidence = []
    exit_evidence = []
    current = ""
    sleeps = []

    def shell(command):
        nonlocal current
        commands.append(command)
        if command.startswith("if test -f"):
            assert shlex.split(command)[3].rstrip(";") == "/sdcard/a b.jpg"
            return "exists"
        if command.startswith("am start"):
            tokens = shlex.split(command)
            current = tokens[tokens.index("-n") + 1]
            if current.startswith("com.cmcc"):
                assert tokens[tokens.index("--es") + 1:] == ["img_path", "/sdcard/a b.jpg"]
            else:
                assert tokens[tokens.index("-d") + 1] == "file:///sdcard/a b.jpg"
                assert tokens[tokens.index("-t") + 1] == "image/*"
            return "Status: error" if failure == "launch" else "Status: ok"
        if failure == "crashed":
            return "mLastPausedActivity: ActivityRecord{abc u0 " + current + " t1}"
        component = current + "Other" if failure == "wrong_activity" else current
        return "topResumedActivity=ActivityRecord{abc u0 " + component + " t1}"

    monkeypatch.setattr(feature, "step_log", lambda *a, **kw: None)
    monkeypatch.setattr(feature, "step_evidence", lambda *a, **kw: (evidence if a[0] == "图片播放器启动检查" else exit_evidence).append(a[1]))
    def back():
        nonlocal current
        commands.append("BACK")
        current = "com.launcher/.Home"

    monkeypatch.setattr(feature.time, "sleep", sleeps.append)
    dut = SimpleNamespace(run_device_shell=shell, back=back)
    params = {"iptv_middle_screen_067:media_files": "/sdcard/a b.jpg",
              "iptv_middle_screen_067:image_viewers": selected}
    if failure:
        with pytest.raises(AssertionError):
            feature.check_image(SimpleNamespace(source_id=67), dut, params, "dut")
        assert not evidence
        assert commands.count("BACK") == 0
        assert 5 not in sleeps
    else:
        feature.check_image(SimpleNamespace(source_id=67), dut, params, "dut")
        assert commands.count("BACK") == len(selected)
        assert sleeps.count(5) == len(selected)
        assert len(evidence) == len(exit_evidence) == len(selected)
        assert all(item["exited"] for item in exit_evidence)
        assert [item["viewer"] for item in evidence] == selected
        launches = [command for command in commands if command.startswith("am start")]
        assert len(launches) == len(selected)
        assert all(item["image_rendering_verified"] is False for item in evidence)


def test_empty_image_viewer_selection_is_blocked_before_launch():
    from core.testing.params.registry import default_registry
    from pathlib import Path
    from core.testing.params.validation import validate_run_request
    from core.testing.state.models import SelectedCase, TestPageState
    from core.testing.tests.android.common.iptv.test_middle_screen_067_images import CASE

    field = default_registry().get_param("iptv_middle_screen_067:image_viewers")
    assert field.default == ["jarvis", "droidlogic"]
    key = "iptv_middle_screen_067:image_viewers"
    assert key in CASE.parameters
    nodeid = "test_middle_screen_067_images"
    catalog = [{"nodeid": nodeid, "required_params": list(CASE.parameters)}]
    state = TestPageState(selected=[SelectedCase(nodeid)], case_parameters={nodeid: {
        "iptv_middle_screen_067:media_files": "/sdcard/cn.jpg", key: [],
    }})
    assert [issue.param_key for issue in validate_run_request(
        root_dir=Path.cwd(), state=state, catalog=catalog,
    )] == [key]
    state.case_parameters[nodeid][key] = ["jarvis"]
    assert not validate_run_request(root_dir=Path.cwd(), state=state, catalog=catalog)
    with pytest.raises(AssertionError, match="Select at least one"):
        feature.check_image(SimpleNamespace(source_id=67), SimpleNamespace(), {
            "iptv_middle_screen_067:media_files": "/sdcard/cn.jpg",
            "iptv_middle_screen_067:image_viewers": [],
        }, "dut")


@pytest.mark.parametrize("backs_required", [2, 3])
def test_image_exit_is_bounded_and_reports_failure(monkeypatch, backs_required):
    backs = []
    expected = "com.droidlogic.imageplayer/com.droidlogic.imageplayer.FullImageActivity"
    def shell(command):
        component = "com.droidlogic.imageplayer/.FullImageActivity" if len(backs) < backs_required else "com.launcher/.Home"
        return "topResumedActivity=ActivityRecord{abc u0 " + component + " t1}"
    monkeypatch.setattr(feature, "step_log", lambda *a, **kw: None)
    monkeypatch.setattr(feature.time, "sleep", lambda seconds: None)
    dut = SimpleNamespace(run_device_shell=shell, back=lambda: backs.append(True))
    if backs_required == 3:
        with pytest.raises(AssertionError, match="did not exit"):
            feature._exit_image_viewer(dut, expected, 67)
    else:
        feature._exit_image_viewer(dut, expected, 67)
    assert len(backs) == 2


def test_missing_image_is_rejected_before_any_launch(monkeypatch):
    commands = []
    def shell(command):
        commands.append(command)
        return "missing"
    monkeypatch.setattr(feature, "step_log", lambda *a, **kw: None)
    with pytest.raises(AssertionError, match="Image file does not exist"):
        feature.check_image(SimpleNamespace(source_id=67), SimpleNamespace(run_device_shell=shell), {
            "iptv_middle_screen_067:media_files": "/sdcard/missing.jpg",
            "iptv_middle_screen_067:image_viewers": ["jarvis", "droidlogic"],
        }, "dut")
    assert len(commands) == 1 and commands[0].startswith("if test -f")
