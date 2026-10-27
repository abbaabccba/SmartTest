from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import re
import tempfile
import time
from typing import Any, Iterable
from urllib.parse import urlsplit

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.config.value_conversion import to_float, to_int, to_string_list
from core.testing.test_context import smarttest_context
from core.testing.runtime.steps import step_log


DEFAULT_MEDIA_DIR = "/storage/*/Movies /storage/*/Video"
EXOPLAYER_ACTIVITY = "com.droidlogic.exoplayer2.demo/com.droidlogic.videoplayer.MoviePlayer"
EXOPLAYER_PACKAGE = "com.droidlogic.exoplayer2.demo"
SUPPORTED_MEDIA_SUFFIXES = (".mkv", ".mp4", ".ts", ".webm", ".avi", ".mov", ".m4v")
MEDIA_MIME_TYPES = {
    ".avi": "video/avi",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".ts": "video/mp2t",
    ".webm": "video/webm",
}
DEFAULT_MEDIA_SCAN_COMMAND = "find /storage/*/Movies /storage/*/Video -maxdepth 1 -type f 2>/dev/null"
MEDIA_DIR_SCAN_COMMAND = "find /storage -maxdepth 2 -type d \\( -name Movies -o -name Video \\) 2>/dev/null"
SCREENSHOT_REMOTE_PATH = "/sdcard/smarttest_playback_screen.png"

PLAY_KEYEVENT = "KEYCODE_MEDIA_PLAY"
PAUSE_KEYEVENT = "KEYCODE_MEDIA_PAUSE"
BACK_KEYEVENT = "KEYCODE_BACK"
DIRECT_ACTION_KEYEVENTS = {
    "pause": PAUSE_KEYEVENT,
    "seek_forward": "KEYCODE_MEDIA_FAST_FORWARD",
    "seek_backward": "KEYCODE_MEDIA_REWIND",
}
CONTROLLED_ACTIONS = {"back_to_start", "seek_to_end"}
SUPPORTED_ACTIONS = set(DIRECT_ACTION_KEYEVENTS) | CONTROLLED_ACTIONS
SEEK_TO_END_PROGRESS_RATIO = 0.75
SHORT_VIDEO_SEEK_TO_END_PROGRESS_RATIO = 0.50
SHORT_VIDEO_SEEK_TO_END_DURATION_SEC = 120
SEEK_TO_START_PROGRESS_RATIO = 0.05
PROGRESS_SCREENSHOT_RETRIES = 3
PROGRESS_SCREENSHOT_RETRY_INTERVAL_SEC = 0.5
PROGRESS_TAP_Y_RATIO = 0.20
PROGRESS_SCREENSHOT_DELAY_SEC = 0.3
ACTION_VERIFY_SETTLE_SEC = 1.0
SEEK_FORWARD_MIN_REMAINING_SEC = 5
SEEK_FORWARD_MIN_DURATION_SEC = 30
POST_ACTION_STATE_RETRIES = 20
POST_ACTION_STATE_RETRY_INTERVAL_SEC = 1.0
START_READY_STATE_RETRIES = 10
START_READY_STATE_RETRY_INTERVAL_SEC = 1.0
PLAYBACK_FINISH_EXTRA_SEC = 30.0
PLAYBACK_FINISH_POLL_SEC = 2.0


@dataclass(frozen=True)
class StressAction:
    loop_index: int
    file_path: str
    action: str


