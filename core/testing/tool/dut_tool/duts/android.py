import os
import re
from core.testing.tool.dut_tool import command_batch as subprocess
import threading
import time
from collections import Counter
from typing import Optional
from xml.dom import minidom
from pathlib import Path

import _io
import pytest

from core.testing.tool.dut_tool.duts.base import BaseDut
from core.testing.tool.dut_tool.features.android_ui import (
    UiautomatorTool,
    close_hotspot as android_ui_close_hotspot,
    enter_hotspot as android_ui_enter_hotspot,
    enter_wifi_activity as android_ui_enter_wifi_activity,
    factory_reset_ui as android_ui_factory_reset,
    kill_moresetting as android_ui_kill_moresetting,
    kill_setting as android_ui_kill_setting,
    open_hotspot as android_ui_open_hotspot,
    wait_for_launcher as android_ui_wait_for_launcher,
)
from core.testing.tool.dut_tool.features import logcat as logcat_feature
from core.testing.tool.dut_tool.features import screen as screen_feature
from core.testing.tool.dut_tool.features import system as system_feature
from core.testing.tool.dut_tool.features.wifi import (
    WifiConnectParams,
    check_driver as wifi_check_driver,
    connect as wifi_connect,
    connect_command as wifi_connect_command,
    forget as wifi_forget,
    get_mcs_rx as wifi_get_mcs_rx,
    get_mcs_tx as wifi_get_mcs_tx,
    get_tx_bitrate as wifi_get_tx_bitrate,
    parse_mcs_distribution_from_blocks as wifi_parse_mcs_distribution_from_blocks,
    scan as wifi_scan,
    wait_for_service as wifi_wait_for_service,
)
from core.logging import smart_log


