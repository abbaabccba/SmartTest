from __future__ import annotations
from dataclasses import dataclass, replace


SELECTED_SOURCE_IDS = (4, 5, 10, 18, 20, 21, 29, 31, 32, 33, 49, 52, 53, 54, 55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 95, 96, 97, 98, 114)

@dataclass(frozen=True)
class Checkpoint:
    definition_id: str
    expected: str
    pass_rule: str


@dataclass(frozen=True)
class MiddleScreenCase:
    source_id: int
    source_rows: tuple[int, ...]
    title: str
    executor: str
    steps: tuple[str, ...]
    checkpoints: tuple[Checkpoint, ...]
    parameters: tuple[str, ...] = ()
    media_sources: tuple[str, ...] = ()
    pre_actions: tuple[str, ...] = ()
    manual_boundaries: tuple[str, ...] = ()
    equipment_boundaries: tuple[str, ...] = ()
    coverage_level: str = "full"
    unverified_items: tuple[str, ...] = ()
    source_file: str = "\u4e2d\u5c4f\u7528\u4f8b\u8bc4\u4f30.xlsx"
    source_sheet: str = "SmartTest\u8986\u76d6\u8bc4\u4f30"

    @property
    def source_row(self):
        return self.source_rows[0]

    @property
    def pytest_id(self):
        return f"source-{self.source_id:03d}-{self.executor.replace('_', '-')}"


def _cp(source_id: int, expected: str) -> tuple[Checkpoint, ...]:
    return (Checkpoint(f"iptv.{source_id:03d}.objective", expected, f"objective evidence: {expected}"),)


AV = ("画质", "音质", "花屏", "卡顿", "丢帧", "解码正确性", "音画同步")

def case_param(source_id: int, name: str) -> str:
    return f"iptv_middle_screen_{source_id:03d}:{name}"

def media_params(source_id: int) -> tuple[str, ...]:
    source_name = "media_files" if source_id in (57, 58, 59, 67, 68, 69, 114) else "media_url"
    result = (case_param(source_id, source_name), case_param(source_id, "playback_timeout_s"))
    return result + ((case_param(source_id, "playback_duration_s"),) if source_id == 114 else ())

FULL_STEPS = (
    "校验参数和测试环境", "采集执行前状态", "调用共享驱动执行操作", "采集原始输出和结构化实际值",
    "执行客观 checkpoint", "恢复被修改的设备状态", "写入结果、证据和自动化边界",
)
MEDIA_STEPS = (
    "解析并记录媒体源", "启动播放器", "在超时时间内等待 PLAYING", "按配置持续采样播放状态",
    "判定所有采样均为 PLAYING", "停止播放器", "写入 software_partial 结果和未判定边界",
)
def _case(source_id, row, title, executor, expected, params=(), sources=(), manual=(), equipment=(), steps=("执行原工作簿用例并记录结果",), coverage="full", unverified=()):
    return MiddleScreenCase(
        source_id,
        (row,),
        title,
        executor,
        steps,
        _cp(source_id, expected),
        params,
        sources,
        manual_boundaries=manual,
        equipment_boundaries=equipment,
        coverage_level=coverage,
        unverified_items=unverified,
    )

