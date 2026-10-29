from __future__ import annotations

import ipaddress
import re
import shlex
import time
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from core.testing.runtime.steps import step_evidence, step_log
from core.config.value_conversion import to_float, to_int, to_string_list
from core.testing.tool.dut_tool.features.system import package_exists


TARGET_SOURCE_IDS = frozenset((4, 10, 18, 20, 21, 29, 31, 32, 33, 49, 52, 53, 54, 55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 95, 96, 97, 98, 114))


def parse_link_speed_mbps(output: str) -> int:
    value = str(output or "").strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"Invalid Ethernet speed output: {value!r}")
    return int(value)


def parse_wm_size(output: str) -> tuple[int, int]:
    matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", str(output or ""), re.I)
    if not matches:
        raise ValueError("wm size output contains no dimensions")
    return tuple(map(int, matches[-1]))


def supports_resolution_at_least(output: str, minimum: tuple[int, int] = (1920, 1080)) -> bool:
    width, height = parse_wm_size(output)
    return width >= minimum[0] and height >= minimum[1]


def parse_thermal_millicelsius(output: str) -> int:
    value = str(output or "").strip()
    if not re.fullmatch(r"-?\d+", value):
        raise ValueError(f"Invalid thermal value: {value!r}")
    return int(value)


def parse_interface_addresses(output: str) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {"ipv4": [], "ipv6": []}
    for family, raw in re.findall(r"\binet(6?)\s+(?:addr:)?([^\s/]+)", str(output or "")):
        address = ipaddress.ip_address(raw)
        result["ipv6" if family else "ipv4"].append(str(address))
    return {key: tuple(value) for key, value in result.items()}


def parse_ping_success(output: str) -> bool:
    match = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)%\s*packet loss", str(output or ""), re.I)
    return bool(match and float(match.group(1)) == 0.0)


def parse_hdmi_node_evidence(output: str) -> tuple[bool, bool]:
    value = str(output or "").lower()
    connected = bool(re.search(r"(^|\s)(connected|1)(\s|$)", value))
    active_mode = bool(re.search(r"\b(?:\d{3,4}[pi]\d{2,3}(?:hz)?|enabled)\b", value))
    return connected, active_mode


def _case_param(case: Any, params: Mapping[str, Any], key: str, default: Any = "") -> Any:
    return params.get(f"iptv_middle_screen_{case.source_id:03d}:{key}", default)


def _shell(dut: Any, command: str, source_id: int) -> str:
    output = str(dut.run_device_shell(command) or "").strip()
    step_log("iptv.middle_screen.probe", extra={"source_id": source_id, "command": command, "output": output})
    return output


def check_hdmi_objective(case: Any, dut: Any, params: Mapping[str, Any], serial: str) -> None:
    command = str(_case_param(case, params, "hdmi_state_command", "") or "").strip() or (
        "cat /sys/class/drm/card0-HDMI-A-1/status 2>/dev/null; "
        "cat /sys/class/amhdmitx/amhdmitx0/hpd_state 2>/dev/null; "
        "cat /sys/class/amhdmitx/amhdmitx0/disp_mode 2>/dev/null"
    )
    connected, active_mode = parse_hdmi_node_evidence(_shell(dut, command, case.source_id))
    assert connected, "HDMI connector is not objectively connected"
    assert active_mode, "No active HDMI output-node mode evidence"
    step_log("HDMI TV visual result is a manual boundary", extra={"source_id": case.source_id, "manual": True})


Handler = Callable[[Any, Any, Mapping[str, Any], str], None]