def connect_again(func):
    """
    Connect again.

    -------------------------
    It executes external commands via Python's subprocess module.

    -------------------------
    Parameters
    -------------------------
    func : Any
        The ``func`` parameter.

    -------------------------
    Returns
    -------------------------
    Any
        The result produced by the function.
    """

    def inner(self, *args, **kwargs):
        """
        Inner.

        -------------------------
        It executes external commands via Python's subprocess module.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        if ':5555' in self.serialnumber:
            self.command_runner.run(f'adb connect {self.serialnumber}', shell=True)
        self.wait_devices()
        result = func(self, *args, **kwargs)
        if ':5555' in self.serialnumber:
            text = f"{getattr(result, 'stdout', '') or ''}\n{getattr(result, 'stderr', '') or ''}".lower()
            if (
                "device offline" in text
                or ("offline" in text and "error:" in text)
                or ("device" in text and "not found" in text)
                or "no devices/emulators found" in text
            ):
                smart_log(
                    f"adb offline detected; reconnect serial={self.serialnumber}",
                    level="warning",
                    domain="dut",
                    source="android",
                )
                self.command_runner.run(f'adb disconnect {self.serialnumber}', shell=True)
                self.command_runner.run(f'adb connect {self.serialnumber}', shell=True)
                self.wait_devices()
                return func(self, *args, **kwargs)
        return result

    return inner


class android(BaseDut):
    """
    ADB.

    -------------------------
    It runs shell commands on the target device using ADB helpers and captures the output.
    It executes external commands via Python's subprocess module.
    It logs information for debugging or monitoring purposes.
    It ensures the device has root privileges when required.
    It remounts the device's file system with write permissions.
    It sends key events to the device using ADB.
    It simulates user input on the device's screen (tap, swipe, or text entry).
    It introduces delays to allow the device to process commands.

    -------------------------
    Returns
    -------------------------
    None
        This class does not return a value.
    """

    ADB_S = 'adb -s '
    DUMP_FILE = '\\view.xml'
    OSD_VIDEO_LAYER = 'osd+video'

    def __init__(self, serialnumber="", logdir="", *, prepare=True):
        """
        Init.

        -------------------------
        It ensures the device has root privileges when required.
        It remounts the device's file system with write permissions.

        -------------------------
        Parameters
        -------------------------
        serialnumber : Any
            The ADB serial number identifying the target device.
        logdir : Any
            Path to the directory where logs will be stored.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        super().__init__()
        self.serialnumber = serialnumber
        self.logdir = logdir or os.path.join(os.getcwd(), 'results')
        self.timer = None
        self.live = False
        self.lock = threading.Lock()
        self.p_config_wifi = ''
        if self.serialnumber and prepare:
            self.root()
            self.remount()

    def _wifi_connect_impl(self, params: WifiConnectParams) -> bool:
        return wifi_connect(self, params)

    def _wifi_scan_impl(self, ssid: str, *, attempts: int, scan_wait: int, interval: float) -> bool:
        return wifi_scan(self, ssid, attempts=attempts, scan_wait=scan_wait, interval=interval)

    def _wifi_forget_impl(self):
        return wifi_forget(self)

    def push_iperf(self):
        if self.checkoutput('[ -e /system/bin/iperf ] && echo yes || echo no').strip() != 'yes':
            path = os.path.join(os.getcwd(), 'res/iperf')
            self.push(path, '/system/bin')
            self.checkoutput('chmod a+x /system/bin/iperf')
        return None

    def _run_iperf_server_on_device(self, command: str, *, start_background, extend_logs, encoding: str):
        cmd_parts = command.split()
        cmd_list = ["adb", "-s", self.serialnumber, "shell", *cmd_parts]
        return start_background(cmd_list, "server adb command:")

    def _run_iperf_client_on_device(self, command: str, *, run_blocking, encoding: str):
        cmd_parts = command.split()
        cmd_list = ["adb", "-s", self.serialnumber, "shell", *cmd_parts]
        run_blocking(cmd_list, "client adb command:")
        return None

    def _iperf_client_post_delay_seconds(self) -> int:
        return 0
    def set_status_on(self):
        """
        Set status on.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        with self.lock:
            if self.live:
                return
            self.live = True
            smart_log('Adb status is on', level="debug")

    def set_status_off(self):
        """
        Set status off.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        with self.lock:
            if not self.live:
                return
            self.live = False
            smart_log('Adb status is Off', level="debug")

    def _u_impl(self, *, type="u2"):
        """
        U.

        -------------------------
        Parameters
        -------------------------
        type : Any
            Type specifier for the UI automation tool (e.g., "u2").

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        self._u = UiautomatorTool(self.serialnumber, type)
        return self._u

    def _keyevent_impl(self, keycode):
        """
        Keyevent.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It sends key events to the device using ADB.
        It introduces delays to allow the device to process commands.

        -------------------------
        Parameters
        -------------------------
        keycode : Any
            Key code representing the button to press.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        if isinstance(keycode, int):
            keycode = str(keycode)
        self.adb_call("shell", "input", "keyevent", keycode)
        time.sleep(0.5)

    def send_event(self, key, hold=3):
        """
        Send event.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It introduces delays to allow the device to process commands.

        -------------------------
        Parameters
        -------------------------
        key : Any
            Key identifier for sending input events.
        hold : Any
            Time in seconds to hold the key pressed.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.checkoutput(
            f'sendevent /dev/input/event5 4 4 786501;sendevent /dev/input/event5 1 {key} 1;sendevent  /dev/input/event5 0 0 0;')
        time.sleep(hold)
        self.checkoutput(
            f'sendevent /dev/input/event5 4 4 786501;sendevent /dev/input/event5 1 {key} 0;sendevent  /dev/input/event5 0 0 0;')

    def _home_impl(self):
        """
        Home.

        -------------------------
        It sends key events to the device using ADB.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.keyevent("KEYCODE_HOME")

    def enter(self):
        """
        Enter.

        -------------------------
        It sends key events to the device using ADB.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.keyevent("KEYCODE_ENTER")

    def root(self):
        """
        Root.

        -------------------------
        It executes external commands via Python's subprocess module.
        It ensures the device has root privileges when required.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("root")

    def remount(self):
        """
        Remount.

        -------------------------
        It executes external commands via Python's subprocess module.
        It remounts the device's file system with write permissions.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("remount")

    def _reboot_impl(self):
        """
        Reboot.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("reboot")
        self.wait_devices()

    def _back_impl(self):
        """
        Back.

        -------------------------
        It sends key events to the device using ADB.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.keyevent("KEYCODE_BACK")

    def _app_switch_impl(self):
        """
        App switch.

        -------------------------
        It sends key events to the device using ADB.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.keyevent("KEYCODE_APP_SWITCH")

    def _app_stop_impl(self, app_name):
        """
        App stop.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        app_name : Any
            Name of the application package.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        smart_log("Stop app(%s)" % app_name, level="info")
        self.force_stop(app_name)

    def clear_app_data(self, app_name):
        return system_feature.clear_app_data(self, app_name)
    def _expand_logcat_capacity_impl(self):
        logcat_feature.expand_capacity(self)
    def delete(self, times=1):
        """
        Delete.

        -------------------------
        It sends key events to the device using ADB.

        -------------------------
        Parameters
        -------------------------
        times : Any
            Number of repetitions for the action.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        remain = times
        batch = 64
        while remain > 0:
            self.keyevent("67 " * batch)
            remain -= batch

    def _tap_impl(self, x, y):
        """
        Tap.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It simulates user input on the device's screen (tap, swipe, or text entry).

        -------------------------
        Parameters
        -------------------------
        x : Any
            Horizontal coordinate on the device screen.
        y : Any
            Vertical coordinate on the device screen.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("shell", "input", "tap", x, y)

    def _swipe_impl(self, x_start, y_start, x_end, y_end, duration):
        """
        Swipe.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It simulates user input on the device's screen (tap, swipe, or text entry).

        -------------------------
        Parameters
        -------------------------
        x_start : Any
            Starting horizontal coordinate for a swipe gesture.
        y_start : Any
            Starting vertical coordinate for a swipe gesture.
        x_end : Any
            Ending horizontal coordinate for a swipe gesture.
        y_end : Any
            Ending vertical coordinate for a swipe gesture.
        duration : Any
            Duration of the swipe gesture in milliseconds.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("shell", "input", "swipe", x_start, y_start, x_end, y_end, duration)

    def _text_impl(self, text):
        """
        Text.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It simulates user input on the device's screen (tap, swipe, or text entry).

        -------------------------
        Parameters
        -------------------------
        text : Any
            Text to input into the device.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        if isinstance(text, int):
            text = str(text)
        self.adb_call("shell", "input", "text", text)

    def _clear_logcat_impl(self):
        logcat_feature.clear(self)
    def _save_logcat_impl(self, filepath, *, tag=''):
        return logcat_feature.save(self, filepath, tag=tag)
    def _stop_save_logcat_impl(self, log, filepath):
        logcat_feature.stop_save(self, log, filepath)
    def _filter_logcat_pid_impl(self):
        return logcat_feature.filter_pid(self)
    def _start_activity_impl(self, packageName, activityName, *, intentname=""):
        """
        Start activity.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        packageName : Any
            The ``packageName`` parameter.
        activityName : Any
            The ``activityName`` parameter.
        intentname : Any
            The ``intentname`` parameter.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        try:
            self.app_stop(packageName)
        except Exception as e:
            ...
        self.adb_call("shell", "am", "start", "-a", intentname, "-n", packageName + "/" + activityName)

    def pull(self, filepath, destination):
        """
        Pull.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Parameters
        -------------------------
        filepath : Any
            Path of the file on the host machine where data should be saved.
        destination : Any
            The ``destination`` parameter.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("pull", filepath, destination)

    def push(self, filepath, destination):
        """
        Push.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        filepath : Any
            Path of the file on the host machine where data should be saved.
        destination : Any
            The ``destination`` parameter.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("push", filepath, destination)

    def shell(self, cmd):
        """
        Shell.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Parameters
        -------------------------
        cmd : Any
            Command string to parse or execute.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("shell", cmd)

    def check_apk_exist(self, package_name):
        return system_feature.package_exists(self, package_name)
    def install_apk(self, apk_path):
        return system_feature.install_apk(self, apk_path)
    def uninstall_apk(self, apk_name):
        return system_feature.uninstall_apk(self, apk_name)
    def get_time(self, time=None):
        """
        Retrieve time.

        -------------------------
        Parameters
        -------------------------
        time : Any
            The ``time`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        if (":" not in time[6:8]) and (":" not in time[9:11]) and (":" not in time[12:14]) and (
                ":" not in time[15:18]) and ("." not in time[15:18]):
            th = int(time[6:8])
            tm = int(time[9:11])
            ts = int(time[12:14])
            tms = int()
            if "-" not in time[15:18]:
                tms = int(time[15:18])
            return (tms + ts * 1000 + tm * 60 * 1000 + th * 3600 * 1000) / 1000

    def getprop(self, key):
        return system_feature.getprop(self, key)
    def rm(self, flags, path):
        """
        Rm.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Parameters
        -------------------------
        flags : Any
            The ``flags`` parameter.
        path : Any
            The ``path`` parameter.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("shell", "rm", flags, path)

    def _uiautomator_dump_impl(self, *, filepath='', uiautomator_type='adb'):
        """
        Uiautomator dump.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        filepath : Any
            Path of the file on the host machine where data should be saved.
        uiautomator_type : Any
            The ``uiautomator_type`` parameter.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        filepath = self._resolve_ui_dump_path(filepath)
        smart_log(f'doing uiautomator dump type={uiautomator_type}', level="debug")
        if uiautomator_type == 'u2':
            xml = self.u().d2.dump_hierarchy()
            with open(filepath, 'w+', encoding='utf-8') as f:
                f.write(xml)
        else:
            self._adb_uiautomator_dump(filepath)
        smart_log(f'uiautomator dump done: {filepath}', level="debug")

    def _resolve_ui_dump_path(self, filepath: str = '') -> str:
        path = str(filepath or self.logdir)
        if not path.endswith('view.xml'):
            path += self.DUMP_FILE
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        return path

    def _adb_uiautomator_dump(self, filepath: str) -> None:
        remote_path = "/sdcard/window_dump.xml"
        self.adb_call("shell", "uiautomator", "dump", remote_path, timeout=30)
        result = self.adb_call("pull", remote_path, filepath, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"uiautomator dump pull failed: {result.stderr}")

    def get_dump_info(self):
        """
        Retrieve dump info.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        path = self.logdir + self.DUMP_FILE if os.path.exists(
            self.logdir + self.DUMP_FILE) else self.logdir + '/view.xml'
        with open(path, 'r', encoding='utf-8') as f:
            temp = f.read()
        return temp

    def expand_notifications(self):
        """
        Expand notifications.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        self.adb_call("shell", "cmd", "statusbar", "expand-notifications")

    def _screencap(self, filepath, layer="osd", app_level=28):
        screen_feature.screencap(self, filepath, layer=layer, app_level=app_level)
    def screenshot(self, destination, layer="osd", app_level=28):
        screen_feature.screenshot(self, destination, layer=layer, app_level=app_level)
    def continuous_screenshot(self, destination, layer="osd+video", app_level=30, screenshot_counter=3):
        screen_feature.continuous_screenshot(self, destination, layer=layer, app_level=app_level, screenshot_counter=screenshot_counter)
    def screencatch(self, layer="osd+video", counter=1):
        screen_feature.screencatch(self, layer=layer, counter=counter)
    def video_record(self, destination, app_level=28, record_time=30, sleep_time=30,
                     frame=30, bits=4000000, type=1):
        screen_feature.video_record(self, destination, app_level=app_level, record_time=record_time, sleep_time=sleep_time, frame=frame, bits=bits, type=type)
    def mkdir_temp(self):
        """
        Mkdir temp.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.
        It ensures the device has root privileges when required.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        self.root()
        dirs = '/data/temp'
        temp = self.checkoutput("ls /data")
        if "temp" not in temp:
            self.checkoutput("mkdir " + dirs)
        self.checkoutput("chmod 777 " + dirs)
        return dirs

    def check_adb_status(self, waitTime=100):
        """
        Check ADB status.

        -------------------------
        It executes external commands via Python's subprocess module.
        It logs information for debugging or monitoring purposes.
        It introduces delays to allow the device to process commands.

        -------------------------
        Parameters
        -------------------------
        waitTime : Any
            The ``waitTime`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        i = 0
        waitCnt = waitTime / 5
        while i < waitCnt:
            command = "adb devices"
            cmd = command.split()
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            adb_devices = proc.communicate()[0]
            rc = proc.returncode
            if rc == 0 and self.serialnumber in adb_devices and \
                    len(self.serialnumber) != 0:
                return True
            i = i + 1
            time.sleep(5)
            smart_log("Still waiting..", level="debug")
        return False

    def wait_and_tap(self, searchKey, attribute, times=5):
        """
        Wait for and tap.

        -------------------------
        It introduces delays to allow the device to process commands.

        -------------------------
        Parameters
        -------------------------
        searchKey : Any
            The ``searchKey`` parameter.
        attribute : Any
            The ``attribute`` parameter.
        times : Any
            Number of repetitions for the action.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        for _ in range(times):
            if self.find_element(searchKey, attribute):
                self.find_and_tap(searchKey, attribute)
                return 1
            time.sleep(1)

    def wait_element(self, searchKey, attribute):
        """
        Wait for element.

        -------------------------
        It introduces delays to allow the device to process commands.

        -------------------------
        Parameters
        -------------------------
        searchKey : Any
            The ``searchKey`` parameter.
        attribute : Any
            The ``attribute`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        for _ in range(5):
            if self.find_element(searchKey, attribute):
                return 1
            time.sleep(1)

    def find_element(self, searchKey, attribute, extractKey=None):
        """
        Find element.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        searchKey : Any
            The ``searchKey`` parameter.
        attribute : Any
            The ``attribute`` parameter.
        extractKey : Any
            The ``extractKey`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        smart_log(f'find {searchKey}', level="info")
        for item in self._ui_dump_nodes():
            value = self._node_attr(item, attribute)
            if searchKey == value:
                result = value if extractKey is None else self._node_attr(item, extractKey)
                smart_log(result, level="info")
                return result
        return None

    def find_pos(self, searchKey, attribute):
        """
        Find pos.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        searchKey : Any
            The ``searchKey`` parameter.
        attribute : Any
            The ``attribute`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        smart_log('find_pos', level="info")
        for item in self._ui_dump_nodes():
            value = self._node_attr(item, attribute)
            smart_log(f'try to find {searchKey} - {value}', level="debug")
            if searchKey == value:
                position = self._node_center(item)
                if position is not None:
                    smart_log(f'{position[0]} {position[1]}', level="info")
                    return position
                break
        else:
            smart_log("attr: %s not found" % attribute, level="error")
        return -1, -1

    def find_and_tap(self, searchKey, attribute):
        """
        Find and tap.

        -------------------------
        It logs information for debugging or monitoring purposes.
        It simulates user input on the device's screen (tap, swipe, or text entry).
        It introduces delays to allow the device to process commands.

        -------------------------
        Parameters
        -------------------------
        searchKey : Any
            The ``searchKey`` parameter.
        attribute : Any
            The ``attribute`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        smart_log(f'find_and_tap {searchKey}', level="info")
        x_midpoint, y_midpoint = self.find_pos(searchKey, attribute)
        if (x_midpoint, y_midpoint) != (-1, -1):
            self.tap(x_midpoint, y_midpoint)
        return x_midpoint, y_midpoint

    def text_entry(self, text, searchKey, attribute, delete=64):
        """
        Text entry.

        -------------------------
        It sends key events to the device using ADB.
        It simulates user input on the device's screen (tap, swipe, or text entry).

        -------------------------
        Parameters
        -------------------------
        text : Any
            Text to input into the device.
        searchKey : Any
            The ``searchKey`` parameter.
        attribute : Any
            The ``attribute`` parameter.
        delete : Any
            The ``delete`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        position = None
        for item in self._ui_dump_nodes():
            if searchKey.upper() in self._node_attr(item, attribute).upper():
                if "EditText" in self._node_attr(item, 'class'):
                    position = self._node_center(item)
                    break
        if position is None:
            return None
        x_midpoint, y_midpoint = position

        self.tap(x_midpoint, y_midpoint)

        self.keyevent("KEYCODE_MOVE_END")
        self.delete(delete)

        self.text(text)

        self.keyevent("KEYCODE_ENTER")
        return x_midpoint, y_midpoint

    def _ui_dump_nodes(self):
        filepath = self._resolve_ui_dump_path('')
        self.uiautomator_dump(filepath)
        return minidom.parse(filepath).getElementsByTagName('node')

    @staticmethod
    def _node_attr(node, attribute: str) -> str:
        if not node.hasAttribute(attribute):
            return ""
        return node.getAttribute(attribute)

    @classmethod
    def _node_center(cls, node):
        bounds = re.findall(r'\[(\d+)\,(\d+)\]', cls._node_attr(node, 'bounds'))
        if len(bounds) != 2:
            return None
        x_start, y_start = bounds[0]
        x_end, y_end = bounds[1]
        return (int(x_start) + int(x_end)) / 2, (int(y_start) + int(y_end)) / 2

    @classmethod
    def wait_power(cls):
        """
        Wait for power.

        -------------------------
        It executes external commands via Python's subprocess module.
        It introduces delays to allow the device to process commands.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        for i in range(10):
            info = subprocess.check_output("adb devices", shell=True, encoding='utf-8')
            devices = re.findall(r'\n(.*?)\s+device', info, re.S)
            if devices:
                break
            time.sleep(10)
        else:
            assert False, "Can't find any device"

    def wait_devices(self):
        """
        Wait for devices.

        -------------------------
        It executes external commands via Python's subprocess module.
        It logs information for debugging or monitoring purposes.
        It introduces delays to allow the device to process commands.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
    def _kill_logcat_pid_impl(self):
        """
        Kill logcat pid.

        -------------------------
        It executes external commands via Python's subprocess module.

        -------------------------
        Returns
        -------------------------
        None
            This method does not return a value.
        """
        logcat_feature.kill_pid(self)

    def popen(self, command):
        """
        Popen.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        command : Any
            The ``command`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        cmd = self.adb_command(command)
        return self.command_runner.popen(cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def checkoutput(self, command):
        """
        Checkoutput.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Parameters
        -------------------------
        command : Any
            The ``command`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        command = 'shell ' + f'"{command}"'
        return self.checkoutput_shell(command)

    @connect_again
    def checkoutput_shell(self, command):
        """
        Checkoutput shell.

        -------------------------
        It runs shell commands on the target device using ADB helpers and captures the output.

        -------------------------
        Parameters
        -------------------------
        command : Any
            The ``command`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        command = self.adb_command(command)
        return self.checkoutput_term(command)

    @connect_again
    def adb_call(self, *parts: object, timeout: float | None = None):
        command = self.adb_command_prefix().split()
        command.extend(str(part) for part in parts if str(part).strip())
        result = self.command_runner.run(command, timeout=timeout)
        self._remember_command_result(result)
        return result

    def _remember_command_result(self, result) -> None:
        self._last_command_stdout = result.stdout or ""
        self._last_command_stderr = result.stderr or ""
        self._last_command_returncode = result.returncode

    def force_stop(self, package_name: str) -> None:
        self.adb_call("shell", "am", "force-stop", package_name, timeout=30)

    def start_intent(self, action: str, *, category: str | None = None) -> None:
        parts: list[object] = ["shell", "am", "start", "-a", action]
        if category:
            parts.extend(["-c", category])
        self.adb_call(*parts, timeout=30)

    def dumpsys(self, service: str, *args: object) -> str:
        result = self.adb_call("shell", "dumpsys", service, *args, timeout=30)
        return result.stdout or ""

    def current_focus(self) -> str:
        window = self.dumpsys("window")
        lines = []
        for line in window.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                lines.append(line.strip())
        return "\n".join(lines)

    def wm_size(self) -> str:
        return self.checkoutput("wm size").strip()

    def wm_density(self) -> str:
        return self.checkoutput("wm density").strip()

    def check_wifi_driver(self):
        return wifi_check_driver(self)
    def get_mcs_rx(self):
        return wifi_get_mcs_rx(self)
    def get_mcs_tx(self):
        return wifi_get_mcs_tx(self)
    def parse_mcs_distribution_from_blocks(self, blocks):
        return wifi_parse_mcs_distribution_from_blocks(blocks)
    def get_tx_bitrate(self):
        return wifi_get_tx_bitrate(self)
    def wait_for_wifi_service(self, type='wlan0', recv='Link encap') -> None:
        return wifi_wait_for_service(self, interface=type, recv=recv)
    def wait_for_launcher(self) -> None:
        android_ui_wait_for_launcher(self)
    def enter_wifi_activity(self) -> None:
        android_ui_enter_wifi_activity(self)
    def enter_hotspot(self) -> None:
        android_ui_enter_hotspot(self)
    def open_hotspot(self) -> None:
        android_ui_open_hotspot(self)
    def close_hotspot(self) -> None:
        android_ui_close_hotspot(self)
    def kill_setting(self) -> None:
        android_ui_kill_setting(self)
    def kill_moresetting(self) -> None:
        android_ui_kill_moresetting(self)
    def factory_reset_ui(self):
        android_ui_factory_reset(self)
    def get_wifi_cmd(self, router_info):
        """
        Retrieve Wi閳ユ厭i cmd.

        -------------------------
        It logs information for debugging or monitoring purposes.

        -------------------------
        Parameters
        -------------------------
        router_info : Any
            The ``router_info`` parameter.

        -------------------------
        Returns
        -------------------------
        Any
            The result produced by the function.
        """
        type = 'wpa3' if 'WPA3' in router_info.security_mode else 'wpa2'
        # Treat several synonyms for unencrypted networks; Chinese labels removed
        unencrypted_labels = ['open', 'unencrypted', 'none', 'open system',
                              'unencrypted (allow all connections)']
        if router_info.security_mode.lower() in unencrypted_labels:
            security = "open"
            password = ""
        else:
            security = type
            password = router_info.password
        hidden = router_info.hide_ssid in ('yes', 'true', True)
        cmd = wifi_connect_command(router_info.ssid, password, security, hidden)
        smart_log(f'conn wifi cmd :{cmd}', level="info")
        return cmd

