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

    function log(line) { logArea.append(line) }
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
            }
        }
    }

    Connections {
        target: bridge
        function onRunOutput(line) { win.log(line) }
        function onRunFinished(ok) { win.log(ok ? qsTr("✓ task finished") : qsTr("✗ task failed")) }
        function onErrorOccurred(msg) { win.log("! " + msg) }
    }

    StepEditor { id: editor; model: taskModel }

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
                    }
                }
                ToolButton {
                    text: qsTr("Export JSON")
                    onClicked: win.log(bridge.exportJson(taskModel, win.ns)
                                       ? qsTr("exported to %1/tasks.json").arg(win.ns)
                                       : qsTr("export failed"))
                }
                Item { Layout.fillWidth: true }
                ToolButton { text: qsTr("+ Task"); onClicked: editor.openNewTask() }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            TreeView {
                id: tree
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

                    contentItem: RowLayout {
                        spacing: 6

                        Label {
                            text: del.title
                            font.bold: del.isTask
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        // Editable lifecycle status (tap to change), tasks only.
                        TaskStatusChip {
                            visible: del.isTask
                            status: del.statusName
                            TapHandler {
                                onTapped: {
                                    statusMenu.targetId = del.taskId
                                    statusMenu.popup()
                                }
                            }
                        }
                        // Step button chip.
                        TaskStatusChip {
                            visible: !del.isTask && del.payload && del.payload.button
                            status: del.payload && del.payload.button ? del.payload.button : ""
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
