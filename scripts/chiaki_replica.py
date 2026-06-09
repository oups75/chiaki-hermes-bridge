#!/usr/bin/env python3
"""Persistent QtRO replica of the Chiaki RemoteController.

Holds a single long-lived QRemoteObjectNode + dynamic replica on a dedicated
Qt thread and mirrors the controller's signals into a thread-safe latest-state
cache. The asyncio MCP server reads that cache (and marshals control requests
onto the Qt thread) instead of spawning a gateway subprocess — and reconnecting
the replica — on every call.

Only the existing RemoteController signals are consumed: screenShotReady,
recordedEventCaptured, sessionConnectedtoPsChanged (+ sessionConnectedtoPs
property) and buttonChanged.
"""

import queue
import threading
from collections import deque
from typing import Any, Callable, Optional

_QAPP_REF = None  # keep QCoreApplication alive for the process lifetime


class LatestState:
    """Thread-safe newest-value cache fed by the Qt thread, read by asyncio."""

    def __init__(self, max_events: int = 256):
        self._lock = threading.Lock()
        self._screenshot: bytes = b""
        self._screenshot_seq: int = 0
        self._events: deque = deque(maxlen=max_events)
        self._session_connected: bool = False
        self._last_button: str = ""
        self._replica_available: bool = False

    def set_screenshot(self, data: Any) -> None:
        with self._lock:
            self._screenshot = bytes(data) if data else b""
            self._screenshot_seq += 1

    def add_event(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    def set_session_connected(self, value: Any) -> None:
        with self._lock:
            self._session_connected = bool(value)

    def set_last_button(self, button: Any) -> None:
        with self._lock:
            self._last_button = str(button)

    def set_replica_available(self, value: Any) -> None:
        with self._lock:
            self._replica_available = bool(value)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "screenshot": self._screenshot,
                "screenshot_seq": self._screenshot_seq,
                "events": list(self._events),
                "session_connected": self._session_connected,
                "last_button": self._last_button,
                "replica_available": self._replica_available,
            }

    def recent_events(self, count: int = 50) -> list:
        with self._lock:
            return list(self._events)[-count:]


def _coerce_event(event: Any) -> dict:
    try:
        data = dict(event)
    except Exception:
        return {"raw": event}
    shot = data.get("screenshot")
    if shot is not None and not isinstance(shot, (bytes, bytearray)):
        try:
            data["screenshot"] = bytes(shot)
        except Exception:
            pass
    return data


def connect_replica(replica, state: LatestState) -> dict:
    """Wire RemoteController replica signals into the latest-state cache.

    Returns the slot closures so callers can keep references alive (PySide6
    requires the bound slots to outlive the connection).
    """

    def on_shot(data):
        state.set_screenshot(bytes(data) if data is not None else b"")

    def on_event(event):
        state.add_event(_coerce_event(event))

    def on_session(*_args):
        try:
            value = replica.property("sessionConnectedtoPs")
        except Exception:
            value = None
        state.set_session_connected(value)

    def on_button(button):
        state.set_last_button(button)

    replica.screenShotReady.connect(on_shot)
    replica.recordedEventCaptured.connect(on_event)
    replica.sessionConnectedtoPsChanged.connect(on_session)
    replica.buttonChanged.connect(on_button)

    return {
        "on_shot": on_shot,
        "on_event": on_event,
        "on_session": on_session,
        "on_button": on_button,
    }