def observe_media_playback(dut, source, *, duration_s, poll_interval_s, assert_state, monotonic=time.monotonic, sleep=time.sleep):
    """Objectively sample PLAYING for the configured observation window."""
    started_at = monotonic()
    assert_state(dut, file_path=source, expected_state="PLAYING")
    samples = [{"elapsed_s": 0, "state": "PLAYING"}]
    step_evidence("播放状态采样", {"source": source, **samples[-1]}, evidence_type="checkpoint")
    deadline = started_at + max(duration_s, 0)
    while monotonic() < deadline:
        sleep(min(max(poll_interval_s, 0.001), max(deadline - monotonic(), 0)))
        assert_state(dut, file_path=source, expected_state="PLAYING")
        samples.append({"elapsed_s": monotonic() - started_at, "state": "PLAYING"})
        step_evidence("播放状态采样", {"source": source, **samples[-1]}, evidence_type="checkpoint")
    return samples

def _resumed_activity_components(output: str) -> list[str]:
    resumed = re.findall(
        r"\b(?:topResumedActivity|mResumedActivity|ResumedActivity)\s*[:=]\s*"
        r"ActivityRecord\{\S+\s+u\d+\s+([^\s}]+)", output,
    )
    result = []
    for item in resumed:
        package, activity = item.split("/", 1)
        result.append(package + "/" + (package + activity if activity.startswith(".") else activity))
    return result


def _exit_image_viewer(dut, expected: str, source_id: int) -> int:
    for back_count in range(2):
        actual = _resumed_activity_components(_shell(dut, "dumpsys activity activities", source_id))
        if expected not in actual:
            return back_count
        dut.back()
        time.sleep(0.5)
    actual = _resumed_activity_components(_shell(dut, "dumpsys activity activities", source_id))
    assert expected not in actual, f"Image viewer did not exit after two BACK actions: {expected}"
    return 2


def check_image(case, dut, params, serial):
    sources = [
        line.strip()
        for line in str(_case_param(case, params, "media_files", "") or "").splitlines()
        if line.strip()
    ]
    if not sources:
        pytest.skip("configure workbook image paths")
    selected_viewers = to_string_list(_case_param(case, params, "image_viewers", []))
    assert selected_viewers, "Select at least one image viewer"
    executed = False
    for source in sources:
        exists = _shell(dut, f"if test -f {shlex.quote(source)}; then echo exists; else echo missing; fi", case.source_id)
        assert exists == "exists", f"Image file does not exist on DUT: {source}"
        viewers = (
            ("jarvis", "com.cmcc.jarvis/.setting.SettingImageShowActivity", f"--es img_path {shlex.quote(source)}"),
            ("droidlogic", "com.droidlogic.imageplayer/.FullImageActivity",
             f"-a android.intent.action.VIEW -d {shlex.quote('file://' + source)} -t 'image/*'"),
        )
        for viewer, component, arguments in viewers:
            if viewer not in selected_viewers:
                continue
            package, activity = component.split("/", 1)
            if not package_exists(dut, package):
                step_evidence("图片播放器跳过", {
                    "source": source, "viewer": viewer, "component": component,
                    "package": package, "status": "skipped", "reason": "package_not_installed",
                }, evidence_type="checkpoint")
                continue
            executed = True
            output = _shell(dut, f"am start -W -n {component} {arguments}", case.source_id)
            assert re.search(r"^Status:\s*ok\s*$", output, re.M), (
                f"Image viewer launch failed: {source}, {component}: {output}"
            )
            expected = package + "/" + package + activity
            try:
                actual = _resumed_activity_components(_shell(dut, "dumpsys activity activities", case.source_id))
                assert expected in actual, (
                    f"Image viewer is not resumed: {source}, expected={expected}, actual={actual}"
                )
                step_evidence("图片播放器启动检查", {
                    "source": source, "viewer": viewer, "component": component, "launch_output": output,
                    "resumed_activities": actual, "check": "launch_and_activity_only",
                    "image_rendering_verified": False, "minimum_display_duration_s": 5,
                }, evidence_type="checkpoint")
                time.sleep(5)
            finally:
                back_actions = _exit_image_viewer(dut, expected, case.source_id)
                step_evidence("图片播放器退出检查", {
                    "source": source, "viewer": viewer, "component": component,
                    "back_actions": back_actions, "exited": True,
                }, evidence_type="checkpoint")
    if not executed:
        pytest.skip("None of the selected image viewer packages is installed on DUT")


