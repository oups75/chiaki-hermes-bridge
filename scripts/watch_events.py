#!/usr/bin/env python3
"""Live event printer for the Chiaki RemoteController task recorder.

Connects to the persistent QtRO replica, arms recording (startTaskRecording),
and prints every recordedEventCaptured in real time. Use a controller (or
keyboard) on the active Chiaki session and watch events stream. Ctrl+C stops
recording and exits cleanly.

Run:
    python3 watch_events.py                       # local socket, default task
    python3 watch_events.py --task my-test
    python3 watch_events.py --url tcp://HOST:15432

Note: recordedEventCaptured only fires while recording is armed, so this tool
arms it for you; controller input is what drives the events.
"""

import argparse
import signal
import sys

from PySide6.QtCore import (
    QCoreApplication,
    QTimer,
    QUrl,
)
from PySide6.QtRemoteObjects import QRemoteObjectNode


def fmt_event(ev: dict) -> str:
    ev = dict(ev)
    shot = ev.pop("screenshot", None)
    src = ev.get("source", "?")
    delta = ev.get("deltaMs", "?")
    if src == "controller":
        body = (f"buttons=0x{int(ev.get('buttons', 0)):08x} "
                f"pressed=0x{int(ev.get('pressed', 0)):08x} "
                f"mod=0x{int(ev.get('modifiers', 0)):08x} "
                f"L=({ev.get('leftX')},{ev.get('leftY')}) "
                f"R=({ev.get('rightX')},{ev.get('rightY')}) "
                f"l2={ev.get('l2')} r2={ev.get('r2')}")
    elif src == "keyboard":
        body = f"key={ev.get('key')} mods={ev.get('keyboardModifiers')}"
    elif src == "frame":
        body = "periodic frame"
    else:
        body = str(ev)
    shot_info = f" shot={len(bytes(shot))}B" if shot else ""
    return f"[+{delta:>5}ms] {src:<10} {body}{shot_info}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Print Chiaki recorder events live.")
    parser.add_argument("--url", default="local:chiaki-current-session",
                        help="RemoteController node URL (default: local socket).")
    parser.add_argument("--name", default="RemoteController")
    parser.add_argument("--task", default="manual-watch")
    args = parser.parse_args()

    app = QCoreApplication(sys.argv)
    node = QRemoteObjectNode()
    node.connectToNode(QUrl(args.url))
    replica = node.acquireDynamic(args.name)

    state = {"wired": False, "count": 0}

    def wire():
        if state["wired"]:
            return
        state["wired"] = True

        def on_event(ev):
            state["count"] += 1
            print(fmt_event(dict(ev)), flush=True)

        def on_session():
            try:
                conn = replica.property("sessionConnectedtoPs")
            except Exception:
                conn = None
            print(f"** session_connected={conn}", flush=True)

        replica.recordedEventCaptured.connect(on_event)
        replica.sessionConnectedtoPsChanged.connect(on_session)
        # Dynamic replicas expose invokables as direct callables; the PySide6
        # QGenericArgument form is not usable for QString args.
        replica.startTaskRecording(args.task)
        print(f"** recording armed (task={args.task}); move the controller. Ctrl+C to stop.",
              flush=True)

    replica.initialized.connect(wire)
    if replica.isInitialized():
        wire()
    else:
        print("** waiting for RemoteController replica...", flush=True)

    def stop(*_):
        if state["wired"]:
            try:
                replica.stopTaskRecording()
            except Exception:
                pass
            app.processEvents()
        print(f"\n** stopped. {state['count']} events.", flush=True)
        app.quit()

    signal.signal(signal.SIGINT, lambda *_: stop())
    # let Python signal handlers run during the Qt loop
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
