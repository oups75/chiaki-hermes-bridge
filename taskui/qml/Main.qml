import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Soloway.QtTaskTree
import ChiakiTaskUi

// Chiaki PS5 task manager: load learned tasks (tasks.json) per namespace, edit
// (CRUD on tasks + steps) on the shared tree served by tasktree-mcp over Qt
// Remote Objects, export back to JSON, and run a task on the live PlayStation
// via the gateway. The TreeView binds to the live model replica.
ApplicationWindow {
    id: win
    width: 660
    height: 780
    visible: true
    title: qsTr("Chiaki PS5 Task Manager")

    // Env override from main.cpp via setInitialProperties (TASKTREE_RO_URL);
    // empty when unset — then the persisted Settings value applies.
    required property string envServerUrl

    // Persisted user configuration (QML Settings rule).
    Settings {
        id: settings
        property string serverUrl: "tcp://127.0.0.1:8792"
        property string lastNamespace: ""
        property alias dynamicMode: dynamicCheck.checked
    }

    // Filterable UI log (QT_LOGGING_RULES="soloway.taskui.ui.debug=true").
    LoggingCategory {
        id: uiLog
        name: "soloway.taskui.ui"
        defaultLogLevel: LoggingCategory.Info
    }

    RemoteTaskClient { id: remote; url: win.envServerUrl || settings.serverUrl }
    ChiakiTaskBridge { id: bridge }

    readonly property string ns: nsCombo.currentText
    readonly property bool dynamicMode: dynamicCheck.checked
    property string currentPage: ""
    property var availableList: []
    property bool chiakiConnected: false
    property string chiakiStatusMsg: qsTr("not tested")

    // Tasks runnable from the current screen: start_scene matches (or empty), or
    // the task changes context (end_scene set) so it can navigate toward a match.
    // Only approved tasks are offered.
    function refreshAvailable() {
        const out = []
        for (const id of remote.rootIds()) {
            const info = remote.taskInfo(id)
            const p = info.payload || ({})
            if (p.approved === false)
                continue
            const ss = p.start_scene || ""
            const es = p.end_scene || ""
            const match = currentPage === "" || ss === "" || ss === currentPage || es !== ""
            if (match)
                out.push({ id: id, title: info.title, start: ss, end: es })
        }
        availableList = out
    }

    // Mirror the on-screen run log into the Qt logging system.
    function log(line) {
        console.info(uiLog, line)
        logArea.append(line)
    }
    function approve(id) {
        const info = remote.taskInfo(id)
        const p = info.payload || ({})
        p.approved = true
        remote.updateTask(id, { "payload": p })
        log(qsTr("approved %1").arg(info.title))
    }
    function refreshNamespaces() {
        const list = bridge.namespaces()
        nsCombo.model = list.length ? list : ["ps"]
        // Restore the last-used namespace (Settings).
        const last = nsCombo.find(settings.lastNamespace)
        if (last >= 0)
            nsCombo.currentIndex = last
    }

    property bool autoLoaded: false
    Component.onCompleted: {
        refreshNamespaces()
        remote.connectToHost()
    }
    // Auto-load the namespace's learned tasks once the server link is up
    // (import RPCs need a live TaskService replica).
    Connections {
        target: remote
        function onConnectedChanged() {
            if (!remote.connected) {
                win.log(qsTr("task server offline (%1)").arg(remote.url))
                return
            }
            win.log(qsTr("task server connected (%1)").arg(remote.url))
            if (!win.autoLoaded && nsCombo.count > 0) {
                win.autoLoaded = true
                const n = bridge.importJson(remote, win.ns)
                if (n >= 0) {
                    win.log(qsTr("loaded %1 task(s) from %2").arg(n).arg(win.ns))
                    tree.expandRecursively()
                    bridge.watchNamespace(remote, win.ns) // live-sync newly-learned tasks
                }
            }
        }
    }

    Connections {
        target: bridge
        function onRunOutput(line) { win.log(line) }
        function onRunFinished(ok) { win.log(ok ? qsTr("✓ task finished") : qsTr("✗ task failed")) }
        function onErrorOccurred(msg) { win.log("! " + msg) }
        function onContextChanged(page) {
            win.currentPage = page
            win.log(qsTr("current screen: %1").arg(page))
            win.refreshAvailable()
        }
        function onTasksMerged(added) {
            win.log(qsTr("synced %1 newly-learned task(s)").arg(added))
            tree.expandRecursively()
            win.refreshAvailable()
        }
        function onConnectionStatus(replica, crun, msg) {
            win.chiakiConnected = replica
            win.chiakiStatusMsg = msg
            win.log(qsTr("chiaki: %1").arg(msg))
        }
    }
    onDynamicModeChanged: if (dynamicMode) refreshAvailable()

    // Confirm before closing chiaki (a live session may be active).
    Dialog {
        id: closeConfirm
        parent: Overlay.overlay
        width: 340
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        modal: true
        title: qsTr("Close chiaki?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: bridge.closeChiaki()
        contentItem: Label {
            text: qsTr("A session may be active. Close chiaki?")
            wrapMode: Text.WordWrap
        }
    }

    StepEditor { id: editor; model: remote; bridge: bridge; ns: win.ns }

    // Floating proxy carrying the dragged task id for drag-drop composition.
    Item {
        id: dragProxy
        property string taskId: ""
        width: 1; height: 1
        Drag.active: false
    }

    readonly property var statusNames: ["Todo", "Ready", "Running", "Done", "Failed", "Blocked"]

    // Shared status picker; statusMenu.targetId set before opening.
    Menu {
        id: statusMenu
        property string targetId: ""
        Repeater {
            model: win.statusNames
            delegate: MenuItem {
                required property int index
                required property string modelData
                text: modelData
                onTriggered: remote.setStatus(statusMenu.targetId, index)
            }
        }
    }

    Page {
        anchors.fill: parent

        header: ToolBar {
            RowLayout {
                anchors.fill: parent
                spacing: 6

                Label { text: qsTr("Namespace"); Layout.leftMargin: 8 }
                ComboBox {
                    id: nsCombo
                    objectName: "nsCombo"
                    Layout.preferredWidth: 140
                    onActivated: settings.lastNamespace = currentText
                }
                ToolButton {
                    objectName: "importBtn"
                    text: qsTr("Import")
                    enabled: remote.connected
                    onClicked: {
                        const n = bridge.importJson(remote, win.ns)
                        win.log(n >= 0 ? qsTr("imported %1 task(s) from %2").arg(n).arg(win.ns)
                                       : qsTr("import failed"))
                        if (n >= 0) {
                            tree.expandRecursively()
                            bridge.watchNamespace(remote, win.ns)
                            win.currentPage = ""
                            win.refreshAvailable()
                        }
                    }
                }
                ToolButton {
                    objectName: "exportBtn"
                    text: qsTr("Export JSON")
                    enabled: remote.connected
                    onClicked: win.log(bridge.exportJson(remote, win.ns)
                                       ? qsTr("exported to %1/tasks.json").arg(win.ns)
                                       : qsTr("export failed"))
                }
                ToolSeparator {}
                CheckBox {
                    id: dynamicCheck
                    objectName: "dynamicCheck"
                    text: qsTr("Dynamic")
                    ToolTip.text: qsTr("Show only tasks runnable from the current screen")
                    ToolTip.visible: hovered
                }
                ToolButton {
                    text: qsTr("Refresh ctx")
                    visible: win.dynamicMode
                    onClicked: { win.log(qsTr("classifying current screen…")); bridge.classify(win.ns) }
                }
                Label {
                    visible: win.dynamicMode
                    text: win.currentPage ? qsTr("screen: %1").arg(win.currentPage) : qsTr("screen: ?")
                    font.italic: true
                }
                Item { Layout.fillWidth: true }
                ToolButton { text: qsTr("+ Task"); onClicked: editor.openNewTask() }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            // Chiaki session control strip.
            ToolBar {
                Layout.fillWidth: true
                RowLayout {
                    anchors.fill: parent
                    spacing: 6
                    Label {
                        text: "●"
                        font.pixelSize: 16
                        Layout.leftMargin: 8
                        color: win.chiakiConnected ? "#2e7d32"
                             : bridge.chiakiRunning ? "#ef6c00" : "#c62828"
                    }
                    Label { text: qsTr("Chiaki: %1").arg(win.chiakiStatusMsg); Layout.fillWidth: true }
                    // Task-server (QtRO) link state.
                    Label {
                        objectName: "serverStateLabel"
                        text: remote.connected ? qsTr("server ✓") : qsTr("server ✗")
                        color: remote.connected ? "#2e7d32" : "#c62828"
                        ToolTip.text: remote.url
                        ToolTip.visible: serverHover.hovered
                        HoverHandler { id: serverHover }
                    }
                    ToolButton { objectName: "testBtn"; text: qsTr("Test")
                                 onClicked: { win.log(qsTr("testing connection…")); bridge.testConnection(win.ns) } }
                    ToolButton { objectName: "connectBtn"; text: qsTr("Connect")
                                 enabled: bridge.chiakiRunning && !win.chiakiConnected
                                 ToolTip.text: qsTr("Wait for the PS stream to come up")
                                 ToolTip.visible: hovered
                                 onClicked: { win.log(qsTr("waiting for PS session…")); bridge.connectSession(win.ns) } }
                    ToolButton { objectName: "startSessionBtn"; text: qsTr("Start session")
                                 enabled: !win.chiakiConnected
                                 ToolTip.text: qsTr("Discover the PS5, wake it if needed, stream directly (closes an idle chiaki)")
                                 ToolTip.visible: hovered
                                 onClicked: { win.log(qsTr("discovering PS5…")); bridge.startSession(win.ns) } }
                    ToolButton { objectName: "launchBtn"; text: qsTr("Launch")
                                 enabled: !bridge.chiakiRunning
                                 onClicked: { win.log(qsTr("launching chiaki…")); bridge.launchChiaki() } }
                    ToolButton { objectName: "restartBtn"; text: qsTr("Restart")
                                 enabled: bridge.chiakiRunning
                                 ToolTip.text: qsTr("Close and relaunch chiaki (recovers a dead stream)")
                                 ToolTip.visible: hovered
                                 onClicked: { win.log(qsTr("restarting chiaki…")); bridge.restartChiaki() } }
                    ToolButton { objectName: "closeBtn"; text: qsTr("Close")
                                 enabled: bridge.chiakiRunning
                                 onClicked: win.chiakiConnected ? closeConfirm.open() : bridge.closeChiaki() }
                }
            }

            // Dynamic mode: flat list of tasks runnable from the current screen.
            ListView {
                id: dynList
                objectName: "dynList"
                visible: win.dynamicMode
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: win.availableList
                spacing: 2
                header: ItemDelegate {
                    width: ListView.view.width
                    enabled: false
                    text: win.currentPage
                          ? qsTr("Available from \"%1\" (%2)").arg(win.currentPage).arg(dynList.count)
                          : qsTr("All approved tasks (%1) — Refresh ctx to filter by screen").arg(dynList.count)
                }
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width
                    contentItem: RowLayout {
                        spacing: 6
                        Label {
                            text: modelData.title
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        TaskStatusChip {
                            visible: modelData.start
                            status: modelData.start ? modelData.start : ""
                        }
                        Label {
                            visible: modelData.end
                            text: modelData.end ? "→ " + modelData.end : ""
                            color: "#888888"
                            font.italic: true
                        }
                        ToolButton {
                            text: "▶"
                            ToolTip.text: qsTr("Run on PS5")
                            ToolTip.visible: hovered
                            onClicked: { win.log(qsTr("▶ run-task %1").arg(modelData.title));
                                         bridge.runTask(modelData.title, win.ns) }
                        }
                    }
                }
            }

            TreeView {
                id: tree
                objectName: "tree"
                visible: !win.dynamicMode
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: remote.model
                selectionModel: ItemSelectionModel {}

                // Single column spans the full view width so rows fit the window.
                columnWidthProvider: function (column) { return width }
                onWidthChanged: forceLayout()

                delegate: TreeViewDelegate {
                    id: del
                    required property string taskId
                    required property string title
                    required property string typeName
                    required property string statusName
                    required property var payload

                    readonly property bool isTask: typeName === "Group"

                    contentItem: Item {
                        id: rowRoot
                        implicitHeight: rowLay.implicitHeight

                        RowLayout {
                            id: rowLay
                            anchors.fill: parent
                            spacing: 6

                            // Drag handle (tasks): drop onto another task to nest it.
                            Label {
                                visible: del.isTask
                                text: "⠿"
                                color: "#888888"
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.OpenHandCursor
                                    drag.target: dragProxy
                                    onPressed: {
                                        dragProxy.taskId = del.taskId
                                        dragProxy.parent = rowRoot
                                        dragProxy.x = 0; dragProxy.y = 0
                                        dragProxy.Drag.active = true
                                    }
                                    onReleased: { dragProxy.Drag.drop(); dragProxy.Drag.active = false }
                                }
                            }
                            Label {
                                text: del.title
                                font.bold: del.isTask
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            // Pending badge for unapproved learned tasks.
                            TaskStatusChip {
                                visible: del.isTask && del.payload && del.payload.approved === false
                                status: "Pending"
                            }
                            // Editable lifecycle status (tap to change), tasks only.
                            TaskStatusChip {
                                visible: del.isTask
                                status: del.statusName
                                TapHandler {
                                    onTapped: { statusMenu.targetId = del.taskId; statusMenu.popup() }
                                }
                            }
                            // Step button chip.
                            TaskStatusChip {
                                visible: !del.isTask && del.payload && del.payload.button
                                status: del.payload && del.payload.button ? del.payload.button : ""
                            }
                            ToolButton {
                                text: "✓"
                                visible: del.isTask && del.payload && del.payload.approved === false
                                ToolTip.text: qsTr("Approve")
                                ToolTip.visible: hovered
                                onClicked: win.approve(del.taskId)
                            }
                            ToolButton {
                                text: "▶"
                                visible: del.isTask
                                ToolTip.text: qsTr("Run on PS5")
                                ToolTip.visible: hovered
                                onClicked: { win.log(qsTr("▶ run-task %1").arg(del.title));
                                             bridge.runTask(del.title, win.ns) }
                            }
                            ToolButton {
                                text: "＋"
                                visible: del.isTask
                                ToolTip.text: qsTr("Add step")
                                ToolTip.visible: hovered
                                onClicked: editor.openNewStep(del.taskId)
                            }
                            ToolButton {
                                text: "✎"
                                ToolTip.text: qsTr("Edit")
                                ToolTip.visible: hovered
                                onClicked: del.isTask ? editor.openEditTask(del.taskId)
                                                      : editor.openEditStep(del.taskId)
                            }
                            ToolButton {
                                text: "🗑"
                                ToolTip.text: qsTr("Delete")
                                ToolTip.visible: hovered
                                onClicked: remote.removeTask(del.taskId)
                            }
                        }

                        // Dropping a dragged task here nests it under this task.
                        DropArea {
                            anchors.fill: parent
                            enabled: del.isTask
                            onDropped: function (drop) {
                                const sid = dragProxy.taskId
                                if (sid && sid !== del.taskId) {
                                    remote.moveTask(sid, del.taskId, 0)
                                    win.log(qsTr("composed: moved into %1").arg(del.title))
                                }
                            }
                        }
                    }
                }
            }

            ToolSeparator { orientation: Qt.Horizontal; Layout.fillWidth: true }

            GroupBox {
                title: qsTr("Run log")
                Layout.fillWidth: true
                Layout.preferredHeight: 160
                ScrollView {
                    anchors.fill: parent
                    TextArea {
                        id: logArea
                        objectName: "logArea"
                        readOnly: true
                        wrapMode: TextArea.WrapAnywhere
                        placeholderText: qsTr("import a namespace, edit tasks, run on PS5…")
                    }
                }
            }
        }
    }
}