def check_legacy_media(case, dut, params, serial):
    sources = to_string_list(_case_param(case, params, "media_files", [])) or list(case.media_sources)
    if not sources:
        pytest.skip(f"source {case.source_id} requires configured media")
    from core.testing.tool.dut_tool.features.local_playback import assert_media_session_state
    timeout_s = max(to_float(_case_param(case, params, "playback_timeout_s", 10), default=10), 0)
    for source in sources:
        try:
            dut.start_file(source)
            assert_media_session_state(dut, file_path=source, expected_state="PLAYING", timeout_s=timeout_s)
        finally:
            dut.stop_player()

MIDDLE_SCREEN_HANDLERS: dict[str, Handler] = {
    "hdmi_objective": check_hdmi_objective,
    "media": check_legacy_media,
    "image": check_image,
}


def execute_middle_screen_case(case: Any, dut: Any, params: Mapping[str, Any], *, serial: str) -> None:
    step_evidence("输入参数", dict(params), evidence_type="parameters")
    MIDDLE_SCREEN_HANDLERS[case.executor](case, dut, params, serial)


def prepare_middle_screen_case(case: Any, dut: Any, params: Mapping[str, Any], *, serial: str) -> dict[str, Any]:
    state: dict[str, Any] = {"serial": serial}
    if case.source_id == 18:
        state["cpu_snapshot"] = dut.cpu_frequency_snapshot()
    elif case.source_id == 21:
        state["wifi_enabled"] = "enabled" in _shell(dut, "cmd wifi status", case.source_id).lower()
    return state


def run_middle_screen_action(case: Any, dut: Any, params: Mapping[str, Any], *, serial: str) -> dict[str, Any]:
    source_id = case.source_id
    if source_id == 4:
        output = _shell(dut, "cat /proc/mounts; ls -1 /sys/block", source_id)
        return {"output": output, "matcher": str(_case_param(case, params, "usb_match", "") or "").strip()}
    if source_id == 10:
        interface = str(_case_param(case, params, "interface", "eth0") or "eth0").strip()
        return {
            "interface": interface,
            "addresses": parse_interface_addresses(_shell(dut, f"ip addr show {interface}", source_id)),
            "expected_speed_mbps": to_int(_case_param(case, params, "expected_speed_mbps", 0), default=0),
            "actual_speed_mbps": parse_link_speed_mbps(_shell(dut, f"cat /sys/class/net/{interface}/speed", source_id)),
        }
    if source_id == 18:
        frequencies = to_string_list(_case_param(case, params, "frequencies", []))
        samples = []
        for frequency in frequencies:
            dut.set_cpu_frequency(frequency)
            samples.append({"expected": frequency, "actual": dut.wait_current_cpu_frequency(frequency)})
        return {"frequencies": frequencies, "samples": samples}
    if source_id == 20:
        return {"mmc_output": _shell(dut, "dmesg | grep -i mmc", source_id)}
    if source_id == 21:
        dut.wifi_enable()
        interface_output = _shell(dut, "ip link show wlan0", source_id)
        bands = []
        for band in ("2g", "5g"):
            ssid = str(_case_param(case, params, f"wifi_{band}_ssid", "") or "").strip()
            password = str(_case_param(case, params, f"wifi_{band}_password", "") or "")
            scanned = bool(ssid and dut.scan(ssid))
            connected = bool(scanned and dut.connect(ssid, password, "wpa2" if password else "open", forget_existing=False))
            addresses = parse_interface_addresses(_shell(dut, "ip addr show wlan0", source_id)) if connected else {"ipv4": (), "ipv6": ()}
            bands.append({"band": band, "ssid": ssid, "scanned": scanned, "connected": connected, "addresses": addresses})
        return {"interface_output": interface_output, "bands": bands}
    if source_id == 29:
        output = _shell(dut, "cat /sys/class/thermal/thermal_zone0/temp", source_id)
        try:
            value = parse_thermal_millicelsius(output)
        except ValueError:
            value = None
        return {"raw": output, "temperature_millicelsius": value}
    if source_id in (31, 32):
        return {"adb_ready": bool(dut.check_adb_status(waitTime=5)), "serial": serial, "network_serial": bool(re.match(r"^(?:\[.*\]|[^:]+):\d+$", serial))}
    if source_id == 33:
        output = dut.wm_size()
        try:
            dimensions = parse_wm_size(output)
        except ValueError:
            dimensions = None
        return {"output": output, "dimensions": dimensions}
    if source_id in (49, 52, 53, 54, 55):
        return _network_facts(case, dut, params)
    if source_id in TARGET_SOURCE_IDS:
        return _media_facts(case, dut, params)
    raise KeyError(f"No phased middle-screen action for source {source_id}")


