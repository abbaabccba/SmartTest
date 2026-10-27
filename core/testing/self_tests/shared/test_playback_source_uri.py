import shlex
from unittest.mock import Mock

import pytest

from core.testing.tool.dut_tool.features.local_playback import assert_media_session_state, start_playback


@pytest.mark.parametrize(
    ("source", "expected_uri"),
    [
        ("http://192.168.8.221/video.mp4", "http://192.168.8.221/video.mp4"),
        ("https://example.com/video.mp4", "https://example.com/video.mp4"),
        ("igmp://236.1.3.1:8001", "igmp://236.1.3.1:8001"),
        ("rtsp://192.168.8.221/live", "rtsp://192.168.8.221/live"),
        ("rtp://236.1.3.1:8001", "rtp://236.1.3.1:8001"),
        ("udp://@236.1.3.1:8001", "udp://@236.1.3.1:8001"),
        ("custom+stream://host/live", "custom+stream://host/live"),
        ("RTSP://host/live?token=a%2Fb&name=demo", "RTSP://host/live?token=a%2Fb&name=demo"),
        ("file:///sdcard/Movies/video.mp4", "file:///sdcard/Movies/video.mp4"),
        ("/storage/usb/Movies/测试 video.mp4", "file:///storage/usb/Movies/测试 video.mp4"),
    ],
)
def test_start_playback_passes_source_uri_to_android(source, expected_uri):
    dut = Mock(EXOPLAYER_DEMO_COMPONENT="player/.MoviePlayer")

    start_playback(source, dut)

    command = shlex.split(dut.run_device_shell.call_args.args[0])
    assert command[command.index("-d") + 1] == expected_uri


@pytest.mark.parametrize("scheme", ["igmp", "rtp", "udp"])
@pytest.mark.parametrize("state", ["PLAYING", "PAUSED"])
def test_stream_state_check_without_source_metadata(scheme, state):
    dut = Mock()
    dut.run_device_shell.return_value = (
        "MoviePlayer com.droidlogic.exoplayer2.demo/MoviePlayer (userId=0)\n"
        f"  state=PlaybackState {{state={state}(3)}}\n"
        "  metadata: size=2, description=null, null, null\n"
    )
    if state == "PLAYING":
        assert_media_session_state(dut, file_path=f"{scheme}://236.1.3.1:8001", expected_state="PLAYING", timeout_s=0)
    else:
        with pytest.raises(AssertionError):
            assert_media_session_state(dut, file_path=f"{scheme}://236.1.3.1:8001", expected_state="PLAYING", timeout_s=0)


@pytest.mark.parametrize("source", ["/sdcard/video.mp4", "http://host/video.mp4", "rtsp://host/video.mp4"])
def test_other_sources_still_require_matching_metadata(source):
    dut = Mock()
    dut.run_device_shell.return_value = (
        "MoviePlayer com.droidlogic.exoplayer2.demo/MoviePlayer (userId=0)\n"
        "  state=PlaybackState {state=PLAYING(3)}\n"
        "  metadata: description=other.mp4\n"
    )
    with pytest.raises(AssertionError):
        assert_media_session_state(dut, file_path=source, expected_state="PLAYING", timeout_s=0)
