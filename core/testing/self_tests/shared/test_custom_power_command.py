import os
import subprocess
import shlex
import sys

import pytest

from core.testing.params.contracts import env_field_contract
from core.testing.state.models import TestPageState as PageState
from core.testing.state.store import load_state, save_state
from core.testing.tool.env_tool.equipment_rows import default_env_config
from core.testing.tool.equipment import TestEquipment
from core.testing.tool.dut_tool.command_batch import CommandExecutionError
from core.testing.tool.relay_tool import get_relay_controller


def host_command(script, *args):
    parts = [str(arg) for arg in (sys.executable, script, *args)]
    return subprocess.list2cmdline(parts) if os.name == 'nt' else shlex.join(parts)


def test_custom_relay_persisted_config_runs_both_directions(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTTEST_LOG_DIR", str(tmp_path / "logs"))
    script = tmp_path / 'customer power.py'
    output = tmp_path / 'power events.txt'
    script.write_text('import sys\nfrom pathlib import Path\nwith Path(sys.argv[1]).open("a") as f: f.write(sys.argv[2] + "\\n")\n')
    config = default_env_config('relay', 'custom_command')
    config.update(power_on_command=host_command(script, output, 'on'),
                  power_off_command=host_command(script, output, 'off'))
    state = PageState()
    state.global_context['equipment'] = {'relay': config}
    state_path = tmp_path / 'state.json'
    save_state(state_path, state)
    from core.testing.runner.config import RUN_CONFIG_ENV, build_run_config_from_state, run_config_to_json

    run_config, _ = build_run_config_from_state(
        root_dir=tmp_path, state=load_state(state_path), device_lister=lambda: []
    )
    monkeypatch.setenv(RUN_CONFIG_ENV, run_config_to_json(run_config))
    equipment = TestEquipment.from_environment()
    equipment.relay.pulse('power_off')
    equipment.relay.pulse('power_on')
    assert output.read_text().splitlines() == ['off', 'on']
    for key in ('power_on_command', 'power_off_command'):
        assert env_field_contract('relay', 'custom_command', key).value_type == 'string'


def test_nonzero_command_reports_output_and_raises(tmp_path, monkeypatch):
    script = tmp_path / 'fail.py'
    script.write_text('import sys\nprint("customer stdout")\nprint("customer stderr", file=sys.stderr)\nsys.exit(7)\n')
    relay = get_relay_controller('custom_command', power_on_command=host_command(script), power_off_command='')
    records = []
    monkeypatch.setattr('core.testing.tool.relay_tool.custom_command.smart_log', lambda *args, **kwargs: records.append((args, kwargs)))
    with pytest.raises(CommandExecutionError, match='code 7'):
        relay.pulse('power_on')
    assert records[0][0][2:] == (7, 'customer stdout\n', 'customer stderr\n')
    assert records[0][1]['level'] == 'error'
    with pytest.raises(ValueError, match='power_off'):
        relay.pulse('power_off')


def test_existing_snmp_and_usb_configurations(monkeypatch):
    from core.testing.tool.relay_tool import usb_relay_controller
    monkeypatch.setattr(usb_relay_controller, 'UsbRelayController', lambda port, **kwargs: (port, kwargs))
    relay = TestEquipment({'relay': {'type': 'usb_relay', 'port': 'COM3', 'terminals': [{'terminal': 1, 'mode': 'NC', 'press_seconds': 2}]}}).relay
    assert relay == ('COM3', {'terminals': [{'terminal': 1, 'mode': 'NC', 'press_seconds': 2}], 'mode': None, 'press_seconds': None})
    snmp = TestEquipment({'relay': {'type': 'snmp_pdu', 'ip': '192.0.2.1', 'port': 2}}).relay
    assert snmp.port == ('192.0.2.1', 2)


def test_custom_command_text_has_finished_translations():
    from pathlib import Path
    import xml.etree.ElementTree as ET

    root = Path(__file__).resolve().parents[4]
    sources = {'test.env.relay.type.custom_command', 'test.env.relay.custom_command.power_on_command.label', 'test.env.relay.custom_command.power_off_command.label',
               'test.env.relay.custom_command.command.description'}
    for locale in ('en_US', 'zh_CN'):
        catalog = ET.parse(root / f'client/app/ui/example/example_{locale}.ts').getroot()
        context = next(item for item in catalog.findall('context') if item.findtext('name') == 'TestPageBridge')
        translations = {item.findtext('source'): item.find('translation') for item in context.findall('message')}
        for source in sources:
            assert source in translations
            assert translations[source].text and translations[source].get('type') != 'unfinished'


def test_real_qml_custom_relay_entry_edit_and_restore(tmp_path):
    from pathlib import Path

    pytest.importorskip("PySide6")
    root = Path(__file__).resolve().parents[4]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
               SMARTTEST_LOG_DIR=str(tmp_path / "logs"), POWER_UI_STATE_DIR=str(tmp_path))
    script = r"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / 'client/app/ui'))
