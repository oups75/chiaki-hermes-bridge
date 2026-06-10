import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Soloway.QtTaskTree

// Editor dialog for a task (goal) or a step (PS5 action). Handles both create and
// edit via `mode` + `targetId`. Writes through the bound TaskTreeModel.
Dialog {
    id: dlg

    required property TaskTreeModel model
    property string mode: "step"      // "task" | "step"
    property string targetId: ""       // empty => create
    property string parentId: ""       // parent task for a new step

    readonly property var buttonNames: [
        "cross", "circle", "box", "triangle",
        "dpad_up", "dpad_down", "dpad_left", "dpad_right",
        "l1", "r1", "l3", "r3", "options", "touchpad", "ps", "none"
    ]
    readonly property var stepTypes: ["button", "wait", "scene"]

    // Group=0, Manual=2 (mirror TaskTree::Type).
    readonly property int kGroup: 0
    readonly property int kManual: 2

    parent: Overlay.overlay
    x: Math.round((parent.width - width) / 2)
    y: Math.round((parent.height - height) / 2)
    width: 380
    modal: true
    standardButtons: Dialog.Save | Dialog.Cancel
    title: (targetId === "" ? qsTr("New ") : qsTr("Edit "))
           + (mode === "task" ? qsTr("task") : qsTr("step"))

    function openNewTask() { mode = "task"; targetId = ""; parentId = ""; _reset(); open() }
    function openEditTask(id) { mode = "task"; targetId = id; _load(id); open() }
    function openNewStep(pid) { mode = "step"; targetId = ""; parentId = pid; _reset(); open() }
    function openEditStep(id) { mode = "step"; targetId = id; _load(id); open() }

    property var _loadedPayload: ({})

    function _reset() {
        nameField.text = ""
        buttonCombo.currentIndex = 0
        typeCombo.currentIndex = 0
        waitSpin.value = 0
        sceneField.text = ""
        startSceneField.text = ""
        endSceneField.text = ""
        _loadedPayload = ({})
    }
    function _load(id) {
        const info = model.taskInfo(id)
        nameField.text = info.title || ""
        const p = info.payload || ({})
        _loadedPayload = p
        buttonCombo.currentIndex = Math.max(0, buttonNames.indexOf(p.button || "none"))
        typeCombo.currentIndex = Math.max(0, stepTypes.indexOf(p.type || "button"))
        waitSpin.value = p.wait_ms || 0
        sceneField.text = p.scene || ""
        startSceneField.text = p.start_scene || ""
        endSceneField.text = p.end_scene || ""
    }

    onAccepted: {
        if (mode === "task") {
            if (targetId === "") {
                model.addTask("", nameField.text, kGroup, {
                    "mode": "Sequential", "source": "user", "approved": true,
                    "start_scene": startSceneField.text, "end_scene": endSceneField.text
                })
            } else {
                // Merge scenes into the existing payload (keep key/source/approved).
                const p = Object.assign({}, _loadedPayload)
                p.start_scene = startSceneField.text
                p.end_scene = endSceneField.text
                model.updateTask(targetId, { "title": nameField.text, "payload": p })
            }
        } else {
            const payload = {
                "button": buttonCombo.currentText,
                "type": typeCombo.currentText,
                "wait_ms": waitSpin.value,
                "scene": sceneField.text
            }
            if (targetId === "")
                model.addTask(parentId, nameField.text, kManual, payload)
            else
                model.updateTask(targetId, { "title": nameField.text, "payload": payload })
        }
    }

    contentItem: ColumnLayout {
        spacing: 8

        Label { text: dlg.mode === "task" ? qsTr("Goal") : qsTr("Step name") }
        TextField {
            id: nameField
            Layout.fillWidth: true
            placeholderText: dlg.mode === "task" ? qsTr("e.g. open pack") : qsTr("e.g. press cross")
        }

        // Task preconditions: page it runs from / page it leaves you on.
        GridLayout {
            visible: dlg.mode === "task"
            columns: 2
            columnSpacing: 8
            rowSpacing: 6
            Layout.fillWidth: true

            Label { text: qsTr("Start scene") }
            TextField {
                id: startSceneField
                placeholderText: qsTr("page this task runs from (e.g. hut store)")
                Layout.fillWidth: true
            }
            Label { text: qsTr("End scene") }
            TextField {
                id: endSceneField
                placeholderText: qsTr("page after it completes (optional)")
                Layout.fillWidth: true
            }
        }

        GridLayout {
            visible: dlg.mode === "step"
            columns: 2
            columnSpacing: 8
            rowSpacing: 6
            Layout.fillWidth: true

            Label { text: qsTr("Button") }
            ComboBox {
                id: buttonCombo
                model: dlg.buttonNames
                Layout.fillWidth: true
            }
            Label { text: qsTr("Type") }
            ComboBox {
                id: typeCombo
                model: dlg.stepTypes
                Layout.fillWidth: true
            }
            Label { text: qsTr("Wait (ms)") }
            SpinBox {
                id: waitSpin
                from: 0; to: 60000; stepSize: 50
                editable: true
                Layout.fillWidth: true
            }
            Label { text: qsTr("Scene") }
            TextField {
                id: sceneField
                placeholderText: qsTr("expected scene label (optional)")
                Layout.fillWidth: true
            }
        }
    }
}