class ReplicaManager:
    """Owns the persistent replica on a dedicated Qt thread."""

    def __init__(self, remote_url: str, remote_name: str = "RemoteController",
                 max_events: int = 256, poll_ms: int = 1000):
        self.remote_url = remote_url
        self.remote_name = remote_name
        self.poll_ms = poll_ms
        self.state = LatestState(max_events)
        self.replica = None
        self._thread: Optional[threading.Thread] = None
        self._app = None
        self._node = None
        self._conns: dict = {}
        self._cmd_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._ready = threading.Event()
        self._loop = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, ready_timeout: float = 5.0) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="chiaki-replica", daemon=True)
        self._thread.start()
        return self._ready.wait(ready_timeout)

    def stop(self, join_timeout: float = 3.0) -> None:
        app = self._app
        if app is not None:
            self._post(app.quit)
        if self._thread:
            self._thread.join(join_timeout)

    def _run(self) -> None:
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QTimer, QUrl
        from PySide6.QtRemoteObjects import QRemoteObjectNode

        self._app = QCoreApplication.instance() or QCoreApplication([])
        self._node = QRemoteObjectNode()
        self._node.connectToNode(QUrl(self.remote_url))
        self.replica = self._node.acquireDynamic(self.remote_name)

        # A dynamic replica has no dynamic signals until it is initialized
        # (the metaobject is assigned on the peer handshake), so defer the
        # signal wiring to the initialized signal; poll covers any race.
        try:
            self.replica.initialized.connect(self._wire)
        except Exception:
            pass
        if self.replica.isInitialized():
            self._wire()

        # Poll fallback: acquireDynamic property-change delivery is unreliable,
        # and re-acquire/availability needs periodic refresh.
        timer = QTimer()
        timer.setInterval(self.poll_ms)
        timer.timeout.connect(self._poll)
        timer.start()

        # Drain control requests marshalled from other threads onto this loop.
        cmd_timer = QTimer()
        cmd_timer.setInterval(20)
        cmd_timer.timeout.connect(self._drain_commands)
        cmd_timer.start()

        self._ready.set()
        self._app.exec()

        # Destroy all Qt objects on their owning (worker) thread. Letting Python
        # GC them from the main thread at interpreter exit corrupts Qt state and
        # segfaults, so delete explicitly here via shiboken.
        timer.stop()
        cmd_timer.stop()
        self._conns = {}
        for obj in (self._node, self._app):
            try:
                if obj is not None:
                    shiboken6.delete(obj)
            except Exception:
                pass
        self.replica = None
        self._node = None
        self._app = None

    # -- Qt-thread helpers -------------------------------------------------
    def _wire(self) -> None:
        if self._conns:
            return
        try:
            self._conns = connect_replica(self.replica, self.state)
        except Exception:
            self._conns = {}
            return
        self.state.set_replica_available(True)
        self._refresh_session()

    def _refresh_session(self) -> None:
        try:
            self.state.set_session_connected(self.replica.property("sessionConnectedtoPs"))
        except Exception:
            pass

    def _poll(self) -> None:
        try:
            available = bool(self.replica.isInitialized())
        except Exception:
            available = False
        if available and not self._conns:
            self._wire()
        self.state.set_replica_available(available)
        if available:
            self._refresh_session()

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                return
            try:
                cmd()
            except Exception:
                pass

    def _post(self, fn: Callable[[], None]) -> None:
        self._cmd_queue.put(fn)

    # -- control (marshalled onto Qt thread) -------------------------------
    def press(self, button: str, interval_ms: Optional[int] = None,
              timeout: float = 2.0) -> dict:
        """Send a button on the Qt thread and report whether the replica
        actually accepted it (initialized + setProperty succeeded)."""
        result: dict = {"ok": False, "error": "timeout"}
        done = threading.Event()

        def do():
            try:
                if self.replica is None or not self.replica.isInitialized():
                    result.update(ok=False, error="replica unavailable")
                    return
                if interval_ms is not None:
                    self.replica.setProperty("pressIntervalMs", int(interval_ms))
                accepted = bool(self.replica.setProperty("button", button))
                result.clear()
                result.update(ok=accepted)
                if not accepted:
                    result["error"] = "setProperty(button) rejected"
            except Exception as exc:  # noqa: BLE001
                result.clear()
                result.update(ok=False, error=str(exc))
            finally:
                done.set()

        self._post(do)
        if not done.wait(timeout):
            return {"ok": False, "error": "timeout"}
        return dict(result)

    def request_screenshot(self) -> None:
        from PySide6.QtCore import QMetaObject, Qt

        def do():
            QMetaObject.invokeMethod(self.replica, "requestScreenShot",
                                     Qt.ConnectionType.QueuedConnection)
        self._post(do)