@dataclass(frozen=True)
class PlaybackProgress:
    current_seconds: int
    duration_seconds: int

    @property
    def ratio(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return self.current_seconds / self.duration_seconds


@dataclass(frozen=True)
class PlaybackControls:
    progress: PlaybackProgress | None
    bar_x1: int
    bar_x2: int
    bar_y: int
    thumb_x: int | None


def list_media_files(dut=None, *, nodeid: str | None = None) -> list[str]:
    resolved_dut = _ensure_dut(dut)
    command = _media_scan_command(_normalize_media_dir(_media_dir_from_state(nodeid)))
    output = resolved_dut.run_device_shell(command)
    return parse_media_file_listing(output)


def list_media_dirs(selected_serial: str | None = None, dut=None) -> list[str]:
    resolved_dut = _ensure_dut(dut) if dut is not None else _default_dut(selected_serial)
    return parse_media_dir_listing(resolved_dut.run_device_shell(MEDIA_DIR_SCAN_COMMAND))


def parse_media_dir_listing(output: str) -> list[str]:
    directories: list[str] = []
    seen: set[str] = set()
    for raw_line in str(output or "").splitlines():
        path = normalize_playback_path(raw_line).rstrip("/")
        if not path or path in seen:
            continue
        if not (path.endswith("/Movies") or path.endswith("/Video")):
            continue
        seen.add(path)
        directories.append(path)
    return directories


def parse_media_file_listing(output: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for raw_line in str(output or "").splitlines():
        path = normalize_playback_path(raw_line)
        if not path or path in seen:
            continue
        if not path.lower().endswith(SUPPORTED_MEDIA_SUFFIXES):
            continue
        seen.add(path)
        files.append(path)
    return files


def discover_media_files(media_dir: str, dut) -> list[str]:
    return parse_media_file_listing(_ensure_dut(dut).run_device_shell(_media_scan_command(_normalize_media_dir(media_dir))))


def start_file(file_path: str, dut) -> None:
    start_playback(file_path, dut)
    dismiss_resume_dialog(dut)


def stop_player(dut) -> None:
    stop_exoplayer(dut)


def exit_player(dut) -> None:
    step_log("local_playback_exit_player keyevent=KEYCODE_BACK")
    _ensure_dut(dut).keyevent(BACK_KEYEVENT)


def resume_playback(dut) -> None:
    _ensure_dut(dut).keyevent(PLAY_KEYEVENT)


def run_media_action(action: str, dut) -> None:
    run_action(action, dut)


def build_stress_plan(
    *,
    media_files: Iterable[str],
    actions: Iterable[str],
    loop_count: int,
    random_playback: bool,
) -> list[StressAction]:
    normalized_files = to_string_list(media_files)
    normalized_actions = [action for action in to_string_list(actions) if action in SUPPORTED_ACTIONS]
    total_loops = max(to_int(loop_count, default=1), 1)
    plan: list[StressAction] = []
    for loop_index in range(1, total_loops + 1):
        loop_files = list(normalized_files)
        if random_playback:
            random.shuffle(loop_files)
        for file_path in loop_files:
            action_plan = [action for action in normalized_actions for _ in range(2)]
            random.shuffle(action_plan)
            for action in action_plan:
                plan.append(StressAction(loop_index=loop_index, file_path=file_path, action=action))
    return plan


def action_shell_command(action: str) -> list[str]:
    return ["input", "keyevent", _action_keyevent(action)]


def start_playback(file_path: str, dut) -> None:
    component = getattr(dut, "EXOPLAYER_DEMO_COMPONENT", EXOPLAYER_ACTIVITY)
    command = _start_playback_shell_command(file_path, component)
    step_log(f"local_playback_start_command={command}")
    _ensure_dut(dut).run_device_shell(command)


def stop_exoplayer(dut) -> None:
    package_name = _exoplayer_package(dut)
    command = f"am force-stop {package_name}"
    step_log(f"local_playback_stop_command={command}")
    _ensure_dut(dut).run_device_shell(command)


def dismiss_resume_dialog(dut) -> None:
    device = _ensure_dut(dut)
    for _ in range(5):
        image = capture_screen_image(device)
        target = _resume_dialog_cancel_target_from_image(image)
        if target is not None:
            x, y, dialog = target
            x1, y1, x2, y2 = dialog
            step_log(
                "local_playback_resume_dialog_dismiss "
                f"method=screenshot dialog=[{x1},{y1}][{x2},{y2}] cancel=[{x},{y}]"
            )
            device.tap(x, y)
            time.sleep(0.5)
            return
        time.sleep(0.3)


def run_action(action: str, dut) -> None:
    normalized = str(action or "").strip()
    if normalized == "seek_to_end":
        seek_progress_to_ratio(dut, SEEK_TO_END_PROGRESS_RATIO, normalized)
        return
    if normalized == "back_to_start":
        seek_progress_to_ratio(dut, SEEK_TO_START_PROGRESS_RATIO, normalized)
        return
    _ensure_dut(dut).keyevent(_action_keyevent(normalized))


def _emit_loop_step_started(*, nodeid: str, loop_index: int, loop_count: int) -> None:
    smarttest_context().events.emit(
        "step_started",
        step_id="local_playback_stress.loop",
        case_nodeid=nodeid,
        title=f"Loop {loop_index}/{loop_count}: Run local playback stress loop",
        kind="step",
        definition_id="local_playback.loop",
        expected="ExoPlayer starts each selected local file and receives the selected stress actions.",
    )


def run_local_playback_stress(
    *,
    nodeid: str,
    selected_serial: str | None,
    trigger: str,
) -> None:
    params = smarttest_context().params.case_values(nodeid)
    playback = _default_dut(selected_serial)
    media_dir = _normalize_media_dir(params.get("local_playback_stress:media_dir", DEFAULT_MEDIA_DIR))
    selected_files = to_string_list(params.get("local_playback_stress:media_files", []))
    if not selected_files:
        raise AssertionError("Select at least one local playback media file before starting the run.")

    actions = [action for action in to_string_list(params.get("local_playback_stress:actions", [])) if action in SUPPORTED_ACTIONS]
    if not actions:
        raise AssertionError("Select at least one supported local playback stress action.")

    loop_count = max(to_int(params.get("local_playback_stress:loop_count", 1), default=1), 1)
    random_playback = bool(params.get("local_playback_stress:random_playback", False))
    action_interval_sec = max(to_float(params.get("local_playback_stress:action_interval_sec", 3), default=3.0), 0.0)
    start_wait_sec = max(to_float(params.get("local_playback_stress:start_wait_sec", 10), default=10.0), 0.0)
    step_log(
        "local_playback_stress "
        f"trigger={trigger} dut={selected_serial or '<default>'} "
        f"files={len(selected_files)} actions={len(actions)} loops={loop_count}"
    )

    for loop_index in range(1, loop_count + 1):
        _emit_loop_step_started(nodeid=nodeid, loop_index=loop_index, loop_count=loop_count)
        loop_files = list(selected_files)
        if random_playback:
            random.shuffle(loop_files)
        for file_path in loop_files:
            action_plan = [action for action in actions for _ in range(2)]
            random.shuffle(action_plan)
            step_log(f"loop={loop_index}/{loop_count} start file={file_path}")
            start_file(file_path, playback)
            assert_media_session_state(playback, file_path=file_path, expected_state="PLAYING")
            if start_wait_sec:
                time.sleep(start_wait_sec)
            for action in action_plan:
                executed = verify_stress_action(
                    dut=playback,
                    file_path=file_path,
                    action=action,
                    action_interval_sec=action_interval_sec,
                )
                if executed is None:
                    step_log(f"local_playback_file_actions_aborted file={file_path} action={action}")
                    break
                if executed and action_interval_sec:
                    time.sleep(action_interval_sec)
            _wait_for_playback_finished_after_actions(playback, file_path)
            exit_player(playback)


def verify_stress_action(*, dut, file_path: str, action: str, action_interval_sec: float = 0.0) -> bool | None:
    device = _ensure_dut(dut)
    try:
        before = read_playback_progress(device)
    except AssertionError as exc:
        reason = "playback_progress_unreadable"
        media_session = str(device.run_device_shell("dumpsys media_session") or "")
        still_playing = _media_session_state(media_session) == "PLAYING" and _media_session_matches_file(media_session, file_path)
        step_log(
            "local_playback_action_skipped "
            f"action={action} file={file_path} "
            f"current=unknown duration=unknown remaining=unknown "
            f"reason={reason} media={_media_session_state_summary(media_session)} "
            f"detail={str(exc).splitlines()[0]}"
        )
        return False if still_playing else None
    state_ok, state_reason, state_summary = _preflight_media_session_ready(device, file_path)
    if not state_ok:
        step_log(
            "local_playback_action_skipped "
            f"action={action} file={file_path} "
            f"current={before.current_seconds} duration={before.duration_seconds} "
            f"remaining={_playback_remaining_seconds(before)} "
            f"reason={state_reason} media={state_summary}"
        )
        return None
    safe, reason = _stress_action_safety(action, before, action_interval_sec=action_interval_sec)
    if not safe:
        step_log(
            "local_playback_action_skipped "
            f"action={action} file={file_path} "
            f"current={before.current_seconds} duration={before.duration_seconds} "
            f"remaining={_playback_remaining_seconds(before)} reason={reason}"
        )
        return False

    if action == "pause":
        device.keyevent(PAUSE_KEYEVENT)
        time.sleep(ACTION_VERIFY_SETTLE_SEC)
        device.keyevent(PLAY_KEYEVENT)
        time.sleep(ACTION_VERIFY_SETTLE_SEC)
        post_ok, post_reason, post_summary = _wait_for_post_action_playing(device, file_path)
        if not post_ok:
            step_log(
                "local_playback_action_skipped "
                f"action={action} file={file_path} before={_format_progress(before)} "
                f"reason={post_reason} media={post_summary}"
            )
            return None
        step_log(
            "local_playback_action_executed "
            f"action={action} file={file_path} before={_format_progress(before)}"
        )
        return True

    try:
        if action == "seek_to_end":
            seek_progress_to_ratio(device, _seek_to_end_target_ratio(before), action)
        else:
            run_action(action, device)
    except (AssertionError, RuntimeError) as exc:
        media_session = str(device.run_device_shell("dumpsys media_session") or "")
        still_playing = _media_session_state(media_session) == "PLAYING" and _media_session_matches_file(media_session, file_path)
        step_log(
            "local_playback_action_failed "
            f"action={action} file={file_path} before={_format_progress(before)} "
            f"media={_media_session_state_summary(media_session)} detail={str(exc).splitlines()[0]}"
        )
        return False if still_playing else None
    time.sleep(ACTION_VERIFY_SETTLE_SEC)
    post_ok, post_reason, post_summary = _wait_for_post_action_playing(device, file_path)
    if not post_ok:
        step_log(
            "local_playback_action_skipped "
            f"action={action} file={file_path} before={_format_progress(before)} "
            f"reason={post_reason} media={post_summary}"
        )
        return None
    step_log(
        "local_playback_action_executed "
        f"action={action} file={file_path} before={_format_progress(before)}"
    )
    return True


def assert_media_session_state(
    dut,
    *,
    file_path: str,
    expected_state: str,
    timeout_s: float = START_READY_STATE_RETRIES * START_READY_STATE_RETRY_INTERVAL_SEC,
) -> None:
    device = _ensure_dut(dut)
    last_text = ""
    attempts = max(1, int(float(timeout_s) / START_READY_STATE_RETRY_INTERVAL_SEC))
    requires_source_match = urlsplit(file_path).scheme.lower() not in ("igmp", "rtp", "udp")
    for attempt in range(1, attempts + 1):
        text = str(device.run_device_shell("dumpsys media_session") or "")
        last_text = text
        state = _media_session_state(text)
        matches_file = not requires_source_match or _media_session_matches_file(text, file_path)
        if state == expected_state and matches_file:
            step_log(
                "local_playback_media_session_ready "
                f"file={file_path} expected={expected_state} state={_media_session_state_summary(text)}"
            )
            return
        step_log(
            "local_playback_media_session_waiting "
            f"file={file_path} expected={expected_state} attempt={attempt} "
            f"state={_media_session_state_summary(text)}"
        )
        if attempt < attempts:
            time.sleep(START_READY_STATE_RETRY_INTERVAL_SEC)
    raise AssertionError(
        "Local playback media session state mismatch.\n"
        f"file={file_path}\n"
        f"expected_state={expected_state}\n"
        f"state={_media_session_state_summary(last_text)}"
    )


def read_playback_progress(
    dut,
    *,
    retries: int = PROGRESS_SCREENSHOT_RETRIES,
    retry_interval_sec: float = PROGRESS_SCREENSHOT_RETRY_INTERVAL_SEC,
) -> PlaybackProgress:
    device = _ensure_dut(dut)
    last_error = ""
    for attempt in range(1, max(int(retries), 1) + 1):
        tapped = attempt > 1
        if tapped:
            tap_playback_overlay(device)
            time.sleep(PROGRESS_SCREENSHOT_DELAY_SEC)
        try:
            image = capture_screen_image(device)
            progress = _playback_progress_from_image(image)
        except RuntimeError as exc:
            last_error = str(exc)
            progress = None
        if progress is not None:
            step_log(f"local_playback_progress_read attempt={attempt} tapped={str(tapped).lower()} progress={_format_progress(progress)}")
            return progress
        step_log(f"local_playback_progress_missing attempt={attempt} tapped={str(tapped).lower()} error={last_error}")
        if attempt < retries:
            time.sleep(max(float(retry_interval_sec), 0.0))
    raise AssertionError(
        "Unable to read local playback progress from screenshot after retries.\n"
        f"attempts={max(int(retries), 1)}\n"
        f"last_error={last_error}"
    )


def seek_progress_to_ratio(dut, ratio: float, action_name: str) -> None:
    device = _ensure_dut(dut)
    controls = read_playback_controls(device)
    target_x = controls.bar_x1 + int((controls.bar_x2 - controls.bar_x1) * max(0.0, min(float(ratio), 1.0)))
    step_log(
        "local_playback_progress_seek_tap "
        f"action={action_name} ratio={ratio:.2f} "
        f"bounds=[{controls.bar_x1},{controls.bar_y}][{controls.bar_x2},{controls.bar_y}] "
        f"thumb_x={controls.thumb_x if controls.thumb_x is not None else 'unknown'} "
        f"target=({target_x},{controls.bar_y})"
    )
    device.tap(target_x, controls.bar_y)


def capture_screen_image(dut) -> Image.Image:
    device = _ensure_dut(dut)
    with tempfile.TemporaryDirectory(prefix="smarttest_playback_") as tmp_dir:
        local_path = Path(tmp_dir) / "screen.png"
        device.run_device_shell(f"screencap -p {SCREENSHOT_REMOTE_PATH}")
        device.pull(SCREENSHOT_REMOTE_PATH, str(local_path))
        if not local_path.exists():
            raise RuntimeError("pull screenshot failed: local file was not created")
        return Image.open(local_path).convert("RGB")


def tap_playback_overlay(dut) -> None:
    width, height = screen_size(dut)
    _ensure_dut(dut).tap(width // 2, max(1, int(height * PROGRESS_TAP_Y_RATIO)))


def screen_size(dut) -> tuple[int, int]:
    text = str(_ensure_dut(dut).run_device_shell("wm size") or "")
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 1920, 1080


def _playback_progress_from_image(image: Image.Image) -> PlaybackProgress | None:
    pair = _ocr_playback_time_pair(image)
    if pair is None:
        return None
    current_seconds, duration_seconds = pair
    if duration_seconds <= 0:
        return None
    if current_seconds < 0:
        return None
    overshoot_tolerance = _ocr_current_overshoot_tolerance_seconds(duration_seconds)
    if current_seconds > duration_seconds + overshoot_tolerance:
        return None
    if current_seconds > duration_seconds:
        current_seconds = duration_seconds
    return PlaybackProgress(current_seconds=current_seconds, duration_seconds=duration_seconds)


def _ocr_current_overshoot_tolerance_seconds(duration_seconds: int) -> int:
    return max(1, min(3, int(round(duration_seconds * 0.005))))


def read_playback_controls(
    dut,
    *,
    retries: int = PROGRESS_SCREENSHOT_RETRIES,
    retry_interval_sec: float = PROGRESS_SCREENSHOT_RETRY_INTERVAL_SEC,
) -> PlaybackControls:
    device = _ensure_dut(dut)
    last_error = ""
    for attempt in range(1, max(int(retries), 1) + 1):
        tapped = attempt > 1
        if tapped:
            tap_playback_overlay(device)
            time.sleep(PROGRESS_SCREENSHOT_DELAY_SEC)
        image = capture_screen_image(device)
        progress = _playback_progress_from_image(image)
        try:
            controls = _playback_controls_from_image(image, progress=progress)
        except AssertionError as exc:
            last_error = str(exc)
            step_log(f"local_playback_controls_missing attempt={attempt} tapped={str(tapped).lower()} error={last_error}")
            if attempt < retries:
                time.sleep(max(float(retry_interval_sec), 0.0))
            continue
        step_log(
            "local_playback_controls_read "
            f"attempt={attempt} tapped={str(tapped).lower()} progress={_format_progress(progress)} "
            f"bar=[{controls.bar_x1},{controls.bar_y}][{controls.bar_x2},{controls.bar_y}] "
            f"thumb_x={controls.thumb_x if controls.thumb_x is not None else 'unknown'}"
        )
        return controls
    raise AssertionError(
        "Unable to detect local playback progress bar from screenshot after retries.\n"
        f"attempts={max(int(retries), 1)}\n"
        f"last_error={last_error}"
    )


def _playback_controls_from_image(image: Image.Image, *, progress: PlaybackProgress | None = None) -> PlaybackControls:
    width, height = image.size
    crop_top = max(0, height - 220)
    crop = np.array(image.crop((0, crop_top, width, height)).convert("RGB"))
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    _, bright = cv2.threshold(gray, 35, 255, cv2.THRESH_BINARY)
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (80, 1))
    horizontal = cv2.morphologyEx(bright, cv2.MORPH_OPEN, horizontal_kernel)
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[int, int, int, int, int]] = []
    for contour in contours:
        x, y, bar_width, bar_height = cv2.boundingRect(contour)
        if bar_width < int(width * 0.5):
            continue
        if bar_height > 10:
            continue
        if y < 20:
            continue
        candidates.append((x, y, bar_width, bar_height, bar_width))
    if not candidates:
        raise AssertionError("Unable to detect local playback progress bar from screenshot.")

    x, y, bar_width, bar_height, _score = max(candidates, key=lambda item: item[4])
    bar_y = crop_top + y + bar_height // 2
    bar_x1 = x
    bar_x2 = x + bar_width
    thumb_x = _detect_progress_thumb_x(crop, crop_top=crop_top, bar_y=bar_y, bar_x1=bar_x1, bar_x2=bar_x2)
    return PlaybackControls(progress=progress, bar_x1=bar_x1, bar_x2=bar_x2, bar_y=bar_y, thumb_x=thumb_x)


def _detect_progress_thumb_x(
    crop: np.ndarray,
    *,
    crop_top: int,
    bar_y: int,
    bar_x1: int,
    bar_x2: int,
) -> int | None:
    local_bar_y = bar_y - crop_top
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([180, 80, 255]))
    strip_y1 = max(0, local_bar_y - 10)
    strip_y2 = min(mask.shape[0], local_bar_y + 11)
    strip = mask[strip_y1:strip_y2, :]
    xs = np.where(np.any(strip > 0, axis=0))[0]
    clusters = _cluster_sorted_points(xs.tolist())
    candidates = [
        ((start + end) // 2, end - start + 1)
        for start, end in clusters
        if 8 <= end - start + 1 <= 80 and start >= bar_x1 - 30 and end <= bar_x2 + 30
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def _stress_action_safety(
    action: str,
    progress: PlaybackProgress,
    *,
    action_interval_sec: float = 0.0,
) -> tuple[bool, str]:
    if progress.duration_seconds <= 0:
        return False, "playback_duration_invalid"

    normalized = str(action or "").strip()
    remaining_seconds = _playback_remaining_seconds(progress)

    if normalized == "seek_to_end":
        if progress.ratio >= _seek_to_end_target_ratio(progress):
            return False, "seek_to_end_already_near_target"
    elif normalized == "seek_forward":
        if progress.duration_seconds < SEEK_FORWARD_MIN_DURATION_SEC:
            return False, "video_too_short_for_seek_forward"
        if remaining_seconds <= SEEK_FORWARD_MIN_REMAINING_SEC:
            return False, "remaining_time_too_short"

    return True, "safe"


def _seek_to_end_target_ratio(progress: PlaybackProgress) -> float:
    if progress.duration_seconds < SHORT_VIDEO_SEEK_TO_END_DURATION_SEC:
        return SHORT_VIDEO_SEEK_TO_END_PROGRESS_RATIO
    return SEEK_TO_END_PROGRESS_RATIO


def _playback_remaining_seconds(progress: PlaybackProgress) -> int:
    return max(progress.duration_seconds - progress.current_seconds, 0)


def _preflight_media_session_ready(dut, file_path: str) -> tuple[bool, str, str]:
    text = str(_ensure_dut(dut).run_device_shell("dumpsys media_session") or "")
    summary = _media_session_state_summary(text)
    if not _media_session_matches_file(text, file_path):
        return False, "media_session_not_current_file", summary
    state = _media_session_state(text)
    if state != "PLAYING":
        return False, "media_session_not_playing", summary
    return True, "safe", summary


def _wait_for_post_action_playing(dut, file_path: str) -> tuple[bool, str, str]:
    device = _ensure_dut(dut)
    last_summary = "pkg=False active=unknown state=unknown"
    last_state: str | None = None
    for attempt in range(1, POST_ACTION_STATE_RETRIES + 1):
        text = str(device.run_device_shell("dumpsys media_session") or "")
        last_summary = _media_session_state_summary(text)
        if not _media_session_matches_file(text, file_path):
            return False, "media_session_not_current_file", last_summary
        last_state = _media_session_state(text)
        if last_state == "PLAYING":
            return True, "safe", last_summary
        if last_state == "PAUSED":
            step_log(
                "local_playback_post_action_resume "
                f"file={file_path} attempt={attempt} state={last_summary}"
            )
            device.keyevent(PLAY_KEYEVENT)
            if attempt < POST_ACTION_STATE_RETRIES:
                time.sleep(POST_ACTION_STATE_RETRY_INTERVAL_SEC)
            continue
        if last_state != "BUFFERING":
            return False, "media_session_not_playing", last_summary
        if attempt == 3:
            step_log(
                "local_playback_post_action_buffering_resume "
                f"file={file_path} attempt={attempt} state={last_summary}"
            )
            device.keyevent(PLAY_KEYEVENT)
        if attempt < POST_ACTION_STATE_RETRIES:
            time.sleep(POST_ACTION_STATE_RETRY_INTERVAL_SEC)
    return False, "media_session_still_buffering", last_summary


def _wait_for_playback_finished_after_actions(dut, file_path: str) -> bool:
    device = _ensure_dut(dut)
    try:
        progress = read_playback_progress(device)
    except AssertionError as exc:
        step_log(f"local_playback_wait_finish_skipped file={file_path} reason=progress_unreadable detail={str(exc).splitlines()[0]}")
        return False
    timeout_sec = _playback_remaining_seconds(progress) + PLAYBACK_FINISH_EXTRA_SEC
    step_log(f"local_playback_wait_finish_start file={file_path} progress={_format_progress(progress)} timeout={timeout_sec:.1f}")
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        text = str(device.run_device_shell("dumpsys media_session") or "")
        state = _media_session_state(text)
        if not _media_session_matches_file(text, file_path) or state == "STOPPED":
            step_log(f"local_playback_wait_finish_done file={file_path} state={_media_session_state_summary(text)}")
            return True
        time.sleep(min(PLAYBACK_FINISH_POLL_SEC, max(deadline - time.monotonic(), 0.0)))
    step_log(f"local_playback_wait_finish_timeout file={file_path} state={_media_session_state_summary(str(device.run_device_shell('dumpsys media_session') or ''))}")
    return False


def _time_ratio_matches(progress: PlaybackProgress, expected_ratio: float) -> bool:
    tolerance = 0.08 if expected_ratio >= 0.5 else 0.05
    actual_ratio = progress.current_seconds / progress.duration_seconds
    return abs(actual_ratio - expected_ratio) <= tolerance


def _cluster_sorted_points(values: list[int]) -> list[tuple[int, int]]:
    if not values:
        return []
    clusters: list[tuple[int, int]] = []
    start = values[0]
    previous = values[0]
    for value in values[1:]:
        if value <= previous + 1:
            previous = value
            continue
        clusters.append((start, previous))
        start = previous = value
    clusters.append((start, previous))
    return clusters


def _resume_dialog_cancel_target_from_image(image: Image.Image) -> tuple[int, int, tuple[int, int, int, int]] | None:
    width, height = image.size
    arr = np.array(image.convert("RGB"))
    channel_delta = arr.max(axis=2) - arr.min(axis=2)
    gray_mask = (
        (arr[:, :, 0] >= 35)
        & (arr[:, :, 0] <= 105)
        & (arr[:, :, 1] >= 35)
        & (arr[:, :, 1] <= 105)
        & (arr[:, :, 2] >= 35)
        & (arr[:, :, 2] <= 105)
        & (channel_delta <= 12)
    ).astype(np.uint8) * 255
    gray_mask = cv2.morphologyEx(gray_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    contours, _ = cv2.findContours(gray_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    dialog_candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, dialog_width, dialog_height = cv2.boundingRect(contour)
        if not (int(width * 0.25) <= dialog_width <= int(width * 0.6)):
            continue
        if not (int(height * 0.12) <= dialog_height <= int(height * 0.35)):
            continue
        center_x = x + dialog_width / 2
        center_y = y + dialog_height / 2
        if abs(center_x - width / 2) > width * 0.18:
            continue
        if not (height * 0.35 <= center_y <= height * 0.65):
            continue
        dialog_candidates.append((x, y, x + dialog_width, y + dialog_height))
    if not dialog_candidates:
        return None

    dialog = max(dialog_candidates, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
    x1, y1, x2, y2 = dialog
    lower_y1 = y1 + int((y2 - y1) * 0.55)
    button_area = arr[lower_y1:y2, x1:x2, :]
    cyan_mask = (
        (button_area[:, :, 0] <= 80)
        & (button_area[:, :, 1] >= 110)
        & (button_area[:, :, 2] >= 100)
        & ((button_area[:, :, 1].astype(np.int16) - button_area[:, :, 0].astype(np.int16)) >= 60)
    )
    local_xs = np.where(np.any(cyan_mask, axis=0))[0]
    clusters: list[tuple[int, int]] = []
    if len(local_xs) > 0:
        max_button_text_gap = max(24, int(width * 0.018))
        start = previous = int(local_xs[0])
        for value in [int(item) for item in local_xs[1:]]:
            if value <= previous + max_button_text_gap:
                previous = value
                continue
            clusters.append((start, previous))
            start = previous = value
        clusters.append((start, previous))
    clusters = [(start, end) for start, end in clusters if end - start + 1 >= max(8, int(width * 0.004))]
    if len(clusters) < 2:
        return None

    first_start, first_end = clusters[0]
    first_mask = cyan_mask[:, first_start : first_end + 1]
    local_ys = np.where(np.any(first_mask, axis=1))[0]
    if len(local_ys) == 0:
        return None
    target_x = x1 + (first_start + first_end) // 2
    target_y = lower_y1 + (int(local_ys.min()) + int(local_ys.max())) // 2
    return target_x, target_y, dialog


def _ocr_playback_time_pair(image: Image.Image) -> tuple[int, int] | None:
    width, height = image.size
    crop = image.crop((0, max(0, height - 150), min(width, 520), height)).convert("L")
    tokens = _ocr_time_tokens(crop)
    text = "".join(tokens)
    matches = re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if len(matches) < 2:
        return None
    return _duration_seconds(matches[0]), _duration_seconds(matches[1])


def _ocr_time_tokens(image: Image.Image) -> list[str]:
    arr = np.array(image)
    _, threshold = cv2.threshold(arr, 80, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    digit_boxes: list[tuple[int, int, int, int]] = []
    dot_boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if area > 20 and height >= 8 and width <= 28:
            if width > 18:
                split = width // 2
                digit_boxes.append((x, y, split, height))
                digit_boxes.append((x + split, y, width - split, height))
            else:
                digit_boxes.append((x, y, width, height))
        elif area >= 2 and 2 <= width <= 6 and 2 <= height <= 6:
            dot_boxes.append((x, y, width, height))
    tokens: list[tuple[float, str]] = []
    for x, y, width, height in digit_boxes:
        glyph = threshold[y : y + height, x : x + width]
        digit = _classify_time_digit(glyph)
        if digit is not None:
            tokens.append((x + width / 2, digit))
    used: set[int] = set()
    for index, first in enumerate(dot_boxes):
        if index in used:
            continue
        x1, y1, w1, h1 = first
        for other_index, second in enumerate(dot_boxes[index + 1 :], start=index + 1):
            if other_index in used:
                continue
            x2, y2, w2, h2 = second
            if abs((x1 + w1 / 2) - (x2 + w2 / 2)) <= 3 and 4 <= abs(y2 - y1) <= 12:
                tokens.append(((x1 + x2 + w1 / 2 + w2 / 2) / 2, ":"))
                used.update({index, other_index})
                break
    return [token for _x, token in sorted(tokens, key=lambda item: item[0])]


def _classify_time_digit(glyph: np.ndarray) -> str | None:
    resized = cv2.resize(glyph, (10, 15), interpolation=cv2.INTER_AREA)
    _, resized = cv2.threshold(resized, 80, 255, cv2.THRESH_BINARY)
    best_digit = None
    best_score = -1.0
    scores: dict[str, float] = {}
    for digit, template in _digit_templates().items():
        score = float(np.mean(resized == template))
        scores[digit] = score
        if score > best_score:
            best_digit = digit
            best_score = score
    if best_digit == "6" and scores.get("0", 0.0) >= best_score - 0.04:
        best_digit = "0"
    return best_digit if best_score >= 0.65 else None


def _duration_seconds(value: str) -> int:
    parts = [int(part) for part in str(value or "").split(":") if part != ""]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0


_DIGIT_TEMPLATES: dict[str, np.ndarray] | None = None


def _digit_templates() -> dict[str, np.ndarray]:
    global _DIGIT_TEMPLATES
    if _DIGIT_TEMPLATES is not None:
        return _DIGIT_TEMPLATES
    font_path = _ocr_font_path()
    font = ImageFont.truetype(font_path, 22)
    templates: dict[str, np.ndarray] = {}
    for digit in "0123456789":
        image = Image.new("L", (32, 32), 0)
        draw = ImageDraw.Draw(image)
        draw.text((2, 2), digit, font=font, fill=255)
        arr = np.array(image)
        ys, xs = np.where(arr > 20)
        glyph = arr[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        resized = cv2.resize(glyph, (10, 15), interpolation=cv2.INTER_AREA)
        _, resized = cv2.threshold(resized, 80, 255, cv2.THRESH_BINARY)
        templates[digit] = resized
    _DIGIT_TEMPLATES = templates
    return templates


def _ocr_font_path() -> str:
    candidates = (
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("No local font found for lightweight playback time OCR templates.")


def _media_session_state(text: str) -> str | None:
    block = _movieplayer_media_session_block(text)
    if not block:
        return None
    match = re.search(r"state=([A-Z_]+)\(\d+\)", block)
    return match.group(1) if match else None


def _media_session_matches_file(text: str, file_path: str) -> bool:
    block = _movieplayer_media_session_block(text)
    if not block:
        return False
    file_name = str(file_path or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return bool(file_name) and file_name in block


def playback_state_summary(dut) -> str:
    return _media_session_state_summary(str(_ensure_dut(dut).run_device_shell("dumpsys media_session") or ""))


def _media_session_state_summary(text: str) -> str:
    block = _movieplayer_media_session_block(text)
    package_seen = bool(block)
    state_match = re.search(r"state=([A-Z_]+)\((\d+)\)", block) if block else None
    active_match = re.search(r"active=(true|false)", block) if block else None
    state = f"{state_match.group(1)}({state_match.group(2)})" if state_match else "unknown"
    active = active_match.group(1) if active_match else "unknown"
    return f"pkg={package_seen} active={active} state={state}"


def _movieplayer_media_session_block(text: str) -> str:
    raw = str(text or "")
    marker = f"MoviePlayer {EXOPLAYER_PACKAGE}/MoviePlayer"
    start = raw.find(marker)
    if start < 0:
        return ""
    next_session = re.search(r"\n\s{4}\S.*?\(userId=\d+\)", raw[start + len(marker) :])
    if next_session:
        return raw[start : start + len(marker) + next_session.start()]
    return raw[start:]


def _media_scan_command(media_dir: str | None) -> str:
    directory = str(media_dir or "").strip()
    if not directory or directory == DEFAULT_MEDIA_DIR:
        return DEFAULT_MEDIA_SCAN_COMMAND
    return f"find {_shell_quote(normalize_playback_path(directory.rstrip('/')))} -maxdepth 1 -type f 2>/dev/null"


def normalize_playback_path(value: str) -> str:
    path = str(value or "").strip()
    if path.startswith("/mnt/media_rw/"):
        return "/storage/" + path[len("/mnt/media_rw/") :]
    return path


def _media_dir_from_state(nodeid: str | None) -> str | None:
    normalized_nodeid = str(nodeid or "").strip()
    if not normalized_nodeid:
        return None
    value = smarttest_context().params.get_str(normalized_nodeid, "local_playback_stress:media_dir", "")
    return str(value or "").strip() or None


def _normalize_media_dir(value: Any) -> str:
    directory = str(value or "").strip()
    if directory in ("/mnt/media_rw", "/mnt/media_rw/*/Movies"):
        return DEFAULT_MEDIA_DIR
    return directory or DEFAULT_MEDIA_DIR


def _start_playback_shell_command(file_path: str, component: str) -> str:
    path = normalize_playback_path(str(file_path or "").strip())
    if not path:
        raise ValueError("Playback file path is required.")
    uri = path if urlsplit(path).scheme else "file://" + path
    return (
        "am start -a android.intent.action.VIEW "
        f"-n {component} "
        f"-t {_media_mime_type(path)} "
        f"-d {_shell_quote(uri)}"
    )


def _exoplayer_package(dut) -> str:
    component = getattr(dut, "EXOPLAYER_DEMO_COMPONENT", EXOPLAYER_ACTIVITY)
    package_name = str(component or "").split("/", 1)[0].strip()
    if not package_name:
        raise ValueError("ExoPlayer package name is required.")
    return package_name


def _media_mime_type(file_path: str) -> str:
    normalized = str(file_path or "").strip().lower()
    for suffix, mime_type in MEDIA_MIME_TYPES.items():
        if normalized.endswith(suffix):
            return mime_type
    return "video/*"


def _action_keyevent(action: str) -> str:
    keyevent = DIRECT_ACTION_KEYEVENTS.get(str(action or "").strip())
    if not keyevent:
        raise ValueError(f"Unsupported local playback stress action: {action}")
    return keyevent


def _format_progress(progress: PlaybackProgress | None) -> str:
    if progress is None:
        return "unknown"
    return (
        f"current={progress.current_seconds} duration={progress.duration_seconds} "
        f"remaining={_playback_remaining_seconds(progress)} ratio={progress.ratio:.3f}"
    )


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _ensure_dut(dut):
    if dut is not None:
        return dut
    return _default_dut(None)


def _default_dut(selected_serial: str | None):
    from core.testing.tool.dut_tool.duts.android import android

    return android(serialnumber=str(selected_serial or "").strip())
