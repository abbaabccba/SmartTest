from unittest.mock import Mock

import pytest

from core.testing.tool.dut_tool.duts.android import android


@pytest.mark.parametrize("serial", ["USB123456", "192.0.2.10:5555"])
def test_adb_status_accepts_text_output_for_usb_and_network(serial, monkeypatch):
    process = Mock(returncode=0)
    process.communicate.return_value = (f"List of devices attached\n{serial}\tdevice\n", "")
    monkeypatch.setattr("core.testing.tool.dut_tool.duts.android.subprocess.Popen", lambda *args, **kwargs: process)
    dut = android(serialnumber=serial, prepare=False)

    assert dut.check_adb_status(waitTime=5) is True
