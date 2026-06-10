import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Signal, QByteArray, QCoreApplication  # noqa: E402

import chiaki_replica as cr  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeReplica(QObject):
    screenShotReady = Signal(QByteArray)
    recordedEventCaptured = Signal("QVariantMap")
    sessionConnectedtoPsChanged = Signal()
    buttonChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self._connected = False

    def property(self, name):
        if name == "sessionConnectedtoPs":
            return self._connected
        return super().property(name)


def test_latest_state_defaults():
    s = cr.LatestState()
    snap = s.snapshot()
    assert snap["screenshot"] == b""
    assert snap["session_connected"] is False
    assert snap["last_button"] == ""
    assert snap["events"] == []
    assert snap["replica_available"] is False


def test_connect_replica_updates_cache(qapp):
    s = cr.LatestState()
    rep = FakeReplica()
    cr.connect_replica(rep, s)

    rep.screenShotReady.emit(QByteArray(b"PNGDATA"))
    rep.recordedEventCaptured.emit({"source": "controller", "buttons": 5})
    rep._connected = True
    rep.sessionConnectedtoPsChanged.emit()
    rep.buttonChanged.emit("cross")

    snap = s.snapshot()
    assert snap["screenshot"] == b"PNGDATA"
    assert snap["session_connected"] is True
    assert snap["last_button"] == "cross"
    assert snap["events"][-1]["source"] == "controller"
    assert snap["events"][-1]["buttons"] == 5


def test_event_ring_buffer_caps(qapp):
    s = cr.LatestState(max_events=3)
    rep = FakeReplica()
    cr.connect_replica(rep, s)
    for i in range(5):
        rep.recordedEventCaptured.emit({"i": i})
    snap = s.snapshot()
    assert len(snap["events"]) == 3
    assert snap["events"][0]["i"] == 2
    assert snap["events"][-1]["i"] == 4


def test_screenshot_sequence_increments(qapp):
    s = cr.LatestState()
    rep = FakeReplica()
    cr.connect_replica(rep, s)
    assert s.snapshot()["screenshot_seq"] == 0
    rep.screenShotReady.emit(QByteArray(b"a"))
    rep.screenShotReady.emit(QByteArray(b"bb"))
    snap = s.snapshot()
    assert snap["screenshot_seq"] == 2
    assert snap["screenshot"] == b"bb"


def test_press_reports_failure_when_not_started():
    # Manager never started -> no Qt loop drains the command -> press must
    # report failure (not a false success).
    m = cr.ReplicaManager("local:none", "RemoteController")
    result = m.press("cross", timeout=0.3)
    assert result["ok"] is False
