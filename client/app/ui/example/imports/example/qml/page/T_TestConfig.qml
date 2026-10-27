import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15
import QtQml 2.15
import FluentUI 1.0
import "../global"
import "../component"
import "../state"

FluPage {
    id: page_root
    title: qsTr("Test")
    launchMode: FluPageType.SingleInstance
    property int footerHeight: 30

    property int stateVersion: 0
    property var selectedModel: []
    property var selectedCaseParamsModel: []
    property var dutDynamicParamsModel: []
    property var envEquipmentModel: []
    property var caseTreeDataSource: []
    property var suiteModel: []
    property var suiteSelectedRow: null
    property string suiteDialogMode: "create"
    property string suiteDraftName: ""
    property string suiteDraftDescription: ""
    property bool suiteDraftShared: false
    property bool suiteServiceAvailable: TestPageBridge.suiteError === ""
    TestPageState{ id: test_page_state }
    property var caseExpandState: ({})
    property var caseParamExpandState: ({})
    property string validationDialogMessage: ""

    function trimmedCasePath(filePath){
        var path = (filePath || "").toString()
        if(path.indexOf("testing/tests/") === 0){
            return path.substring("testing/tests/".length)
        }
        return path
    }

    function suiteErrorText(code){
        if(code === "authentication_required") return qsTr("Please sign in to use test suites.")
        if(code === "invalid_input") return qsTr("Please check the suite name and selected cases.")
        if(code === "name_conflict") return qsTr("A test suite with this name already exists.")
        if(code === "not_found") return qsTr("The test suite no longer exists or is not accessible.")
        if(code === "revision_conflict") return qsTr("The test suite changed elsewhere. Refresh and try again.")
        if(code === "service_unavailable") return qsTr("The test suite service is unavailable.")
        if(code.indexOf("missing_nodeids:") === 0) return qsTr("Some saved test cases are no longer available:\n%1").arg(code.substring(16))
        return code
    }

    function openSuiteDialog(mode, row){
        suiteDialogMode = mode
        if(mode === "update" && row){
            suiteDraftName = row.name || ""
            suiteDraftDescription = row.description || ""
            suiteDraftShared = row.visibility === "shared"
        }else if(mode === "copy" && row){
            suiteDraftName = (row.name || "") + " Copy"
            suiteDraftDescription = ""
            suiteDraftShared = false
        }else{
            suiteDraftName = ""
            suiteDraftDescription = ""
            suiteDraftShared = false
        }
        dialog_suite.open()
    }

    function themePair(lightColor, darkColor){
        return FluTheme.dark ? darkColor : lightColor
    }

    function caseParamHeaderColor(){
        return themePair("#F5F9FF", "#202A36")
    }

    function caseParamHeaderBorderColor(){
        return themePair("#CFE3F8", "#3A4D63")
    }

    function caseParamAccentColor(){
        return themePair("#0F6CBD", "#60CDFF")
    }

    function caseParamHeaderTextColor(){
        return themePair("#0F172A", "#F3F7FA")
    }

    function compactEditorInline(containerWidth){
        return containerWidth >= 420
    }

    function compactEditorWidth(containerWidth){
        return Math.max(96, Math.min(260, containerWidth * 0.32))
    }

    function fieldTextValue(fieldData){
        var _version = stateVersion
        var value = fieldData ? fieldData.value : ""
        if(value === undefined || value === null){
            return ""
        }
        return value + ""
    }

    function fieldBoolValue(fieldData){
        var _version = stateVersion
        var value = fieldData ? fieldData.value : ""
        if(value === true || value === false){
            return value
        }
        var text = (value === undefined || value === null) ? "" : (value + "").toLowerCase()
        return text === "true" || text === "1" || text === "yes" || text === "on"
    }

    function fieldListContains(fieldData, value){
        var values = fieldData && fieldData.list_values ? fieldData.list_values : []
        return values.indexOf((value || "").toString().trim()) >= 0
    }

    function displayText(value, source){
        var text = (value === undefined || value === null) ? "" : (value + "")
        if(source === "fixed"){
            return qsTranslate("TestPageBridge", text)
        }
        return text
    }

    function fieldLabel(fieldData){
        return displayText(fieldData ? fieldData.label : "", fieldData ? fieldData.label_source : "dynamic")
    }

    function fieldDescription(fieldData){
        return displayText(fieldData ? fieldData.description : "", fieldData ? fieldData.description_source : "dynamic")
    }

    function fieldScopeLabel(fieldData){
        return displayText(fieldData ? fieldData.scope_label : "", fieldData ? fieldData.scope_label_source : "dynamic")
    }

    function fieldSummary(fieldData){
        if(fieldData && fieldData.readonly === true){
            return qsTr("Configured under DUT.")
        }
        var summary = fieldScopeLabel(fieldData)
        var description = fieldDescription(fieldData)
        if(description){
            summary += summary ? " - " + description : description
        }
        return summary
    }

    function isCaseParamExpanded(nodeid, requiredCount){
        if(caseParamExpandState[nodeid] === undefined){
            caseParamExpandState[nodeid] = requiredCount > 0
        }
        return caseParamExpandState[nodeid] === true
    }

    function isExpandedByDefault(key){
        if(caseExpandState[key] === undefined){
            caseExpandState[key] = false
        }
        return caseExpandState[key] === true
    }

    function expandedKeys(){
        var keys = []
        for(var key in caseExpandState){
            if(caseExpandState[key] === true){
                keys.push(key)
            }
        }
        return keys
    }

    Component{
        id: paramFieldEditorComponent
        ColumnLayout{
            property var fieldData: ({})
            property string caseNodeId: ""
            property string dutSerial: ""
            property string editMode: "case"
            property real editorWidth: width
            property bool readonlyField: fieldData.readonly === true
            property bool compactTextField: fieldData.type === "string" || fieldData.type === "float" || fieldData.type === "int"
            Layout.fillWidth: true
            spacing: 2

            function saveValue(value){
                if(editMode === "dut"){
                    TestPageBridge.saveDutDynamicParamValue(dutSerial, fieldData.key, value)
                }else{
                    TestPageBridge.saveCaseParamValue(caseNodeId, fieldData.key, value)
                }
            }

            function setValue(value){
                if(editMode === "dut"){
                    TestPageBridge.setDutDynamicParamValue(dutSerial, fieldData.key, value)
                }else{
                    TestPageBridge.setCaseParamValue(caseNodeId, fieldData.key, value)
                }
            }

            function setListItemSelected(option, checked){
                if(editMode === "dut"){
                    TestPageBridge.setDutDynamicParamListItemSelected(dutSerial, fieldData.key, option, checked)
                }else{
                    TestPageBridge.setCaseParamListItemSelected(caseNodeId, fieldData.key, option, checked)
                }
            }

            GridLayout{
                visible: !readonlyField && compactTextField
                Layout.fillWidth: true
                columns: compactEditorInline(editorWidth) ? 2 : 1
                columnSpacing: 12
                rowSpacing: 4
                ColumnLayout{
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: 2
                    FluText{
                        text: fieldLabel(fieldData)
                        font: FluTextStyle.BodyStrong
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                    FluText{
                        Layout.fillWidth: true
                        text: fieldSummary(fieldData)
                        font: FluTextStyle.Caption
                        color: FluTheme.fontSecondaryColor
                        wrapMode: Text.WordWrap
                    }
                }
                FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                    id: compact_text_param
                    enabled: fieldData.readonly !== true
                    Layout.fillWidth: !compactEditorInline(editorWidth)
                    Layout.preferredWidth: compactEditorInline(editorWidth) ? compactEditorWidth(editorWidth) : -1
                    Layout.maximumWidth: compactEditorInline(editorWidth) ? 260 : 16777215
                    placeholderText: fieldData.default !== undefined && fieldData.default !== null ? (fieldData.default + "") : ""
                    cleanEnabled: false
                    property bool persistReady: false
                    property bool syncingFromState: false
                    function syncFromState(){
                        if(!visible || activeFocus){
                            return
                        }
                        var nextText = fieldTextValue(fieldData)
                        if(text !== nextText){
                            syncingFromState = true
                            text = nextText
                            syncingFromState = false
                        }
                    }
                    function persistValue(){
                        if(!persistReady || syncingFromState){
                            return
                        }
                        if(fieldData.type === "int"){
                            var parsed = parseInt(text, 10)
                            if(isNaN(parsed)){
                                return
                            }
                            saveValue(parsed)
                            return
                        }
                        saveValue(text)
                    }
                    onTextChanged: persistValue()
                    Component.onCompleted: {
                        syncFromState()
                        persistReady = true
                    }
                    onVisibleChanged: syncFromState()
                    onActiveFocusChanged: {
                        if(!activeFocus){
                            persistValue()
                        }
                    }
                    onEditingFinished: persistValue()
                    Connections {
                        target: page_root
                        function onStateVersionChanged() {
                            compact_text_param.syncFromState()
                        }
                    }
                }
            }

            RowLayout{
                visible: !readonlyField && !compactTextField
                Layout.fillWidth: true
                spacing: 8
                FluText{
                    text: fieldLabel(fieldData)
                    font: FluTextStyle.BodyStrong
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    elide: editMode === "dut" ? Text.ElideRight : Text.ElideNone
                }
                AppLoadingIndicator{
                    visible: fieldData.loading === true
                    Layout.preferredWidth: editMode === "dut" ? 14 : 16
                    Layout.preferredHeight: editMode === "dut" ? 14 : 16
                    indeterminate: true
                }
            }

            FluText{
                visible: !readonlyField && !compactTextField && editMode !== "dut"
                Layout.fillWidth: true
                text: fieldSummary(fieldData)
                font: FluTextStyle.Caption
                color: FluTheme.fontSecondaryColor
                wrapMode: Text.WordWrap
            }

            FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                id: text_param
                visible: !readonlyField && fieldData.type === "path"
                enabled: fieldData.readonly !== true
                Layout.fillWidth: true
                cleanEnabled: editMode === "dut" ? false : true
                placeholderText: fieldData.default !== undefined && fieldData.default !== null ? (fieldData.default + "") : ""
                property bool persistReady: false
                property bool syncingFromState: false
                function syncFromState(){
                    if(!visible || activeFocus){
                        return
                    }
                    var nextText = fieldTextValue(fieldData)
                    if(text !== nextText){
                        syncingFromState = true
                        text = nextText
                        syncingFromState = false
                    }
                }
                function persistValue(){
                    if(!persistReady || syncingFromState){
                        return
                    }
                    saveValue(text)
                }
                onTextChanged: persistValue()
                Component.onCompleted: {
                    syncFromState()
                    persistReady = true
                }
                onVisibleChanged: syncFromState()
                onActiveFocusChanged: {
                    if(!activeFocus){
                        persistValue()
                    }
                }
                onEditingFinished: persistValue()
                Connections {
                    target: page_root
                    function onStateVersionChanged() {
                        text_param.syncFromState()
                    }
                }
            }

            FluComboBox{ /* persistence-opt-out: owner:TestPageBridge */
                visible: !readonlyField && fieldData.type === "enum"
                Layout.fillWidth: true
                model: fieldData.enum_values || []
                enabled: fieldData.readonly !== true && (fieldData.enum_values || []).length > 0
                currentIndex: {
                    var currentValue = fieldTextValue(fieldData)
                    var options = fieldData.enum_values || []
                    if(currentValue === ""){
                        return options.indexOf("None")
                    }
                    return options.indexOf(currentValue)
                }
                onActivated: (activatedIndex)=> {
                    var options = fieldData.enum_values || []
                    var indexToUse = activatedIndex === undefined ? currentIndex : activatedIndex
                    if(indexToUse >= 0 && indexToUse < options.length){
                        var nextValue = options[indexToUse]
                        if(nextValue === "None"){
                            nextValue = ""
                        }
                        setValue(nextValue)
                    }
                }
            }

            ColumnLayout{
                visible: !readonlyField && fieldData.type === "multi_enum"
                Layout.fillWidth: true
                spacing: 2
                Repeater{
                    model: fieldData.enum_values || []
                    FluCheckBox{ /* persistence-opt-out: owner:TestPageBridge */
                        Layout.alignment: Qt.AlignLeft
                        Layout.preferredHeight: 24
                        size: 16
                        textSpacing: 4
                        font: FluTextStyle.Caption
                        enabled: fieldData.readonly !== true
                        text: modelData
                        checked: {
                            var _version = stateVersion
                            return fieldListContains(fieldData, modelData)
                        }
                        onClicked: setListItemSelected(modelData, checked)
                    }
                }
            }

            FluToggleSwitch{ /* persistence-opt-out: owner:TestPageBridge */
                visible: !readonlyField && fieldData.type === "bool"
                enabled: fieldData.readonly !== true
                checked: fieldBoolValue(fieldData)
                text: checked ? qsTr("Enabled") : qsTr("Disabled")
                onClicked: setValue(checked)
            }

            FluMultilineTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                id: multiline_text_param
                visible: !readonlyField && fieldData.type === "multiline"
                enabled: fieldData.readonly !== true
                Layout.fillWidth: true
                Layout.preferredHeight: 76
                placeholderText: fieldData.default !== undefined && fieldData.default !== null ? (fieldData.default + "") : ""
                isCtrlEnterForNewline: true
                Binding {
                    target: multiline_text_param
                    property: "text"
                    when: multiline_text_param.visible && !multiline_text_param.activeFocus
                    value: fieldTextValue(fieldData)
                }
                onTextChanged: saveValue(text)
                onActiveFocusChanged: {
                    if(!activeFocus){
                        setValue(text)
                    }
                }
                onCommit: setValue(text)
            }

            RowLayout{
                visible: readonlyField
                Layout.fillWidth: true
                spacing: 8
                FluText{
                    text: fieldLabel(fieldData)
                    font: FluTextStyle.BodyStrong
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                FluText{
                    text: qsTr("Configured under DUT.")
                    font: FluTextStyle.Caption
                    color: FluTheme.fontSecondaryColor
                }
            }

            FluDivider{
                Layout.fillWidth: true
            }
        }
    }

    function decorateTreeNodes(nodes){
        var source = nodes || []
        var decorated = []
        for(var i = 0; i < source.length; i++){
            var node = source[i]
            var rowType = node.rowType || ""
            var iconSource = 0
            if(rowType === "folder" || rowType === "root"){
                iconSource = FluentIcons.Folder
            }else if(rowType === "file"){
                iconSource = FluentIcons.Document
            }
            var copy = {
                title: node.title,
                _key: node._key,
                rowType: rowType,
                iconSource: iconSource,
                expanded: node.expanded === true,
                file: node.file,
                checked: node.checked === true,
                selectionState: node.selectionState || (node.checked === true ? "checked" : "unchecked"),
                selectableCount: node.selectableCount || 0,
                selectedCount: node.selectedCount || 0
            }
            if(node.children !== undefined){
                copy.children = decorateTreeNodes(node.children)
            }
            decorated.push(copy)
        }
        return decorated
    }

    function refreshViewModels(){
        caseTreeDataSource = decorateTreeNodes(TestPageBridge.caseTree((txt_filter.text || "").toString(), expandedKeys()))
        selectedModel = TestPageBridge.selectedFileRows()
        selectedCaseParamsModel = TestPageBridge.selectedCaseParamRows()
        dutDynamicParamsModel = TestPageBridge.dutDynamicParamRows()
        envEquipmentModel = TestPageBridge.envEquipmentRows()
    }

    Connections{
        target: TestPageBridge
        function onCasesChanged(){
            refreshViewModels()
        }
        function onStateChanged(){
            stateVersion = stateVersion + 1
            refreshViewModels()
        }
        function onSuiteChanged(){
            suiteModel = TestPageBridge.suiteRows()
            if(suiteSelectedRow){
                var selectedStillVisible = false
                for(var i = 0; i < suiteModel.length; i++){
                    if(suiteModel[i].id === suiteSelectedRow.id){
                        suiteSelectedRow = suiteModel[i]
                        selectedStillVisible = true
                        break
                    }
                }
                if(!selectedStillVisible){
                    suiteSelectedRow = null
                }
            }
        }
        function onErrorOccurred(msg){
            showError(msg)
        }
    }

    Connections{
        target: RunBridge
        function onErrorOccurred(msg){
            showError(msg)
        }
        function onValidationFailed(msg){
            validationDialogMessage = msg
            dialog_validation.open()
        }
    }

    Component.onCompleted: {
        TestPageBridge.discoverCases()
        refreshViewModels()
        suiteModel = TestPageBridge.suiteRows()
    }

    FluSplitLayout{
        id: layout_main
        anchors.fill: parent
        anchors.bottomMargin: footerHeight
        orientation: Qt.Horizontal

        FluSplitLayout{
            SplitView.fillWidth: true
            SplitView.preferredWidth: layout_main.width * 0.30
            SplitView.minimumWidth: 260
            SplitView.fillHeight: true
            orientation: Qt.Vertical

            FluFrame{
                SplitView.fillHeight: true
                padding: 10
                ColumnLayout{
                    anchors.fill: parent
                    spacing: 8
                    RowLayout{
                        Layout.fillWidth: true
                        FluText{ text: qsTr("Test Suites"); font: FluTextStyle.Subtitle; Layout.fillWidth: true }
                        FluIconButton{
                            iconSource: test_page_state.suitePanelExpanded ? FluentIcons.ChevronUp : FluentIcons.ChevronDown
                            onClicked: test_page_state.updateSuitePanel(!test_page_state.suitePanelExpanded, test_page_state.suitePanelHeight)
                        }
                    }
                    FluSplitLayout{
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        orientation: Qt.Vertical
                    FluGroupBox{
                        title: ""
                        visible: test_page_state.suitePanelExpanded
                        SplitView.fillWidth: true
                        SplitView.preferredHeight: test_page_state.suitePanelHeight
                        SplitView.minimumHeight: 140
                        onHeightChanged: {
                            if(visible && height >= 140){
                                test_page_state.updateSuitePanel(test_page_state.suitePanelExpanded, height)
                            }
                        }
                        ColumnLayout{
                            anchors.fill: parent
                            RowLayout{
                                Layout.fillWidth: true
                                FluToggleButton{
                                    text: qsTr("My Suites")
                                    checked: TestPageBridge.suiteScope === "mine"
                                    onClicked: TestPageBridge.setSuiteScope("mine")
                                }
                                FluToggleButton{
                                    text: qsTr("Shared Suites")
                                    checked: TestPageBridge.suiteScope === "shared"
                                    onClicked: TestPageBridge.setSuiteScope("shared")
                                }
                                Item{ Layout.fillWidth: true }
                                FluIconButton{
                                    iconSource: FluentIcons.Refresh
                                    disabled: TestPageBridge.suiteRefreshRunning
                                    onClicked: TestPageBridge.refreshSuites()
                                }
                            }
                            AppLoadingIndicator{
                                visible: TestPageBridge.suitePanelLoading || TestPageBridge.suiteRefreshRunning
                                running: visible
                                compact: true
                                text: qsTr("Loading test suites...")
                            }
                            FluTextBox{
                                Layout.fillWidth: true
                                placeholderText: qsTr("Search test suites...")
                                onTextChanged: TestPageBridge.setSuiteSearchText(text)
                            }
                            FluText{
                                visible: TestPageBridge.suiteError !== ""
                                text: suiteErrorText(TestPageBridge.suiteError)
                                color: FluTheme.dark ? "#FF99A4" : "#C42B1C"
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            ListView{
                                id: list_suites
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                model: suiteModel
                                delegate: Rectangle{
                                    width: list_suites.width
                                    height: 34
                                    radius: 4
                                    color: page_root.suiteSelectedRow && page_root.suiteSelectedRow.id === modelData.id
                                           ? FluTheme.itemPressColor : "transparent"
                                    RowLayout{
                                        anchors.fill: parent
                                        anchors.margins: 5
                                        FluText{ text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                                        FluText{ text: qsTr("%1 cases").arg(modelData.caseCount); font: FluTextStyle.Caption }
                                        FluText{ text: modelData.ownerDisplayName; font: FluTextStyle.Caption }
                                    }
                                    MouseArea{ anchors.fill: parent; onClicked: page_root.suiteSelectedRow = modelData }
                                }
                                FluText{
                                    anchors.centerIn: parent
                                    visible: !TestPageBridge.suitePanelLoading && suiteModel.length === 0
                                    text: TestPageBridge.suiteScope === "mine" ? qsTr("No personal test suites yet.") : qsTr("No shared test suites.")
                                }
                            }
                            RowLayout{
                                Layout.fillWidth: true
                                FluButton{
                                    text: qsTr("Load")
                                    disabled: !suiteServiceAvailable || !suiteSelectedRow || !TestPageBridge.discoveryLoaded || TestPageBridge.suiteActionRunning
                                    onClicked: TestPageBridge.loadSuite(suiteSelectedRow.id)
                                }
                                FluButton{
                                    visible: TestPageBridge.suiteScope === "mine"
                                    text: qsTr("Update")
                                    disabled: !suiteServiceAvailable || !suiteSelectedRow || TestPageBridge.activeSuiteId !== suiteSelectedRow.id || TestPageBridge.suiteActionRunning
                                    onClicked: openSuiteDialog("update", suiteSelectedRow)
                                }
                                FluButton{
                                    visible: TestPageBridge.suiteScope === "mine"
                                    text: qsTr("Delete")
                                    disabled: !suiteServiceAvailable || !suiteSelectedRow || TestPageBridge.suiteActionRunning
                                    onClicked: dialog_delete_suite.open()
                                }
                                Item{ Layout.fillWidth: true }
                                FluFilledButton{
                                    text: TestPageBridge.suiteScope === "mine" ? qsTr("Save Current") : qsTr("Save a Copy")
                                    disabled: !suiteServiceAvailable || TestPageBridge.suiteActionRunning
                                              || (TestPageBridge.suiteScope === "shared" && !suiteSelectedRow)
                                    onClicked: openSuiteDialog(
                                        TestPageBridge.suiteScope === "mine" ? "create" : "copy",
                                        suiteSelectedRow
                                    )
                                }
                            }
                        }
                    }
                    FluFrame{
                        SplitView.fillWidth: true
                        SplitView.fillHeight: true
                        padding: 0
                        ColumnLayout{
                            anchors.fill: parent
                            spacing: 8
                    FluText{
                        text: qsTr("Test Cases")
                        font: FluTextStyle.Subtitle
                    }
                    FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                        id: txt_filter
                        placeholderText: qsTr("Filter by file...")
                        Layout.fillWidth: true
                        onTextChanged: refreshViewModels()
                    }
                    FluGroupBox{
                        title: ""
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        padding: 8
                        FluTreeView{
                            id: tree_cases
                            anchors.fill: parent
                            headerVisible: false
                            showLine: false
                            cellHeight: 32
                            depthPadding: 10
                            checkable: true
                            checkLeafOnly: false
                            clickLeafRowToToggleCheckOnly: true
                            columnSource: [{
                                title: "",
                                dataIndex: "title",
                                width: Math.max(220, tree_cases.width - 8)
                            }]
                            dataSource: caseTreeDataSource
                            onLeafCheckToggled: (rowData, checked)=>{
                                if(rowData && rowData.rowType === "file" && rowData.file){
                                    TestPageBridge.setFileSelected(rowData.file, checked === true)
                                }else if(rowData && rowData._key){
                                    TestPageBridge.setTreeNodeSelected(rowData._key, checked === true,
                                                                       (txt_filter.text || "").toString())
                                }
                            }
                            onBranchToggled: (rowData, expanded)=>{
                                if(rowData && rowData._key){
                                    caseExpandState[rowData._key] = expanded === true
                                }
                            }
                        }
                    }
                        }
                    }
                    }
                }
            }

            FluFrame{
                SplitView.preferredHeight: 240
                padding: 10
                ColumnLayout{
                    anchors.fill: parent
                    spacing: 8
                    RowLayout{
                        Layout.fillWidth: true
                        FluText{
                            text: qsTr("Selected (%1)").arg(list_selected.count)
                            font: FluTextStyle.Subtitle
                        }
                    }
                    ListView{
                        id: list_selected
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: selectedModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: FluScrollBar{}
                        property bool dragActive: false
                        property int dragFromIndex: -1
                        property int dragToIndex: -1

                        move: Transition{
                            NumberAnimation{ properties: "y"; duration: 220; easing.type: Easing.OutCubic }
                        }
                        delegate: Item{
                            id: row_sel
                            width: list_selected.width
                            height: 40
                            z: drag_area_sel.pressed ? 3 : 1
                            Rectangle{
                                id: sel_item_bg
                                anchors.fill: parent
                                radius: 4
                                color: drag_area_sel.containsMouse ? FluTheme.itemHoverColor : "transparent"
                                RowLayout{
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    spacing: 8
                                    FluText{
                                        text: (index+1) + "."
                                        font: FluTextStyle.Caption
                                        color: FluTheme.fontSecondaryColor
                                    }
                                    FluText{
                                        text: trimmedCasePath(modelData.file)
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                    FluIconButton{
                                        id: btn_remove
                                        iconSource: FluentIcons.ChromeClose
                                        iconSize: 12
                                        width: 30
                                        height: 30
                                        onClicked: {
                                            TestPageBridge.setFileSelected(modelData.file, false)
                                        }
                                    }
                                }
                                MouseArea{
                                    id: drag_area_sel
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.rightMargin: 42
                                    z: 10
                                    acceptedButtons: Qt.LeftButton
                                    preventStealing: true
                                    hoverEnabled: true
                                    propagateComposedEvents: false
                                    drag.threshold: 1
                                    onPressed: (mouse)=>{
                                        list_selected.dragActive = true
                                        list_selected.dragFromIndex = index
                                        list_selected.dragToIndex = index
                                        mouse.accepted = true
                                    }
                                    onPositionChanged: (mouse)=>{
                                        if(!list_selected.dragActive){
                                            return
                                        }
                                        var pt = drag_area_sel.mapToItem(list_selected.contentItem, mouse.x, mouse.y)
                                        var hoverIndex = list_selected.indexAt(pt.x, pt.y)
                                        if(hoverIndex === -1){
                                            return
                                        }
                                        if(hoverIndex === list_selected.dragToIndex){
                                            return
                                        }
                                        list_selected.dragToIndex = hoverIndex
                                    }
                                    onReleased: (mouse)=>{
                                        var finalFromIndex = list_selected.dragFromIndex
                                        var finalToIndex = list_selected.dragToIndex
                                        list_selected.dragActive = false
                                        list_selected.dragFromIndex = -1
                                        list_selected.dragToIndex = -1
                                        if(finalFromIndex >= 0 && finalToIndex >= 0 && finalFromIndex !== finalToIndex){
                                            TestPageBridge.moveSelectedFile(finalFromIndex, finalToIndex)
                                        }
                                    }
                                    onCanceled: {
                                        list_selected.dragActive = false
                                        list_selected.dragFromIndex = -1
                                        list_selected.dragToIndex = -1
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        FluFrame{
            SplitView.fillWidth: true
            SplitView.preferredWidth: layout_main.width * 0.42
            SplitView.minimumWidth: 260
            SplitView.fillHeight: true
            padding: 10
            ColumnLayout{
                anchors.fill: parent
                spacing: 8
                FluText{
                    text: qsTr("Case Parameters (%1)").arg(selectedCaseParamsModel.length)
                    font: FluTextStyle.Subtitle
                }
                Flickable{
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentHeight: col_case_params.implicitHeight
                    ScrollBar.vertical: FluScrollBar{}
                    ColumnLayout{
                        id: col_case_params
                        width: parent.width
                        spacing: 12
                        FluText{
                            visible: selectedCaseParamsModel.length === 0
                            Layout.fillWidth: true
                            text: qsTr("Select one or more test files to inspect the required parameters for each case.")
                            font: FluTextStyle.Body
                            color: FluTheme.fontSecondaryColor
                            wrapMode: Text.WordWrap
                        }
                        Repeater{
                            model: selectedCaseParamsModel
                            FluExpander{
                                id: case_param_expander
                                property string caseNodeId: modelData.nodeid || ""
                                property var caseFields: modelData.fields || []
                                Layout.fillWidth: true
                                Layout.topMargin: index === 0 ? 0 : 2
                                headerText: modelData.name + "  (" + modelData.required_params.length + ")"
                                headerCustomStyle: true
                                headerBackgroundColor: caseParamHeaderColor()
                                headerBorderColor: caseParamHeaderBorderColor()
                                headerTextColor: caseParamHeaderTextColor()
                                headerAccentColor: caseParamAccentColor()
                                headerAccentWidth: 3
                                headerTextLeftMargin: 22
                                headerTextStrong: true
                                expand: isCaseParamExpanded(caseNodeId, modelData.required_params.length)
                                contentHeight: expander_content.implicitHeight
                                onExpandChanged: {
                                    caseParamExpandState[caseNodeId] = expand
                                }

                                Item{
                                    id: expander_content
                                    width: parent.width
                                    implicitHeight: col_case_form.implicitHeight + 24

                                    ColumnLayout{
                                        id: col_case_form
                                        x: 12
                                        y: 12
                                        width: parent.width - 24
                                        spacing: 8

                                        FluText{
                                            text: trimmedCasePath(modelData.file)
                                            font: FluTextStyle.Caption
                                            color: FluTheme.fontSecondaryColor
                                            elide: Text.ElideMiddle
                                            Layout.fillWidth: true
                                        }

                                        FluText{
                                            visible: modelData.required_params.length === 0
                                            Layout.fillWidth: true
                                            text: qsTr("No configurable parameters are required for this case.")
                                            font: FluTextStyle.Body
                                            color: FluTheme.fontSecondaryColor
                                            wrapMode: Text.WordWrap
                                        }

                                        Repeater{
                                            model: {
                                                var _version = stateVersion
                                                return case_param_expander.caseFields
                                            }
                                            Loader{
                                                Layout.fillWidth: true
                                                sourceComponent: paramFieldEditorComponent
                                                onLoaded: {
                                                    item.fieldData = modelData
                                                    item.caseNodeId = case_param_expander.caseNodeId
                                                    item.editMode = "case"
                                                    item.editorWidth = col_case_form.width
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        FluSplitLayout{
            SplitView.fillWidth: true
            SplitView.preferredWidth: layout_main.width * 0.28
            SplitView.minimumWidth: 260
            SplitView.fillHeight: true
            orientation: Qt.Vertical

            FluFrame{
                SplitView.fillHeight: true
                SplitView.minimumHeight: 150
                padding: 10
                Flickable{
                    anchors.fill: parent
                    clip: true
                    contentHeight: col_dut.implicitHeight
                    ScrollBar.vertical: FluScrollBar{}
                    ColumnLayout{
                        id: col_dut
                        width: parent.width
                        spacing: 10

                        RowLayout{
                            Layout.fillWidth: true
                            spacing: 8
                            FluText{
                                text: qsTr("DUT")
                                font: FluTextStyle.Subtitle
                                Layout.fillWidth: true
                            }
                            FluIconButton{
                                iconSource: FluentIcons.Sync
                                iconSize: 14
                                width: 32
                                height: 32
                                text: qsTr("Refresh")
                                enabled: !TestPageBridge.dutRefreshRunning
                                onClicked: TestPageBridge.refreshGlobalSchema()
                            }
                        }

                        AppLoadingIndicator {
                            visible: TestPageBridge.dutRefreshRunning && TestPageBridge.dutRefreshPhase === "scan"
                            running: visible
                            text: qsTr("Scanning ADB devices")
                            detail: qsTr("The DUT list will be updated before Android Client preparation starts.")
                            compact: false
                            Layout.fillWidth: true
                            Layout.preferredWidth: 20
                            Layout.preferredHeight: 72
                        }
                        AppTaskProgress {
                            visible: TestPageBridge.dutRefreshPhase !== "idle"
                                     && TestPageBridge.dutRefreshPhase !== "scan"
                            Layout.fillWidth: true
                            running: TestPageBridge.dutRefreshRunning
                            indeterminate: TestPageBridge.dutRefreshRunning && TestPageBridge.dutRefreshProgress <= 0
                            value: TestPageBridge.dutRefreshProgress / 100.0
                            text: TestPageBridge.dutRefreshDetail
                            phase: TestPageBridge.dutRefreshPhase
                            status: qsTr("Phase: %1").arg(TestPageBridge.dutRefreshPhase)
                            detail: qsTr("Progress: %1%").arg(TestPageBridge.dutRefreshProgress)
                            error: TestPageBridge.dutRefreshError
                        }

                        Repeater{
                            model: {
                                var _version = stateVersion
                                return TestPageBridge.globalParamRows()
                            }
                            ColumnLayout{
                                Layout.fillWidth: true
                                spacing: 6
                                property var fieldData: modelData
                                property var fieldOptions: fieldData.enum_values || []
                                property var selectedDuts: fieldData.key === "dut" ? TestPageBridge.selectedDuts() : []
                                function dutChecked(serial){
                                    return selectedDuts.indexOf(serial) >= 0
                                }
                                FluText{
                                    text: fieldLabel(fieldData)
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                ColumnLayout{
                                    visible: fieldData.key === "dut"
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Repeater{
                                        model: fieldOptions
                                        delegate: Rectangle{
                                            Layout.fillWidth: true
                                            implicitHeight: 34
                                            radius: 4
                                            color: "transparent"
                                            RowLayout{
                                                anchors.fill: parent
                                                anchors.leftMargin: 4
                                                anchors.rightMargin: 4
                                                spacing: 8
                                                FluCheckBox{ /* persistence-opt-out: owner:TestPageBridge */
                                                    id: dut_check
                                                    checked: dutChecked(modelData)
                                                    onClicked: TestPageBridge.setDutSelected(modelData, checked)
                                                }
                                                FluText{
                                                    Layout.fillWidth: true
                                                    text: modelData
                                                    elide: Text.ElideMiddle
                                                }
                                            }
                                        }
                                    }
                                }
                                FluComboBox{ /* persistence-opt-out: owner:TestPageBridge */
                                    id: combo_global_param
                                    visible: fieldData.type === "enum" && fieldData.key !== "dut"
                                    Layout.fillWidth: true
                                    enabled: fieldOptions.length > 0
                                    model: fieldOptions
                                    currentIndex: {
                                        var _version = stateVersion
                                        var options = fieldOptions
                                        var currentValue = fieldTextValue(fieldData)
                                        return options.indexOf(currentValue)
                                    }
                                    onDownChanged: {
                                        if(down && fieldData.key === "dut"){
                                            TestPageBridge.refreshGlobalSchema()
                                        }
                                    }
                                    onActivated: {
                                        if(currentIndex >= 0){
                                            TestPageBridge.setGlobalValue(fieldData.key, currentText)
                                        }
                                    }
                                }
                                FluText{
                                    visible: fieldData.key === "dut" && fieldOptions.length === 0
                                    Layout.fillWidth: true
                                    text: qsTr("No DUT")
                                    font: FluTextStyle.Caption
                                    color: FluTheme.fontSecondaryColor
                                    wrapMode: Text.WordWrap
                                }
                                FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                                    id: text_global_param
                                    visible: fieldData.type !== "enum"
                                    Layout.fillWidth: true
                                    property bool persistReady: false
                                    property bool syncingFromState: false
                                    function syncFromState(){
                                        if(!visible || activeFocus){
                                            return
                                        }
                                        var nextText = fieldTextValue(fieldData)
                                        if(text !== nextText){
                                            syncingFromState = true
                                            text = nextText
                                            syncingFromState = false
                                        }
                                    }
                                    function persistValue(){
                                        if(!persistReady || syncingFromState){
                                            return
                                        }
                                        TestPageBridge.saveGlobalValue(fieldData.key, text)
                                    }
                                    onTextChanged: TestPageBridge.saveGlobalValue(fieldData.key, text)
                                    Component.onCompleted: {
                                        syncFromState()
                                        persistReady = true
                                    }
                                    onVisibleChanged: syncFromState()
                                    Connections {
                                        target: page_root
                                        function onStateVersionChanged() {
                                            text_global_param.syncFromState()
                                        }
                                    }
                                    onEditingFinished: TestPageBridge.setGlobalValue(fieldData.key, text)
                                }
                                FluDivider{
                                    Layout.fillWidth: true
                                }
                            }
                        }

                        ColumnLayout{
                            visible: dutDynamicParamsModel.length > 0
                            Layout.fillWidth: true
                            spacing: 6

                            FluDivider{
                                Layout.fillWidth: true
                            }

                            RowLayout{
                                Layout.fillWidth: true
                                spacing: 8
                                FluText{
                                    text: qsTr("Dynamic")
                                    font: FluTextStyle.Subtitle
                                    Layout.fillWidth: true
                                }
                                FluText{
                                    text: qsTr("DUT scoped")
                                    font: FluTextStyle.Caption
                                    color: FluTheme.fontSecondaryColor
                                }
                            }

                            Repeater{
                                model: dutDynamicParamsModel
                                FluExpander{
                                    id: dut_dynamic_expander
                                    property string dutSerial: modelData.dut_serial || ""
                                    property var dutFields: modelData.fields || []
                                    Layout.fillWidth: true
                                    headerText: dutSerial + "  (" + dutFields.length + ")"
                                    expand: index === 0
                                    contentHeight: dut_dynamic_content.implicitHeight

                                    Item{
                                        id: dut_dynamic_content
                                        width: parent.width
                                        implicitHeight: col_dut_dynamic_fields.implicitHeight + 20

                                        ColumnLayout{
                                            id: col_dut_dynamic_fields
                                            x: 10
                                            y: 10
                                            width: parent.width - 20
                                            spacing: 8

                                            Repeater{
                                                model: dut_dynamic_expander.dutFields
                                                Loader{
                                                    Layout.fillWidth: true
                                                    sourceComponent: paramFieldEditorComponent
                                                    onLoaded: {
                                                        item.fieldData = modelData
                                                        item.dutSerial = dut_dynamic_expander.dutSerial
                                                        item.editMode = "dut"
                                                        item.editorWidth = col_dut_dynamic_fields.width
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            FluFrame{
                visible: envEquipmentModel.length > 0
                SplitView.preferredHeight: 360
                SplitView.minimumHeight: 160
                padding: 10
                Flickable{
                    anchors.fill: parent
                    clip: true
                    contentHeight: col_env.implicitHeight
                    ScrollBar.vertical: FluScrollBar{}
                    ColumnLayout{
                        id: col_env
                        width: parent.width
                        spacing: 8

                        FluText{
                            text: qsTr("Env")
                            font: FluTextStyle.Subtitle
                            Layout.fillWidth: true
                        }

                        Repeater{
                            model: envEquipmentModel
                            FluExpander{
                                id: env_equipment_expander
                                property string equipmentKind: modelData.kind || ""
                                Layout.fillWidth: true
                                headerText: modelData.label || equipmentKind
                                expand: true
                                contentHeight: env_equipment_content.implicitHeight

                                Item{
                                    id: env_equipment_content
                                    width: parent.width
                                    implicitHeight: col_env_equipment_form.implicitHeight + 24

                                    ColumnLayout{
                                        id: col_env_equipment_form
                                        x: 12
                                        y: 12
                                        width: parent.width - 24
                                        spacing: 8

                                        FluText{
                                            text: modelData.typeLabel || qsTr("Type")
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }

                                        FluComboBox{ /* persistence-opt-out: owner:TestPageBridge */
                                            id: combo_env_equipment_type
                                            objectName: "env_equipment_type_" + env_equipment_expander.equipmentKind
                                            Layout.fillWidth: true
                                            textRole: "label"
                                            model: modelData.typeOptions || []
                                            currentIndex: {
                                                var options = modelData.typeOptions || []
                                                for(var i = 0; i < options.length; i++){
                                                    if(options[i].value === modelData.type){
                                                        return i
                                                    }
                                                }
                                                return options.length > 0 ? 0 : -1
                                            }
                                            onActivated: {
                                                var options = modelData.typeOptions || []
                                                if(currentIndex >= 0 && currentIndex < options.length){
                                                    TestPageBridge.setEnvEquipmentType(env_equipment_expander.equipmentKind, options[currentIndex].value)
                                                }
                                            }
                                        }

                                        Repeater{
                                            model: modelData.fields || []
                                            ColumnLayout{
                                                Layout.fillWidth: true
                                                spacing: 4
                                                property var fieldData: modelData
                                                property bool compactTextField: fieldData.type === "string" || fieldData.type === "int" || fieldData.type === "command"
                                                property bool inlineTextField: fieldData.type !== "command" && compactEditorInline(col_env.width)

                                                function terminalRows(){
                                                    var rows = fieldData.value || []
                                                    if(!rows || rows.length === undefined){
                                                        return []
                                                    }
                                                    var nextRows = []
                                                    for(var i = 0; i < rows.length; i++){
                                                        nextRows.push(rows[i])
                                                    }
                                                    return nextRows
                                                }

                                                GridLayout{
                                                    visible: compactTextField
                                                    Layout.fillWidth: true
                                                    columns: inlineTextField ? 2 : 1
                                                    columnSpacing: 12
                                                    rowSpacing: 4

                                                    ColumnLayout{
                                                        Layout.fillWidth: true
                                                        Layout.minimumWidth: 0
                                                        spacing: 2
                                                        RowLayout{
                                                            Layout.fillWidth: true
                                                            spacing: 6
                                                            FluText{
                                                                text: fieldLabel(fieldData)
                                                                font: FluTextStyle.BodyStrong
                                                                Layout.fillWidth: true
                                                                wrapMode: Text.WordWrap
                                                            }
                                                            AppLoadingIndicator{
                                                                visible: fieldData.loading === true
                                                                Layout.preferredWidth: 16
                                                                Layout.preferredHeight: 16
                                                                indeterminate: true
                                                            }
                                                        }
                                                        FluText{
                                                            visible: !!fieldDescription(fieldData)
                                                            Layout.fillWidth: true
                                                            text: fieldDescription(fieldData)
                                                            font: FluTextStyle.Caption
                                                            color: FluTheme.fontSecondaryColor
                                                            wrapMode: Text.WordWrap
                                                        }
                                                    }

                                                    FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                                                        id: text_env_equipment_compact
                                                        objectName: "env_equipment_" + env_equipment_expander.equipmentKind + "_" + fieldData.key
                                                        visible: compactTextField
                                                        Layout.fillWidth: !inlineTextField
                                                        Layout.preferredWidth: inlineTextField ? compactEditorWidth(col_env.width) : -1
                                                        Layout.maximumWidth: inlineTextField ? 260 : 16777215
                                                        cleanEnabled: false
                                                        property bool persistReady: false
                                                        function stateText(){
                                                            var value = TestPageBridge.envEquipmentValue(env_equipment_expander.equipmentKind, fieldData.key)
                                                            return value === undefined || value === null ? "" : (value + "")
                                                        }
                                                        function syncFromState(){
                                                            if(!visible || activeFocus){
                                                                return
                                                            }
                                                            var nextText = stateText()
                                                            if(text !== nextText){
                                                                text = nextText
                                                            }
                                                        }
                                                        function persistValue(){
                                                            if(!persistReady){
                                                                return
                                                            }
                                                            if(fieldData.type === "int"){
                                                                var parsed = parseInt(text, 10)
                                                                if(isNaN(parsed)){
                                                                    return
                                                                }
                                                                TestPageBridge.setEnvEquipmentValue(env_equipment_expander.equipmentKind, fieldData.key, parsed)
                                                                return
                                                            }
                                                            TestPageBridge.setEnvEquipmentValue(env_equipment_expander.equipmentKind, fieldData.key, text)
                                                        }
                                                        Component.onCompleted: {
                                                            syncFromState()
                                                            persistReady = true
                                                        }
                                                        onVisibleChanged: syncFromState()
                                                        onActiveFocusChanged: {
                                                            if(!activeFocus){
                                                                persistValue()
                                                            }
                                                        }
                                                        onEditingFinished: persistValue()
                                                        Connections {
                                                            target: page_root
                                                            function onStateVersionChanged() {
                                                                text_env_equipment_compact.syncFromState()
                                                            }
                                                        }
                                                    }

                                                }

                                                RowLayout{
                                                    visible: !compactTextField
                                                    Layout.fillWidth: true
                                                    spacing: 6

                                                    FluText{
                                                        text: fieldLabel(fieldData)
                                                        font: FluTextStyle.BodyStrong
                                                        Layout.fillWidth: true
                                                        wrapMode: Text.WordWrap
                                                    }

                                                    AppLoadingIndicator{
                                                        visible: fieldData.loading === true
                                                        Layout.preferredWidth: 16
                                                        Layout.preferredHeight: 16
                                                        indeterminate: true
                                                    }
                                                }

                                                FluText{
                                                    visible: !compactTextField && fieldData.type !== "terminal_list" && !!fieldDescription(fieldData)
                                                    Layout.fillWidth: true
                                                    text: fieldDescription(fieldData)
                                                    font: FluTextStyle.Caption
                                                    color: FluTheme.fontSecondaryColor
                                                    wrapMode: Text.WordWrap
                                                }

                                                FluComboBox{ /* persistence-opt-out: owner:TestPageBridge */
                                                    id: combo_env_equipment_field
                                                    visible: fieldData.type === "enum"
                                                    Layout.fillWidth: true
                                                    textRole: "label"
                                                    model: fieldData.enum_values || []
                                                    currentIndex: {
                                                        var options = fieldData.enum_values || []
                                                        var currentValue = fieldData.value === undefined || fieldData.value === null ? "" : (fieldData.value + "")
                                                        for(var i = 0; i < options.length; i++){
                                                            var option = options[i]
                                                            var optionValue = (option && option.value !== undefined) ? (option.value + "") : (option + "")
                                                            if(optionValue === currentValue){
                                                                return i
                                                            }
                                                        }
                                                        return -1
                                                    }
                                                    onActivated: {
                                                        if(currentIndex >= 0){
                                                            var option = (fieldData.enum_values || [])[currentIndex]
                                                            var optionValue = (option && option.value !== undefined) ? option.value : currentText
                                                            TestPageBridge.setEnvEquipmentValue(env_equipment_expander.equipmentKind, fieldData.key, optionValue)
                                                        }
                                                    }
                                                }

                                                ColumnLayout{
                                                    visible: fieldData.type === "terminal_list"
                                                    Layout.fillWidth: true
                                                    spacing: 6

                                                    RowLayout{
                                                        Layout.fillWidth: true
                                                        spacing: 8
                                                        FluText{
                                                            text: fieldDescription(fieldData)
                                                            visible: !!fieldDescription(fieldData)
                                                            font: FluTextStyle.Caption
                                                            color: FluTheme.fontSecondaryColor
                                                            Layout.fillWidth: true
                                                            wrapMode: Text.WordWrap
                                                        }
                                                        FluIconButton{
                                                            iconSource: FluentIcons.Add
                                                            iconSize: 14
                                                            width: 30
                                                            height: 30
                                                            text: qsTr("Add terminal")
                                                            onClicked: {
                                                                TestPageBridge.addEnvRelayTerminal(env_equipment_expander.equipmentKind)
                                                            }
                                                        }
                                                    }

                                                    Repeater{
                                                        model: terminalRows()
                                                        RowLayout{
                                                            Layout.fillWidth: true
                                                            spacing: 8
                                                            property var terminalData: modelData || {}
                                                            property int rowIndex: index

                                                            FluText{
                                                                text: qsTr("Terminal")
                                                                font: FluTextStyle.Caption
                                                                color: FluTheme.fontSecondaryColor
                                                            }

                                                            FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                                                                Layout.preferredWidth: 54
                                                                Layout.maximumWidth: 54
                                                                cleanEnabled: false
                                                                text: terminalData.terminal === undefined ? "1" : (terminalData.terminal + "")
                                                                onEditingFinished: {
                                                                    var parsed = parseInt(text, 10)
                                                                    if(!isNaN(parsed)){
                                                                        TestPageBridge.setEnvRelayTerminalValue(env_equipment_expander.equipmentKind, rowIndex, "terminal", parsed)
                                                                    }
                                                                }
                                                            }

                                                            FluText{
                                                                text: qsTr("Wiring mode")
                                                                font: FluTextStyle.Caption
                                                                color: FluTheme.fontSecondaryColor
                                                            }

                                                            FluComboBox{ /* persistence-opt-out: owner:TestPageBridge */
                                                                Layout.preferredWidth: 82
                                                                Layout.maximumWidth: 82
                                                                model: fieldData.enum_values || ["NO", "NC"]
                                                                currentIndex: {
                                                                    var options = fieldData.enum_values || ["NO", "NC"]
                                                                    return options.indexOf(terminalData.mode || "NO")
                                                                }
                                                                onActivated: {
                                                                    TestPageBridge.setEnvRelayTerminalValue(env_equipment_expander.equipmentKind, rowIndex, "mode", currentText)
                                                                }
                                                            }

                                                            FluText{
                                                                text: qsTr("Press seconds")
                                                                font: FluTextStyle.Caption
                                                                color: FluTheme.fontSecondaryColor
                                                            }

                                                            FluTextBox{ /* persistence-opt-out: owner:TestPageBridge */
                                                                Layout.preferredWidth: 72
                                                                Layout.maximumWidth: 72
                                                                cleanEnabled: false
                                                                text: terminalData.press_seconds === undefined ? "1" : (terminalData.press_seconds + "")
                                                                onEditingFinished: {
                                                                    var parsed = parseFloat(text)
                                                                    if(!isNaN(parsed)){
                                                                        TestPageBridge.setEnvRelayTerminalValue(env_equipment_expander.equipmentKind, rowIndex, "press_seconds", parsed)
                                                                    }
                                                                }
                                                            }

                                                            FluIconButton{
                                                                iconSource: FluentIcons.Delete
                                                                iconSize: 14
                                                                width: 30
                                                                height: 30
                                                                enabled: terminalRows().length > 1
                                                                text: qsTr("Remove terminal")
                                                                onClicked: {
                                                                    TestPageBridge.removeEnvRelayTerminal(env_equipment_expander.equipmentKind, rowIndex)
                                                                }
                                                            }
                                                        }
                                                    }
                                                }

                                                FluDivider{
                                                    Layout.fillWidth: true
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    FluFilledButton{
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: footerHeight
        text: RunBridge.isRunning ? qsTr("Stop") : qsTr("Start")
        onClicked: {
            if(RunBridge.isRunning){
                RunBridge.stopRun()
                return
            }
            if(RunBridge.startRun()){
                ItemsOriginal.startPageByItem({ title: qsTr("Run"), url: "qrc:/example/qml/page/T_Run.qml" })
            }
        }
    }

    FluContentDialog{
        id: dialog_suite
        title: suiteDialogMode === "update" ? qsTr("Update Test Suite") : qsTr("Save Test Suite")
        buttonFlags: FluContentDialogType.NegativeButton | FluContentDialogType.PositiveButton
        negativeText: qsTr("Cancel")
        positiveText: qsTr("Save")
        positiveDisabled: suiteDraftName.trim() === ""
        onPositiveClicked: {
            var visibility = suiteDraftShared ? "shared" : "private"
            var name = suiteDraftName.trim()
            if(suiteDialogMode === "update"){
                TestPageBridge.updateActiveSuite(name, suiteDraftDescription, visibility)
            }else if(suiteDialogMode === "copy" && suiteSelectedRow){
                TestPageBridge.copySuite(suiteSelectedRow.id, name, visibility)
            }else{
                TestPageBridge.createSuite(name, suiteDraftDescription, visibility)
            }
        }
        contentDelegate: Component{
            ColumnLayout{
                width: dialog_suite.width
                spacing: 10
                FluText{ text: qsTr("Suite name"); font: FluTextStyle.BodyStrong }
                FluTextBox{
                    Layout.fillWidth: true
                    text: page_root.suiteDraftName
                    placeholderText: qsTr("Enter a suite name")
                    onTextChanged: page_root.suiteDraftName = text
                } // persistence-opt-out: transient suite dialog
                FluText{ text: qsTr("Description"); font: FluTextStyle.BodyStrong }
                FluMultilineTextBox{
                    Layout.fillWidth: true
                    text: page_root.suiteDraftDescription
                    placeholderText: qsTr("Optional description")
                    onTextChanged: page_root.suiteDraftDescription = text
                } // persistence-opt-out: transient suite dialog
                FluCheckBox{
                    text: qsTr("Share with other users")
                    checked: page_root.suiteDraftShared
                    onClicked: page_root.suiteDraftShared = checked
                }
            }
        }
    }

    FluContentDialog{
        id: dialog_delete_suite
        title: qsTr("Delete Test Suite")
        message: qsTr("Delete the selected test suite? This cannot be undone.")
        buttonFlags: FluContentDialogType.NegativeButton | FluContentDialogType.PositiveButton
        negativeText: qsTr("Cancel")
        positiveText: qsTr("Delete")
        onPositiveClicked: if(suiteSelectedRow) TestPageBridge.deleteSuite(suiteSelectedRow.id)
    }

    FluContentDialog{
        id: dialog_validation
        title: qsTr("Required Parameters")
        buttonFlags: FluContentDialogType.PositiveButton
        positiveText: qsTr("OK")
        contentDelegate: Component{
            Item{
                implicitWidth: dialog_validation.width
                implicitHeight: validation_message_text.implicitHeight + 16
                FluText{
                    id: validation_message_text
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.leftMargin: 20
                    anchors.rightMargin: 20
                    anchors.topMargin: 4
                    text: page_root.validationDialogMessage
                    font: FluTextStyle.Body
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
