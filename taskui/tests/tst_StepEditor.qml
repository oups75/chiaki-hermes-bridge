import QtQuick
import QtTest
import QtQuick.Controls

import "../qml"

Item {
    id: root
    width: 800
    height: 700

    // Duck-typed stand-in for the task API (TaskTreeModel / RemoteTaskClient).
    QtObject {
        id: fakeModel
        property var lastAdd: null
        property var lastUpdate: null
        property var info: ({
            title: "press circle",
            payload: {
                button: "circle", type: "button", wait_ms: 300, scene: "card revealed",
                start_scene: "hut store", end_scene: ""
            }
        })
        function addTask(parentId, title, type, payload) {
            lastAdd = { parentId: parentId, title: title, type: type, payload: payload }
            return "new-id"
        }
        function updateTask(id, fields) {
            lastUpdate = { id: id, fields: fields }
            return true
        }
        function taskInfo(id) { return info }
    }

    QtObject {
        id: fakeBridge
        signal expectedCaptured(string screenshot, string scene)
        function captureExpected(ns) {
            expectedCaptured("/tmp/expected.png", "hut store")
        }
    }

    Component {
        id: editorComponent
        StepEditor {
            model: fakeModel
            bridge: fakeBridge
        }
    }

    TestCase {
        name: "StepEditorTests"
        when: windowShown

        function init() {
            fakeModel.lastAdd = null
            fakeModel.lastUpdate = null
        }

        function test_openNewTaskResetsFields() {
            let dlg = createTemporaryObject(editorComponent, root)
            verify(!!dlg, "Component exists")
            dlg.openNewTask()
            let nameField = findChild(dlg.contentItem, "nameField")
            let startSceneField = findChild(dlg.contentItem, "startSceneField")
            verify(!!nameField, "Object exists")
            verify(!!startSceneField, "Object exists")
            compare(dlg.mode, "task")
            compare(nameField.text, qsTr(""))
            compare(startSceneField.text, qsTr(""))
        }

        function test_openEditStepPopulatesFields() {
            let dlg = createTemporaryObject(editorComponent, root)
            verify(!!dlg, "Component exists")
            dlg.openEditStep("step-1")
            let nameField = findChild(dlg.contentItem, "nameField")
            let buttonCombo = findChild(dlg.contentItem, "buttonCombo")
            let waitSpin = findChild(dlg.contentItem, "waitSpin")
            let sceneField = findChild(dlg.contentItem, "sceneField")
            verify(!!nameField, "Object exists")
            verify(!!buttonCombo, "Object exists")
            verify(!!waitSpin, "Object exists")
            verify(!!sceneField, "Object exists")
            compare(dlg.mode, "step")
            compare(nameField.text, qsTr("press circle"))
            compare(buttonCombo.currentText, qsTr("circle"))
            compare(waitSpin.value, 300)
            compare(sceneField.text, qsTr("card revealed"))
        }

        function test_saveNewTaskCallsAddTask() {
            let dlg = createTemporaryObject(editorComponent, root)
            verify(!!dlg, "Component exists")
            dlg.openNewTask()
            let nameField = findChild(dlg.contentItem, "nameField")
            let startSceneField = findChild(dlg.contentItem, "startSceneField")
            verify(!!nameField, "Object exists")
            verify(!!startSceneField, "Object exists")
            nameField.focus = true
            nameField.text = qsTr("my goal")
            startSceneField.focus = true
            startSceneField.text = qsTr("hut main")
            let saveButton = dlg.standardButton(Dialog.Save)
            verify(!!saveButton, "Object exists")
            mouseClick(saveButton)
            tryVerify(function() { return fakeModel.lastAdd !== null })
            compare(fakeModel.lastAdd.title, qsTr("my goal"))
            compare(fakeModel.lastAdd.type, 0)
            compare(fakeModel.lastAdd.payload.start_scene, qsTr("hut main"))
            compare(fakeModel.lastAdd.payload.source, "user")
        }

        function test_saveEditedStepCallsUpdateTask() {
            let dlg = createTemporaryObject(editorComponent, root)
            verify(!!dlg, "Component exists")
            dlg.openEditStep("step-9")
            let nameField = findChild(dlg.contentItem, "nameField")
            verify(!!nameField, "Object exists")
            nameField.focus = true
            nameField.text = qsTr("renamed step")
            let saveButton = dlg.standardButton(Dialog.Save)
            verify(!!saveButton, "Object exists")
            mouseClick(saveButton)
            tryVerify(function() { return fakeModel.lastUpdate !== null })
            compare(fakeModel.lastUpdate.id, "step-9")
            compare(fakeModel.lastUpdate.fields.title, qsTr("renamed step"))
            compare(fakeModel.lastUpdate.fields.payload.button, qsTr("circle"))
        }

        function test_captureRoutesToSelectedSlot() {
            let dlg = createTemporaryObject(editorComponent, root)
            verify(!!dlg, "Component exists")
            dlg.openNewTask()
            let expStart = findChild(dlg.contentItem, "expStart")
            verify(!!expStart, "Object exists")
            dlg.captureSlot = "start"
            fakeBridge.captureExpected("demo")
            tryVerify(function() { return !!dlg.startExp.scene })
            compare(dlg.startExp.scene, "hut store")
            compare(expStart.value.screenshot, "/tmp/expected.png")
            // The other slots stay untouched.
            verify(!dlg.endExp.scene)
            verify(!dlg.stepExp.scene)
        }

        function test_clearRoutesToSelectedSlot() {
            let dlg = createTemporaryObject(editorComponent, root)
            verify(!!dlg, "Component exists")
            dlg.openNewTask()
            dlg.captureSlot = "start"
            fakeBridge.captureExpected("demo")
            tryVerify(function() { return !!dlg.startExp.scene })
            dlg.captureSlot = "start"
            dlg._assignCapture({})
            tryVerify(function() { return !dlg.startExp.scene })
            compare(dlg.startExp.scene, undefined)
        }
    }
}