def _network_facts(case: Any, dut: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    interface = str(_case_param(case, params, "interface", "") or ("wlan0" if case.source_id in (54, 55) else "eth0"))
    addresses = parse_interface_addresses(_shell(dut, f"ip addr show {interface}", case.source_id))
    facts: dict[str, Any] = {"interface": interface, "addresses": addresses}
    if case.source_id in (49, 52, 54):
        target = str(_case_param(case, params, "ipv4_ping_target", "www.baidu.com"))
        facts["ipv4_target"] = target
        facts["ipv4_reachable"] = bool(dut.ping(interface=interface, hostname=target))
    if case.source_id in (49, 53, 55):
        target = str(_case_param(case, params, "ipv6_ping_target", "www.baidu.com"))
        output = _shell(dut, f"ping6 -c 3 {target}", case.source_id)
        facts.update({"ipv6_target": target, "ipv6_ping_output": output, "ipv6_reachable": parse_ping_success(output)})
    return facts


def _media_facts(case: Any, dut: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    configured = _case_param(case, params, "media_files", []) if case.source_id in (57, 58, 59, 114) else _case_param(case, params, "media_url", "")
    sources = to_string_list(configured) or list(case.media_sources)
    timeout_s = max(to_float(_case_param(case, params, "playback_timeout_s", 10), default=10), 0)
    duration_s = max(to_float(_case_param(case, params, "playback_duration_s", 86400), default=86400), 0) if case.source_id == 114 else 0
    from core.testing.tool.dut_tool.features.local_playback import assert_media_session_state
    results = []
    for index, source in enumerate(sources):
        if index:
            dut.stop_player()
        dut.start_file(source)
        try:
            samples = observe_media_playback(
                dut,
                source,
                duration_s=duration_s,
                poll_interval_s=min(timeout_s, 10),
                assert_state=lambda device, **kwargs: assert_media_session_state(device, timeout_s=timeout_s, **kwargs),
            )
        except AssertionError as exc:
            results.append({"source": source, "playing": False, "error": str(exc), "samples": []})
        else:
            results.append({"source": source, "playing": True, "samples": samples})
    return {"sources": sources, "timeout_s": timeout_s, "duration_s": duration_s, "results": results}


def cleanup_middle_screen_case(case: Any, dut: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    if case.source_id == 18:
        snapshot = state["cpu_snapshot"]
        actual = dut.restore_cpu_frequency(snapshot)
        return {"expected": snapshot.current_frequency, "actual": actual, "restored": actual == snapshot.current_frequency}
    if case.source_id == 21:
        if state.get("wifi_enabled"):
            dut.wifi_enable()
        else:
            dut.wifi_disable()
        return {"wifi_enabled": bool(state.get("wifi_enabled")), "restored": True}
    if case.source_id in (57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 95, 96, 97, 98, 114):
        dut.stop_player()
        return {"player_stopped": True, "restored": True}
    return {"restored": True, "changed_state": False}
