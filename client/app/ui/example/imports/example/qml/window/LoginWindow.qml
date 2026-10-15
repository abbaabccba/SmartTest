import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15
import FluentUI 1.0
import "../component"

FluWindow {

    id: window
    objectName: "toolLoginWindow"
    title: accountMode ? qsTr("Account") : qsTr("Login")
    width: 400
    height: 400
    fixSize: false
    modality: Qt.ApplicationModal
    property bool accountMode: false
    property string pendingRemoveAccountId: ""
    property bool closeAfterAuthentication: false
    property string savedCredentialMask: "••••••••"

    Component.onCompleted: refreshMode({})

    onClosing: function(close) {
        textbox_password.text = AuthBridge.hasSavedCredential ? savedCredentialMask : ""
        if(AuthBridge.authBusy){
            AuthBridge.cancelAuthentication()
        }
    }

    Connections {
        target: AuthBridge
        function onAuthChanged() {
            if(window.closeAfterAuthentication && AuthBridge.authenticated){
                return
            }
            applyModeSize(AuthBridge.authenticated)
        }
        function onAuthenticationCompleted(result) {
            if(result.source === "auto"){
                return
            }
            if(!result.success){
                window.closeAfterAuthentication = false
                showError(result.message)
                textbox_password.forceActiveFocus()
                return
            }
            if(result.code === "signed_in_password_not_saved"){
                window.closeAfterAuthentication = false
                showWarning(result.message)
                return
            }
            if(window.closeAfterAuthentication){
                window.closeAfterAuthentication = false
                window.close()
            }
        }
    }

    FluContentDialog {
        id: removeAccountDialog
        title: qsTr("Remove account")
        message: qsTr("This removes the saved sign-in information on this device. SmartTest business data will not be deleted.")
        negativeText: qsTr("Cancel")
        positiveText: qsTr("Remove")
        buttonFlags: FluContentDialogType.NegativeButton | FluContentDialogType.PositiveButton
        onPositiveClicked: {
            var result = AuthBridge.removeAccount(window.pendingRemoveAccountId)
            refreshMode({})
            if(!result.success && result.message){
                showError(result.message)
            }
        }
    }

    function applyModeSize(nextAccountMode){
        var targetWidth = nextAccountMode ? 460 : 400
        var targetHeight = nextAccountMode ? 600 : 400
        window.fixSize = false
        window.minimumWidth = 0
        window.minimumHeight = 0
        window.maximumWidth = 16777215
        window.maximumHeight = 16777215
        accountMode = nextAccountMode
        window.width = targetWidth
        window.height = targetHeight
        window.fixSize = true
        window.fixWindowSize()
        if(window.visible && window.autoCenter){
            window.moveWindowToDesktopCenter()
        }
    }

    function refreshMode(argument){
        var initialUsername = ""
        if(argument && argument.username){
            initialUsername = argument.username
        }else{
            initialUsername = AuthBridge.currentUsername()
        }
        applyModeSize(AuthBridge.authenticated)
        textbox_username.updateText(initialUsername)
        textbox_password.text = AuthBridge.hasSavedCredential ? savedCredentialMask : ""
        Qt.callLater(function(){
            if(accountMode){
                btn_primary.forceActiveFocus()
            }else{
                textbox_username.forceActiveFocus()
            }
        })
    }

    function submitLogin(){
        if(textbox_username.text === ""){
            showError(qsTr("Please enter the account"))
            textbox_username.forceActiveFocus()
            return
        }
        if(textbox_password.text === ""){
            showError(qsTr("Please enter your password"))
            textbox_password.forceActiveFocus()
            return
        }
        window.closeAfterAuthentication = true
        if(AuthBridge.hasSavedCredential && textbox_password.text === savedCredentialMask){
            var savedResult = AuthBridge.loginWithSavedCredential()
            if(!savedResult.success){ window.closeAfterAuthentication = false; showError(savedResult.message) }
            else if(savedResult.code !== "authenticating"){
                window.closeAfterAuthentication = false
                window.close()
            }
            return
        }
        var result = AuthBridge.login(textbox_username.text, textbox_password.text, remember_password.checked)
        textbox_password.text = ""
        if(!result.success){
            window.closeAfterAuthentication = false
            showError(result.message)
            textbox_password.forceActiveFocus()
            return
        }
        if(result.code !== "authenticating"){
            window.closeAfterAuthentication = false
            window.close()
        }
    }

    function requestRemoveAccount(accountId){
        window.pendingRemoveAccountId = accountId
        accountPopup.close()
        removeAccountDialog.open()
    }
    onInitArgument:
        (argument)=>{
            refreshMode(argument)
        }

    ColumnLayout{
        anchors{
            left: parent.left
            right: parent.right
            verticalCenter: parent.verticalCenter
        }
        spacing: 8

        FluClip {
            id: loginHeroAvatar
            objectName: "loginHeroAvatar"
            visible: !accountMode
            Layout.preferredWidth: 88
            Layout.preferredHeight: 88
            Layout.alignment: Qt.AlignHCenter
            radius: [44, 44, 44, 44]
            Rectangle {
                anchors.fill: parent
                color: FluTheme.dark ? "#334155" : "#DCEBFA"
                FluText { anchors.centerIn: parent; text: AuthBridge.initials; font.pixelSize: 26; font.bold: true }
            }
            Image { anchors.fill: parent; source: AuthBridge.avatarUrl; visible: source.toString() !== ""; fillMode: Image.PreserveAspectCrop; cache: false }
        }

        FluButton { /* persistence-opt-out: owner:AuthBridge */
            id: account_selector
            objectName: "accountSelector"
            visible: !accountMode
            enabled: !AuthBridge.authBusy
            text: AuthBridge.currentUsername() || qsTr("Add another account")
            Layout.preferredWidth: 320
            Layout.preferredHeight: 42
            Layout.alignment: Qt.AlignHCenter
            contentItem: RowLayout {
                spacing: 8
                FluText { Layout.fillWidth: true; text: account_selector.text; elide: Text.ElideRight }
                FluIcon {
                    objectName: "accountSelectorArrow"
                    iconSource: FluentIcons.ChevronDown
                    iconSize: 12
                    color: FluTheme.fontSecondaryColor
                }
            }
            onClicked: accountPopup.open()
        }

        Popup {
            id: accountPopup
            objectName: "accountPopup"
            parent: Overlay.overlay
            width: 340
            padding: 8
            x: Math.round((window.width - width) / 2)
            y: Math.round((window.height - height) / 2)
            background: Rectangle { radius: 10; color: FluTheme.dark ? "#292C31" : "#FFFFFF"; border.color: FluTheme.dark ? "#41464E" : "#E2E5EA" }
            contentItem: Column {
                spacing: 4
                Repeater {
                    model: AuthBridge.accounts
                    delegate: FluButton {
                        width: 324; height: 54
                        contentItem: RowLayout {
                            spacing: 10
                            FluClip { Layout.preferredWidth: 34; Layout.preferredHeight: 34; radius: [17,17,17,17]
                                Rectangle { anchors.fill: parent; color: FluTheme.dark ? "#334155" : "#DCEBFA"; FluText { anchors.centerIn: parent; text: modelData.username ? modelData.username.charAt(0).toUpperCase() : "" } }
                                Image { anchors.fill: parent; source: modelData.avatarUrl; visible: source.toString() !== ""; fillMode: Image.PreserveAspectCrop }
                            }
                            FluText { Layout.fillWidth: true; text: modelData.username; font: FluTextStyle.BodyStrong; elide: Text.ElideRight }
                            FluIcon { visible: modelData.rememberPassword; iconSource: FluentIcons.Lock; iconSize: 14 }
                            Item {
                                objectName: "accountRemoveButton"
                                Layout.preferredWidth: 28
                                Layout.preferredHeight: 28
                                FluIcon { anchors.centerIn: parent; iconSource: FluentIcons.ChromeClose; iconSize: 10 }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: function(mouse) {
                                        mouse.accepted = true
                                        window.requestRemoveAccount(modelData.accountId)
                                    }
                                }
                            }
                        }
                        onClicked: {
                            accountPopup.close()
                            window.closeAfterAuthentication = true
                            var result = AuthBridge.selectAccount(modelData.accountId)
                            textbox_password.text = AuthBridge.hasSavedCredential ? savedCredentialMask : ""
                            textbox_username.updateText(modelData.username)
                            if(result.requiresPassword) {
                                window.closeAfterAuthentication = false
                                textbox_password.forceActiveFocus()
                            }
                        }
                    }
                }
                FluButton { id: addAccountAction; objectName: "addAccountAction"; width: 324; text: qsTr("Add another account")
                    onClicked: { accountPopup.close(); AuthBridge.useOtherAccount(); textbox_username.updateText(""); textbox_password.text = ""; textbox_username.forceActiveFocus() }
                }
            }
        }

        FluAutoSuggestBox{ /* persistence-opt-out: owner:AuthBridge */
            id: textbox_username
            visible: !accountMode && AuthBridge.selectedAccountId === ""
            enabled: !AuthBridge.authBusy
            items: AuthBridge.currentUsername() !== "" ? [{title: AuthBridge.currentUsername()}] : []
            placeholderText: qsTr("Please enter the account")
            Layout.preferredWidth: 320
            Layout.alignment: Qt.AlignHCenter
            onCommit: {
                textbox_password.forceActiveFocus()
            }
        }

        AppLoadingIndicator {
            visible: AuthBridge.authBusy
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            Layout.alignment: Qt.AlignHCenter
        }

        FluTextBox{ /* persistence-opt-out: sensitive */
            id: textbox_password
            objectName: "loginPasswordInput"
            visible: !accountMode && !AuthBridge.authBusy
            enabled: !AuthBridge.authBusy
            Layout.preferredWidth: 320
            placeholderText: qsTr("Please enter your password")
            echoMode:TextInput.Password
            Layout.alignment: Qt.AlignHCenter
            onCommit: {
                submitLogin()
            }
            onActiveFocusChanged: {
                if(activeFocus && text === savedCredentialMask) selectAll()
            }
        }

        RowLayout {
            objectName: "loginOptionsRow"
            visible: !accountMode && !AuthBridge.authBusy
            Layout.preferredWidth: 320
            Layout.minimumWidth: 320
            Layout.maximumWidth: 320
            Layout.alignment: Qt.AlignHCenter
            FluCheckBox { /* persistence-opt-out: owner:AuthBridge */
                id: remember_password
                objectName: "rememberPasswordCheck"
                enabled: !AuthBridge.authBusy
                text: qsTr("Save password")
                checked: AuthBridge.rememberPassword
                onClicked: AuthBridge.setRememberPassword(checked)
            }
            Item { Layout.fillWidth: true }
        }

        FluFilledButton{
            id: btn_primary
            objectName: "loginPrimaryButton"
            visible: !accountMode
            enabled: !AuthBridge.authBusy
            text: qsTr("Login")
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 320
            Layout.preferredHeight: 42
            Layout.topMargin: 6
            onClicked:{
                submitLogin()
            }
        }

        Rectangle {
                visible: accountMode
            Layout.preferredWidth: 420
            Layout.preferredHeight: 520
            Layout.alignment: Qt.AlignHCenter
            radius: 12
            color: FluTheme.dark ? "#202226" : "#F5F6F8"
            border.width: 1
            border.color: FluTheme.dark ? "#3B3F46" : "#E3E8EF"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 10

                RowLayout {
                    id: accountHeader
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    FluText { text: qsTr("Account"); font: FluTextStyle.BodyStrong }
                    Item { Layout.fillWidth: true }
                }
                FluDivider { Layout.fillWidth: true }

                RowLayout {
                    id: accountIdentityRow
                    Layout.fillWidth: true
                    Layout.preferredHeight: 76
                    Layout.topMargin: 18
                    spacing: 16
                    FluClip {
                        id: accountAvatar
                        Layout.preferredWidth: 66
                        Layout.preferredHeight: 66
                        radius: [33, 33, 33, 33]
                        Rectangle {
                            anchors.fill: parent
                            color: FluTheme.dark ? "#334155" : "#DCEBFA"
                            FluText {
                                anchors.centerIn: parent
                                text: AuthBridge.initials
                                font.pixelSize: 20
                                font.bold: true
                                color: FluTheme.dark ? "#FFFFFF" : "#1E3A5F"
                            }
                        }
                        Image {
                            anchors.fill: parent
                            source: AuthBridge.avatarUrl
                            visible: source.toString() !== ""
                            fillMode: Image.PreserveAspectCrop
                            sourceSize: Qt.size(132, 132)
                            cache: false
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        FluText {
                            Layout.fillWidth: true
                            text: AuthBridge.displayName || AuthBridge.username
                            font: FluTextStyle.Title
                            elide: Text.ElideRight
                        }
                        FluText {
                            Layout.fillWidth: true
                            text: AuthBridge.jobTitle
                            color: FluTheme.fontSecondaryColor
                            font: FluTextStyle.Caption
                            elide: Text.ElideRight
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 9
                    Rectangle {
                        id: gradeCard
                        Layout.fillWidth: true
                        Layout.preferredHeight: 66
                        radius: 9
                        color: FluTheme.dark ? "#292C31" : "#FFFFFF"
                        border.width: 1
                        border.color: FluTheme.dark ? "#41464E" : "#E2E5EA"
                        Column { anchors.fill: parent; anchors.margins: 11; spacing: 5
                            FluText { text: qsTr("Grade"); color: FluTheme.fontSecondaryColor; font.pixelSize: 8 }
                            FluText { width: parent.width; text: AuthBridge.grade; font: FluTextStyle.BodyStrong; elide: Text.ElideRight }
                        }
                    }
                    Rectangle {
                        id: departmentCard
                        Layout.fillWidth: true
                        Layout.preferredHeight: 66
                        radius: 9
                        color: FluTheme.dark ? "#292C31" : "#FFFFFF"
                        border.width: 1
                        border.color: FluTheme.dark ? "#41464E" : "#E2E5EA"
                        Column { anchors.fill: parent; anchors.margins: 11; spacing: 5
                            FluText { text: qsTr("Department"); color: FluTheme.fontSecondaryColor; font.pixelSize: 8 }
                            FluText { width: parent.width; text: AuthBridge.department; font: FluTextStyle.BodyStrong; elide: Text.ElideRight }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 24
                    spacing: 14
                    FluText { visible: AuthBridge.team !== ""; text: qsTr("Team") + ":"; color: FluTheme.fontSecondaryColor; font.pixelSize: 8 }
                    FluText { visible: AuthBridge.team !== ""; text: AuthBridge.team; elide: Text.ElideRight }
                    Item { Layout.fillWidth: true }
                }
                RowLayout {
                    visible: AuthBridge.reportsTo !== ""
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 22 : 0
                    spacing: 8
                    FluText { text: qsTr("Reports To") + ":"; color: FluTheme.fontSecondaryColor; font.pixelSize: 8 }
                    FluText { Layout.fillWidth: true; text: AuthBridge.reportsTo; elide: Text.ElideRight }
                }

                Rectangle {
                    id: productLineCard
                    Layout.fillWidth: true
                    Layout.preferredHeight: 78
                    radius: 9
                    color: FluTheme.dark ? "#292C31" : "#FFFFFF"
                    border.width: 1
                    border.color: FluTheme.dark ? "#41464E" : "#E2E5EA"
                    Column {
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6
                        FluText { text: qsTr("Product Line"); color: FluTheme.fontSecondaryColor; font.pixelSize: 8 }
                        Flow {
                            id: productLineTags
                            width: parent.width
                            spacing: 6
                            Repeater {
                                model: AuthBridge.productLines
                                delegate: Rectangle {
                                    required property string modelData
                                    width: productTagText.implicitWidth + 16
                                    height: 24
                                    radius: 6
                                    color: FluTheme.dark ? "#233650" : "#EDF4FF"
                                    FluText {
                                        id: productTagText
                                        anchors.centerIn: parent
                                        text: modelData
                                        color: FluTheme.dark ? "#A9C9F5" : "#235EA8"
                                        font.pixelSize: 8
                                    }
                                }
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }
                FluFilledButton {
                    objectName: "accountLogoutButton"
                    visible: AuthBridge.authenticated
                    Layout.fillWidth: true
                    Layout.preferredHeight: 42
                    text: qsTr("Logout")
                    onClicked: {
                        AuthBridge.logout()
                        refreshMode({username: AuthBridge.currentUsername()})
                    }
                }
            }
        }
    }
}
