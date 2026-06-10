import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Soloway.QtTaskTree
import ChiakiTaskUi

// Chiaki PS5 task manager: load learned tasks (tasks.json) per namespace, edit
// (CRUD on tasks + steps), persist to CouchDB via the model's store, export back
// to JSON, and run a task on the live PlayStation via the gateway.
ApplicationWindow {
    id: win
    width: 660
    height: 780
    visible: true
    title: qsTr("Chiaki PS5 Task Manager")

    TaskTreeModel { id: taskModel; store: appStore }
    TaskRunner { id: runner; model: taskModel }
    ChiakiTaskBridge { id: bridge }

    readonly property string ns: nsCombo.currentText
    readonly property bool dynamicMode: dynamicCheck.checked
    property string currentPage: ""
    property var availableList: []

    // Tasks runnable from the current screen: start_scene matches (or empty), or
    // the task changes context (end_scene set) so it can navigate toward a match.
    // Only approved tasks are offered.
    function refreshAvailable() {
        const out = []
        for (const id of taskModel.rootIds()) {
            const info = taskModel.taskInfo(id)
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

    function log(line) { logArea.append(line) }
    function approve(id) {
        const info = taskModel.taskInfo(id)
        const p = info.payload || ({})
        p.approved = true
        taskModel.updateTask(id, { "payload": p })
        log(qsTr("approved %1").arg(info.title))
    }
    function refreshNamespaces() {
        const list = bridge.namespaces()
        nsCombo.model = list.length ? list : ["ps"]
    }
    Component.onCompleted: {
        refreshNamespaces()
        // Auto-load the first namespace's learned tasks on startup.
        if (nsCombo.count > 0) {
            const n = bridge.importJson(taskModel, ns)
            if (n >= 0) {
                log(qsTr("loaded %1 task(s) from %2").arg(n).arg(ns))
                tree.expandRecursively()
                bridge.watchNamespace(taskModel, ns) // live-sync newly-learned tasks
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
    }
    onDynamicModeChanged: if (dynamicMode) refreshAvailable()

    StepEditor { id: editor; model: taskModel }

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
                onTriggered: taskModel.setStatus(statusMenu.targetId, index)
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
                    Layout.preferredWidth: 140
                }
                ToolButton {
                    text: qsTr("Import")
                    onClicked: {
                        const n = bridge.importJson(taskModel, win.ns)
                        win.log(n >= 0 ? qsTr("imported %1 task(s) from %2").arg(n).arg(win.ns)
                                       : qsTr("import failed"))
                        if (n >= 0) {
                            tree.expandRecursively()
                            bridge.watchNamespace(taskModel, win.ns)
                            win.currentPage = ""
                            win.refreshAvailable()
                        }
                    }
                }
                ToolButton {
                    text: qsTr("Export JSON")
                    onClicked: win.log(bridge.exportJson(taskModel, win.ns)
                                       ? qsTr("exported to %1/tasks.json").arg(win.ns)
                                       : qsTr("export failed"))
                }
                ToolSeparator {}
                CheckBox {
                    id: dynamicCheck
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

            // Dynamic mode: flat list of tasks runnable from the current screen.
            ListView {
                id: dynList
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
                visible: !win.dynamicMode
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: taskModel
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
                                onClicked: taskModel.removeTask(del.taskId)
                            }
                        }

                        // Dropping a dragged task here nests it under this task.
                        DropArea {
                            anchors.fill: parent
                            enabled: del.isTask
                            onDropped: function (drop) {
                                const sid = dragProxy.taskId
                                if (sid && sid !== del.taskId) {
                                    taskModel.moveTask(sid, del.taskId, 0)
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
                        readOnly: true
                        wrapMode: TextArea.WrapAnywhere
                        placeholderText: qsTr("import a namespace, edit tasks, run on PS5…")
                    }
                }
            }
        }
    }
}
