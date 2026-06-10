import QtQuick
import QtTest

Item {
    id: root
    width: 800
    height: 700

    readonly property string windowUrl: Qt.resolvedUrl("../qml/Main.qml")
    // Unroutable url so RemoteTaskClient stays disconnected during UI tests.
    readonly property var initialProps: ({ envServerUrl: "tcp://127.0.0.1:1", visible: false })

    TestCase {
        name: "MainWindowTests"
        when: windowShown

        function test_componentCompiles() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            verify(comp.status === Component.Ready, comp.errorString())
            comp.destroy()
        }

        function test_startSessionEnabledWithoutLiveSession() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            let startBtn = findChild(win, "startSessionBtn")
            verify(!!startBtn, "Object exists")
            compare(win.chiakiConnected, false)
            tryCompare(startBtn, "enabled", true)
            win.chiakiConnected = true
            tryCompare(startBtn, "enabled", false)
            win.destroy()
            comp.destroy()
        }

        function test_connectDisabledWhenSessionLive() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            let connectBtn = findChild(win, "connectBtn")
            verify(!!connectBtn, "Object exists")
            win.chiakiConnected = true
            tryCompare(connectBtn, "enabled", false)
            win.destroy()
            comp.destroy()
        }

        function test_importExportDisabledWhileServerOffline() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            // The QtRO url is unroutable, so the service replica never
            // becomes valid and the file actions stay disabled.
            let importBtn = findChild(win, "importBtn")
            let exportBtn = findChild(win, "exportBtn")
            verify(!!importBtn, "Object exists")
            verify(!!exportBtn, "Object exists")
            tryCompare(importBtn, "enabled", false)
            tryCompare(exportBtn, "enabled", false)
            win.destroy()
            comp.destroy()
        }

        function test_dynamicModeSwitchesViews() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            let dynamicCheck = findChild(win, "dynamicCheck")
            let dynList = findChild(win, "dynList")
            let tree = findChild(win, "tree")
            verify(!!dynamicCheck, "Object exists")
            verify(!!dynList, "Object exists")
            verify(!!tree, "Object exists")

            dynamicCheck.checked = true
            tryCompare(dynList, "visible", true)
            tryCompare(tree, "visible", false)

            dynamicCheck.checked = false
            tryCompare(dynList, "visible", false)
            tryCompare(tree, "visible", true)
            win.destroy()
            comp.destroy()
        }

        function test_dynamicListFollowsAvailableTasks() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            let dynamicCheck = findChild(win, "dynamicCheck")
            let dynList = findChild(win, "dynList")
            verify(!!dynamicCheck, "Object exists")
            verify(!!dynList, "Object exists")

            dynamicCheck.checked = true
            win.availableList = [
                { id: "t1", title: qsTr("open pack"), start: "hut store", end: "" },
                { id: "t2", title: qsTr("go to store"), start: "", end: "hut store" }
            ]
            tryCompare(dynList, "count", 2)
            win.availableList = []
            tryCompare(dynList, "count", 0)
            win.destroy()
            comp.destroy()
        }

        function test_logAppendsToLogArea() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            let logArea = findChild(win, "logArea")
            verify(!!logArea, "Object exists")
            win.log("hello from the test")
            tryVerify(function() { return logArea.text.indexOf("hello from the test") >= 0 })
            win.destroy()
            comp.destroy()
        }

        function test_serverIndicatorOfflineByDefault() {
            let comp = Qt.createComponent(root.windowUrl)
            tryCompare(comp, "status", Component.Ready)
            let win = comp.createObject(null, root.initialProps)
            verify(!!win, "Object exists")
            let label = findChild(win, "serverStateLabel")
            verify(!!label, "Object exists")
            tryCompare(label, "text", qsTr("server ✗"))
            win.destroy()
            comp.destroy()
        }
    }
}
