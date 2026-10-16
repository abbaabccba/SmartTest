from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[4]


PROBE = r'''
import sys
from pathlib import Path
root_dir = Path(sys.argv[1])
sys.path[:0] = [str(root_dir), str(root_dir / "client/app"), str(root_dir / "client/app/ui")]
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem, QQuickWindow
from FluentUI import FluentUI
from example.imports import resource_rc
from core.issues.create_schema import CreateFieldControl, CreateFieldOption, CreateFieldSchema
from ui.example.bridge.RedmineBridge import RedmineBridge
from core.tools.SmartHome.redmine.clone_draft import RedmineCloneDraftService
from core.tools.SmartHome.redmine.clone_controller import _new_record
from core.tools.SmartHome.redmine.models import RedmineIssueDetail, RedmineProject
import time

def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate()

class Auth(QObject):
    authChanged = Signal()
    username = "qml-acceptance"

app = QGuiApplication([])
schema = (
    CreateFieldSchema("attachment_links", "Attachment links", False, CreateFieldControl.TEXT),
    CreateFieldSchema("channel", "Channel of Reporter", True, CreateFieldControl.CASCADE,
        options=(CreateFieldOption("customer", "Customer-Feedback"),
                 CreateFieldOption("self", "Self-Test"))),
    CreateFieldSchema("case_id", "Test Case ID", True, CreateFieldControl.TEXT),
)
auth = Auth()
bridge = RedmineBridge(auth)
draft = RedmineCloneDraftService().build(
    issue=RedmineIssueDetail(id="1", url="https://redmine/issues/1"),
    project=RedmineProject(name="Project", identifier="project", url="p"),
    schema=schema, account=auth.username, department="QA", prepared_description="",
)
bridge._batch_controller._records = [_new_record(draft)]
bridge._batch_controller._state = "editing"
patches = {}
bridge.cloneDraftFieldChanged.connect(lambda issue_id, field: patches.update({field["fieldId"]: field}))
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
FluentUI.registerTypes(engine)
engine.rootContext().setContextProperty("RedmineBridge", bridge)
source = QUrl.fromLocalFile(str(root_dir / "client/app/ui/example/imports/example/qml/component/issue")).toString()
engine.loadData(("""
import QtQuick 2.15
import QtQuick.Window 2.15
import "%s"
Window {
    title: "SmartTest Jira Create Default Acceptance"
    visible: true
    width: 1000; height: 650
    JiraCreateBatchDialog {
        id: overlay; anchors.fill: parent
        cloneDrafts: RedmineBridge.cloneDrafts
        batchState: RedmineBridge.cloneBatchState
    }
    Connections {
        target: overlay
        function onUpdateCloneDraft(issueId, fieldId, value) { RedmineBridge.updateCloneDraft(issueId, fieldId, value) }
    }
    Connections {
        target: RedmineBridge
        function onCloneDraftFieldChanged(issueId, field) { overlay.updateDraftField(issueId, field) }
    }
}
""" % source).encode())
for _ in range(20):
    app.processEvents()
assert engine.rootObjects(), warnings
window = engine.rootObjects()[0]
def find_item(name):
    pending = [window.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    return None
wait_for(lambda: find_item("jiraCreateText_case_id") is not None)
def editor():
    item = find_item("jiraCreateText_case_id")
    assert item is not None, warnings
    return item
def select(index):
    combo = find_item("jiraCreateCascadeParent_channel")
    expression = QQmlExpression(QQmlEngine.contextForObject(combo), combo,
                               "currentIndex = %d; activated(%d)" % (index, index))
    expression.evaluate()
    assert not expression.hasError(), expression.error()
    for _ in range(10):
        app.processEvents()
assert editor().property("text") == ""
select(1)
assert editor().property("text") == "None", editor().property("text")
assert patches["case_id"]["value"] == "None" and patches["case_id"]["error"] == ""
assert set(patches) == {"channel", "case_id"}
bridge.updateCloneDraft("1", "case_id", "TV-CATEGORY-CASE")
app.processEvents()
select(0)
select(1)
assert editor().property("text") == "TV-CATEGORY-CASE"
select(0)
bridge.updateCloneDraft("1", "case_id", "")
app.processEvents()
assert editor().property("text") == ""
select(1)
assert editor().property("text") == "None"
assert bridge._jira_create_service is None
assert not warnings, warnings
assert window.grabWindow().save(sys.argv[2])
print("QML_VISIBLE_DEFAULT_OK: Customer-Feedback blank -> Self-Test None; explicit value preserved; other channel blank; no Jira create")
bridge.close()
'''


def test_real_create_dialog_displays_shared_default_after_channel_selection(tmp_path):
    screenshot = tmp_path / "jira-create-default.png"
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT), str(screenshot)],
        cwd=ROOT, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QML_VISIBLE_DEFAULT_OK" in result.stdout
    assert screenshot.is_file()