MIDDLE_SCREEN_CASES=(
 _case(4,5,"USB storage recognition","usb_storage","USB storage recognized",(case_param(4,"usb_match"),),equipment=("Attach workbook U-disk/HDD.",)),
 _case(5,6,"HDMI connection/output evidence","hdmi_objective","connector connected and output mode active",(case_param(5,"hdmi_state_command"),),manual=("TV picture and black-border judgement remain manual.",),equipment=("Connect TV/HDMI sink.",)),
 _case(10,11,"Wired Ethernet IP and speed","ethernet_speed","IP exists and speed matches configured link",(case_param(10,"interface"),case_param(10,"expected_speed_mbps")),equipment=("Connect requested 100/1000 Mbps link.",)),
 _case(18,19,"CPU frequency points work and lock","cpu_frequency","every selected point locks and original is restored",(case_param(18,"frequencies"),)),
 _case(20,21,"eMMC HS400 mode","emmc_hs400","MMC evidence contains HS400"),
 _case(21,22,"Wi-Fi driver scan connect and IP","wifi","driver/interface, scan, connection and IP evidence",tuple(case_param(21,n) for n in ("wifi_2g_ssid","wifi_2g_password","wifi_5g_ssid","wifi_5g_password")),equipment=("Configured access point required.",)),
 _case(29,30,"CPU thermal node","thermal","numeric CPU temperature"),
 _case(31,32,"USB ADB connectivity","adb_transport","USB ADB ready",equipment=("Select USB ADB serial.",)),
 _case(32,33,"Network ADB connectivity","adb_transport","network ADB ready",equipment=("Select network ADB serial.",)),
 _case(33,34,"UI mode up to 1080p","wm_size","effective UI dimensions at least 1920x1080"),
 _case(49,50,"DHCP dual-stack","network","IPv4/global IPv6 and reachability",tuple(case_param(49,n) for n in ("interface","ipv4_ping_target","ipv6_ping_target")),equipment=("Dual-stack network required.",)),
 _case(52,53,"Wired IPv4","network","IPv4 and reachability",tuple(case_param(52,n) for n in ("interface","ipv4_ping_target"))),
 _case(53,54,"Wired IPv6","network","global IPv6 and reachability",tuple(case_param(53,n) for n in ("interface","ipv6_ping_target"))),
 _case(54,55,"Wireless IPv4","network","IPv4 and reachability",tuple(case_param(54,n) for n in ("interface","ipv4_ping_target"))),
 _case(55,56,"Wireless IPv6","network","global IPv6 and reachability",tuple(case_param(55,n) for n in ("interface","ipv6_ping_target"))),
 _case(57,58,"H.264 decode","media","both sources reach PLAYING",(),("[4K123]Hercules The Thracian Wars.mp4","\u534e\u4e3a-\u9891\u905311-\u4e1c\u65b9\u536b\u89c6HD.ts"),AV),
 _case(58,59,"H.265 decode","media","both sources reach PLAYING",(),("[4KH265_21.1Mbps_59.940fps_10bit]worldcup2014_10bit_19m_60p.ts","[H.265_4K]_4K-HD.Club-2013-Taipei 101 Fireworks Trailer.mp4"),AV),
 _case(59,60,"AVS2 decode","media","both sources reach PLAYING",(),("case2.ts","case5.ts"),AV),
 _case(60,61,"H.264 4K60","media","source reaches PLAYING",(),("http://qa-sz.amlogic.com:8881/chfs/shared/Streams/Test_Files/REF_QA/4K_Video/4K-H264/60fps_59.94fps/4K_H264_SDR_Main@L5.2_3840x2160_100Mbps_59.94fps_8bit_AAC_2ch.ts",),AV),
 _case(61,62,"H.265 4K120","media","source reaches PLAYING",(),("http://qa-sz.amlogic.com:8881/chfs/shared/Streams/Test_Files/REF_QA/4K_Video/4K-H265-10bit/120FPS/4K_120fps_H265_Main_10@L6.1@High_3840x1608_80Mbps_5min_AAC2ch.ts",),AV),
 _case(62,63,"AV1 4K120","media","source reaches PLAYING",(),("http://qa-sz.amlogic.com:8881/chfs/shared/Streams/Test_Files/REF_QA/AV1/download_from_youtube/4K/120fps/4K_120fps_AV1_Main@L6.1_3840x1608_81Mbps_OGG2ch.mp4",),AV),
 _case(63,64,"VP9 4K120","media","source reaches PLAYING",(),("http://qa-sz.amlogic.com:8881/chfs/shared/Streams/Test_Files/REF_QA/4K_Video/4K-VP9/120fps/4K_120fps_VP9_3840x1608_90Mbps_2min_Vorbis2ch.webm",),AV),
 _case(64,65,"AVS2 4K120","media","source reaches PLAYING",(),("http://qa-sz.amlogic.com:8881/chfs/shared/Streams/Test_Files/REF_QA/4K_Video/4K-AVS2/120fps/4K_120fps_AVS2_3840x1608_100Mbps_AAC2ch.ts",),("Workbook notes source artifacts; assert playback state only.",)),
 _case(65,66,"AVS3 4K50","media","source reaches PLAYING",(),("http://qa-sz.amlogic.com:8881/chfs/shared/Test_File/Basicfunction_testing/IPTV-China-operator/CUVA-HDR/AVS3.0/%E9%A3%8E%E5%91%B3%E4%BA%BA%E9%97%B4-AVS3/01_01_HDR_FW-AVS3-Vivid.ts",),AV),
 _case(66,67,"MJPEG 4K30","media","source reaches PLAYING",(),("https://eng-sw-shared.amlogic.com/chfs/shared/ENG_QA/Test_File/Video%26Audio_release_test_files/006-avi/3840x2160_mjpeg.avi?v=1",),AV),
 _case(67,68,"JPEG GIF BMP PNG images","image","Configured images exist; selected viewers launch with matching resumed activities, show for at least 5 seconds, and exit",(case_param(67,"media_files"),case_param(67,"image_viewers")),manual=("Actual image rendering and fidelity remain manual; launch and activity checks only.",),equipment=("Configure workbook-format image paths; selected viewers must be installed; selecting Jarvis requires permission to launch its non-exported activity.",)),
 _case(68,69,"SBS/TAB 3D video","media","configured sources reach PLAYING",media_params(68),manual=("3D switching and visual/A-V quality remain manual.",),equipment=("Workbook gives no exact source; configure SBS/TAB paths.",)),
 _case(69,70,"VR video","media","configured source reaches PLAYING",media_params(69),manual=("VR interaction and visual/A-V quality remain manual.",),equipment=("Workbook gives no exact source; configure it.",)),
 _case(95,96,"HTTP playback","media","configured HTTP stream reaches PLAYING",(),manual=AV,equipment=("Configure PC HTTP URL.",)),
 _case(96,97,"HLS playback","media","configured HLS stream reaches PLAYING",(),manual=AV,equipment=("Configure PC HLS URL.",)),
 _case(97,98,"UDP playback","media","configured UDP stream reaches PLAYING",(),manual=AV,equipment=("Configure PC UDP URL.",)),
 _case(98,99,"RTSP playback","media","configured RTSP stream reaches PLAYING",(),manual=AV,equipment=("Configure PC RTSP URL.",)),
 _case(114,115,"4K local playback 24H","media","playback remains PLAYING for configured duration",media_params(114),manual=AV,equipment=("Workbook gives no exact 4K file; configure it.",)),
)
TARGET_MEDIA_IDS = frozenset((57,58,59,60,61,62,63,64,65,66,95,96,97,98,114))
MIDDLE_SCREEN_CASES = tuple(
    replace(
        case,
        parameters=media_params(case.source_id),
        steps=MEDIA_STEPS,
        coverage_level="software_partial",
        unverified_items=AV,
        manual_boundaries=("本结果仅代表软件播放状态检查通过，不代表原始用例完整通过。",),
    ) if case.source_id in TARGET_MEDIA_IDS else (
        replace(case, steps=FULL_STEPS) if case.source_id in {4,10,18,20,21,29,31,32,33,49,52,53,54,55} else case
    )
    for case in MIDDLE_SCREEN_CASES
)
assert tuple(case.source_id for case in MIDDLE_SCREEN_CASES) == SELECTED_SOURCE_IDS


def case_by_source_id(source_id: int) -> MiddleScreenCase:
    return next(case for case in MIDDLE_SCREEN_CASES if case.source_id == source_id)