from PySide6.QtCore import QObject, Signal, Property, QUrl, QMetaObject, Q_ARG, QTranslator, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickWindow
from FluentUI import FluentUI
from example.imports import resource_rc
from example.bridge.TestPageBridge import TestPageBridge
from core.testing.state.models import SelectedCase

app = QGuiApplication([])
app.setOrganizationName('SmartTestPowerValidation')
app.setApplicationName('PowerValidation')
root = Path(os.environ['POWER_UI_STATE_DIR'])
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root))
translator = QTranslator()
assert translator.load(':/example/i18n/example_zh_CN.qm')
app.installTranslator(translator)
TestPageBridge._default_state_path = lambda self: root / 'state.json'
TestPageBridge.discoverCases = lambda self: None
bridge = TestPageBridge(Path.cwd())
bridge._cases = [{'nodeid':'ac', 'android_case_id':'ac_onoff', 'file':'core/testing/tests/android/stress/test_ac_onoff.py', 'name':'AC power', 'case_type':'stress', 'required_equipment':['relay']}]
bridge._rebuild_case_indexes()
bridge._state.selected = [SelectedCase(nodeid='ac')]
bridge._bind_params_state()
class Run(QObject):
    errorOccurred = Signal(str)
    validationFailed = Signal(str)
    @Property(bool, constant=True)
    def isRunning(self): return False
run = Run()
engine = QQmlApplicationEngine()
FluentUI.registerTypes(engine)
engine.rootContext().setContextProperty('TestPageBridge', bridge)
engine.rootContext().setContextProperty('RunBridge', run)
component = QQmlComponent(engine, QUrl('qrc:/example/qml/page/T_TestConfig.qml'))
page = component.create()
if page is None: raise RuntimeError('\n'.join(e.toString() for e in component.errors()))
window = QQuickWindow()
window.resize(1600, 1100)
page.setParentItem(window.contentItem())
page.setWidth(1600)
page.setHeight(1100)
window.show()
for _ in range(15): app.processEvents()

def all_items(item):
    yield item
    for c in item.childItems(): yield from all_items(c)
def find(name):
    return next((i for i in all_items(page) if i.objectName() == name), None)
combo = find('env_equipment_type_relay')
assert combo is not None, bridge.envEquipmentRows()
assert combo.property('count') == 3
combo.setProperty('currentIndex', 2)
assert QMetaObject.invokeMethod(combo, 'activated', Q_ARG(int, 2))
for _ in range(15): app.processEvents()
assert bridge._equipment_config()['relay']['type'] == 'custom_command'
assert combo.property('currentText') == '自定义命令/脚本'
for key, text in [('power_on_command', 'python "/customer tools/power.py" on'), ('power_off_command', 'python "/customer tools/power.py" off')]:
    edit = find('env_equipment_relay_' + key)
    assert edit is not None
    assert edit.isVisible()
    assert edit.width() > 260
    edit.setProperty('text', text)
    assert QMetaObject.invokeMethod(edit, 'editingFinished')
    for _ in range(10): app.processEvents()
    assert bridge.envEquipmentValue('relay', key) == text
assert window.grabWindow().save(str(root / 'custom-power-ui.png'))
from core.testing.state.store import load_state
saved = load_state(root / 'state.json').global_context['equipment']['relay']
assert saved['type'] == 'custom_command' and saved['power_on_command'].endswith(' on') and saved['power_off_command'].endswith(' off')
page.setParentItem(None)
page.setParent(None)
page.deleteLater()
from PySide6.QtCore import QCoreApplication, QEvent
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
restored = TestPageBridge(Path.cwd())
restored._cases = bridge._cases
restored._rebuild_case_indexes()
engine.rootContext().setContextProperty('TestPageBridge', restored)
page2 = component.create()
page2.setParentItem(window.contentItem())
page2.setWidth(1600); page2.setHeight(1100)
for _ in range(15): app.processEvents()
page = page2
assert find('env_equipment_relay_power_on_command').property('text') == saved['power_on_command']
assert find('env_equipment_relay_power_off_command').property('text') == saved['power_off_command']
print('PASS real QRC QML type selection, command editing, persistence and page recreation')
page.setParentItem(None)
page.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
window.close()
engine.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS real QRC QML" in result.stdout
