#!/usr/bin/env python3
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import ipaddress
from pathlib import Path
from typing import Any

from scene_learning import (
    DeepInfraClipClassifier,
    LearningBuffer,
    LearningStore,
    SceneLearningError,
    TorchvisionEmbedder,
    TorchvisionExporter,
    cosine,
    update_timing,
)


DEFAULT_CHIAKI_WRAPPER = Path(
    "/home/soloway/.hermes/profiles/ps-main/bin/chiaki-launcher"
)
DEFAULT_PROCESS_PATTERN = r"(?:ps/chiaki/bin|chiaki-ng/build[^/]*/gui)/chiaki$"
DEFAULT_LOCAL_REMOTE_URL = "local:chiaki-current-session"
DEFAULT_REMOTE_URL = os.environ.get("CHIAKI_REMOTE_CONTROLLER_URL", "auto")
DEFAULT_REMOTE_NAME = "RemoteController"
DEFAULT_LAN_PORT = 15432
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_WINDOW_CLASS = "chiaki"
BUTTON_NAMES = (
    "cross",
    "circle",
    "box",
    "triangle",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
    "l1",
    "r1",
    "l3",
    "r3",
    "options",
    "touchpad",
    "ps",
    "none",
)


class RemoteSelectionRequired(RuntimeError):
    def __init__(self, candidates: list[dict]):
        super().__init__("More than one LAN RemoteController found")
        self.candidates = candidates


class RemoteDiscoveryError(RuntimeError):
    def __init__(self, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


def json_print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def process_running(pattern: str) -> bool:
    try:
        compiled = re.compile(pattern)
    except re.error:
        compiled = re.compile(re.escape(pattern))

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0:
        return False

    own_pid = os.getpid()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, args = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == own_pid or "chiaki_remote_gateway.py" in args:
            continue
        if compiled.search(args):
            return True
    return False


def find_visible_window_id(window_class: str) -> str | None:
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", window_class],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        window_id = line.strip()
        if window_id:
            return window_id
    return None


def capture_visible_window_png(window_class: str, output: Path) -> tuple[bool, str | None, str | None]:
    window_id = find_visible_window_id(window_class)
    if not window_id:
        return False, None, f"No visible window found for class {window_class!r}"

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["import", "-window", window_id, str(output)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, window_id, str(exc)

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or f"import exited {result.returncode}"
        return False, window_id, error
    if not output.exists() or not output.read_bytes().startswith(PNG_SIGNATURE):
        return False, window_id, "Window capture did not produce PNG data"
    return True, window_id, None


def resolve_output_path(output: Path) -> Path:
    output = output.expanduser()
    if not output.is_absolute():
        return Path.cwd() / output
    return output


def launch_chiaki(wrapper: Path, process_pattern: str) -> bool:
    if process_running(process_pattern):
        return False
    if not wrapper.exists():
        raise SystemExit(f"Chiaki wrapper not found: {wrapper}")

    env = os.environ.copy()
    env.setdefault("PATH", "/usr/bin:/bin")
    subprocess.Popen(
        ["/bin/bash", str(wrapper)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    return True


def should_launch_chiaki(args: argparse.Namespace) -> bool:
    resolution = getattr(args, "remote_resolution", {}) or {}
    if resolution.get("source") == "lan" or str(args.remote_url).startswith("tcp://"):
        return False
    return True


def import_pyside6() -> tuple[bool, str | None]:
    try:
        import PySide6  # noqa: F401
        from PySide6.QtCore import (
            QByteArray,
            QCoreApplication,
            QEventLoop,
            QGenericArgument,
            QMetaObject,
            Qt,
            QTimer,
            QUrl,
            qInstallMessageHandler,
        )
        from PySide6.QtRemoteObjects import QRemoteObjectNode, QRemoteObjectPendingCall
    except Exception as exc:
        return False, str(exc)

    def message_handler(message_type, context, message):
        del message_type, context
        if (
            "Dynamic metaobject is not assigned" in message
            or "This may cause issues if used for more than checking the Replica state" in message
        ):
            return
        print(message, file=sys.stderr)

    qInstallMessageHandler(message_handler)
    globals().update(
        QByteArray=QByteArray,
        QCoreApplication=QCoreApplication,
        QEventLoop=QEventLoop,
        QGenericArgument=QGenericArgument,
        QMetaObject=QMetaObject,
        Qt=Qt,
        QRemoteObjectNode=QRemoteObjectNode,
        QRemoteObjectPendingCall=QRemoteObjectPendingCall,
        QTimer=QTimer,
        QUrl=QUrl,
    )
    return True, None


class RemoteControllerClient:
    def __init__(self, remote_url: str, remote_name: str):
        ok, error = import_pyside6()
        if not ok:
            raise RuntimeError(f"PySide6 is not available: {error}")

        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.node = QRemoteObjectNode()
        self.node.connectToNode(QUrl(remote_url))
        self.replica = self.node.acquireDynamic(remote_name)

    def wait_ready(self, timeout_ms: int) -> bool:
        if self.replica.isInitialized():
            return True

        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        self.replica.initialized.connect(loop.quit)
        timer.start(timeout_ms)
        loop.exec()
        return self.replica.isInitialized()

    def resolve_call(self, call, timeout_ms: int = 5000):
        if isinstance(call, QRemoteObjectPendingCall):
            if not call.waitForFinished(timeout_ms):
                raise RuntimeError("RemoteController call timed out")
            error = call.error()
            if error:
                raise RuntimeError(f"RemoteController call failed: {error}")
            return call.returnValue()
        return call

    def screenshot(self) -> bytes:
        result: list[bytes] = []
        loop = QEventLoop()

        def on_shot(data):
            if isinstance(data, QByteArray):
                result.append(bytes(data))
            elif data:
                result.append(bytes(data))
            loop.quit()

        try:
            self.replica.screenShotReady.connect(on_shot)
            QMetaObject.invokeMethod(self.replica, "requestScreenShot", Qt.ConnectionType.QueuedConnection)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
        finally:
            try:
                self.replica.screenShotReady.disconnect(on_shot)
            except Exception:
                pass

        return result[0] if result else b""

    def send(self, button_name: str, interval_ms: int | None = None) -> str:
        if interval_ms is not None:
            if not self.replica.setProperty("pressIntervalMs", int(interval_ms)):
                raise RuntimeError("Failed to set RemoteController pressIntervalMs")

        sent: list[str] = []
        loop = QEventLoop()
        saw_start: list[bool] = [False]

        def on_button_changed(btn):
            if btn == button_name:
                saw_start[0] = True
            elif btn == "" and saw_start[0]:
                sent.append(button_name)
                loop.quit()

        # Timeout: press_interval + round-trips + generous slack
        timeout_ms = max(2000, int((interval_ms or 120) + 1500))
        try:
            self.replica.buttonChanged.connect(on_button_changed)
            self.replica.setProperty("button", button_name)
            QTimer.singleShot(timeout_ms, loop.quit)
            loop.exec()
        finally:
            try:
                self.replica.buttonChanged.disconnect(on_button_changed)
            except Exception:
                pass

        return sent[0] if sent else ""

    def wait_session_connected(self, timeout_ms: int) -> bool:
        """Block until sessionConnectedtoPs is true or timeout_ms elapses.

        Polls every 500 ms. Avoids relying on dynamic-replica notified signal
        delivery for named properties, which is unreliable with acquireDynamic.
        """
        if self.replica.property("sessionConnectedtoPs") is True:
            return True

        poll_ms = 500
        elapsed_ms = 0
        while elapsed_ms < timeout_ms:
            wait_ms = min(poll_ms, timeout_ms - elapsed_ms)
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(wait_ms)
            loop.exec()
            elapsed_ms += wait_ms
            if self.replica.property("sessionConnectedtoPs") is True:
                return True
        return False


def remote_controller_ready(remote_url: str, remote_name: str, timeout_ms: int) -> tuple[bool, str | None]:
    try:
        client = RemoteControllerClient(remote_url, remote_name)
        if client.wait_ready(timeout_ms):
            return True, None
        return False, "RemoteController replica did not initialize"
    except Exception as exc:
        return False, str(exc)


def local_ipv4_networks(max_hosts: int) -> list[ipaddress.IPv4Network]:
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show", "scope", "global"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if not match:
            continue
        try:
            interface = ipaddress.IPv4Interface(match.group(1))
        except ValueError:
            continue
        network = interface.network
        if network.num_addresses > max_hosts:
            network = ipaddress.IPv4Network(f"{interface.ip}/24", strict=False)
        key = str(network)
        if key not in seen:
            networks.append(network)
            seen.add(key)
    return networks


def tcp_port_open(host: str, port: int, timeout_ms: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=max(0.05, timeout_ms / 1000.0)):
            return True
    except OSError:
        return False


def discover_lan_remote_urls(args: argparse.Namespace) -> list[dict]:
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    networks = local_ipv4_networks(args.lan_max_hosts)
    deadline = time.monotonic() + args.timeout_ms / 1000.0

    for network in networks:
        for host in network.hosts():
            if time.monotonic() >= deadline:
                break
            host_text = str(host)
            if not tcp_port_open(host_text, args.lan_port, args.lan_connect_timeout_ms):
                continue
            url = f"tcp://{host_text}:{args.lan_port}"
            if url in seen_urls:
                continue
            ready, error = remote_controller_ready(url, args.remote_name, args.probe_ms)
            if ready:
                candidates.append({"url": url, "host": host_text, "source": "lan"})
                seen_urls.add(url)
            elif args.include_unready_lan:
                candidates.append({"url": url, "host": host_text, "source": "lan", "error": error})
                seen_urls.add(url)
    return candidates


def choose_remote_candidate(candidates: list[dict], args: argparse.Namespace) -> dict:
    selection = args.remote_index
    env_selection = os.environ.get("CHIAKI_REMOTE_CONTROLLER_INDEX")
    if selection is None and env_selection:
        try:
            selection = int(env_selection)
        except ValueError:
            selection = None

    if selection is not None:
        index = selection - 1
        if index < 0 or index >= len(candidates):
            raise RemoteDiscoveryError(f"RemoteController index out of range: {selection}", candidates)
        return candidates[index]

    if len(candidates) == 1:
        return candidates[0]

    if sys.stdin.isatty():
        print("Multiple LAN RemoteController sources found:", file=sys.stderr)
        for index, candidate in enumerate(candidates, start=1):
            print(f"{index}. {candidate['url']}", file=sys.stderr)
        choice = input("Connect to RemoteController #: ").strip()
        try:
            index = int(choice) - 1
        except ValueError as exc:
            raise RemoteDiscoveryError(f"Invalid RemoteController selection: {choice}", candidates) from exc
        if index < 0 or index >= len(candidates):
            raise RemoteDiscoveryError(f"RemoteController index out of range: {choice}", candidates)
        return candidates[index]

    raise RemoteSelectionRequired(candidates)


def resolve_remote_url(args: argparse.Namespace) -> None:
    mode = args.remote_url.strip()
    if mode not in {"auto", "lan-auto"}:
        return

    if mode == "auto":
        ready, _ = remote_controller_ready(DEFAULT_LOCAL_REMOTE_URL, args.remote_name, args.probe_ms)
        if ready:
            args.remote_url = DEFAULT_LOCAL_REMOTE_URL
            args.remote_resolution = {"mode": mode, "selected": args.remote_url, "source": "local"}
            return

    candidates = discover_lan_remote_urls(args)
    if not candidates:
        if mode == "auto":
            args.remote_url = DEFAULT_LOCAL_REMOTE_URL
            args.remote_resolution = {
                "mode": mode,
                "selected": args.remote_url,
                "source": "local-fallback",
                "lan_candidates": [],
            }
            return
        raise RemoteDiscoveryError("No LAN RemoteController found", candidates)

    selected = choose_remote_candidate(candidates, args)
    args.remote_url = selected["url"]
    args.remote_resolution = {"mode": mode, "selected": args.remote_url, "source": selected.get("source"), "lan_candidates": candidates}


def wait_for_replica(args: argparse.Namespace) -> tuple[bool, str | None]:
    deadline = time.monotonic() + args.timeout_ms / 1000.0
    last_error = None
    while time.monotonic() < deadline:
        try:
            client = RemoteControllerClient(args.remote_url, args.remote_name)
            remaining_ms = max(100, int((deadline - time.monotonic()) * 1000))
            if client.wait_ready(min(args.probe_ms, remaining_ms)):
                return True, None
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.2)
    return False, last_error


def command_status(args: argparse.Namespace) -> int:
    ok, py_error = import_pyside6()
    replica_available = False
    replica_error = None
    if ok:
        args.timeout_ms = min(args.timeout_ms, args.probe_ms)
        replica_available, replica_error = wait_for_replica(args)

    payload = {
        "py_side6_available": ok,
        "py_side6_error": py_error,
        "chiaki_running": process_running(args.process_pattern),
        "chiaki_wrapper_exists": args.chiaki_wrapper.exists(),
        "remote_url": args.remote_url,
        "remote_name": args.remote_name,
        "remote_resolution": getattr(args, "remote_resolution", None),
        "replica_available": replica_available,
        "replica_error": replica_error,
    }
    json_print(payload)
    return 0 if ok and replica_available else 1


def command_discover_remote(args: argparse.Namespace) -> int:
    ok, py_error = import_pyside6()
    if not ok:
        json_print({"ok": False, "py_side6_available": False, "py_side6_error": py_error})
        return 1

    local_ready, local_error = remote_controller_ready(DEFAULT_LOCAL_REMOTE_URL, args.remote_name, args.probe_ms)
    lan_candidates = discover_lan_remote_urls(args)
    candidates = []
    if local_ready:
        candidates.append({"url": DEFAULT_LOCAL_REMOTE_URL, "source": "local"})
    candidates.extend(lan_candidates)
    json_print(
        {
            "ok": bool(candidates),
            "local": {"url": DEFAULT_LOCAL_REMOTE_URL, "ready": local_ready, "error": local_error},
            "lan_candidates": lan_candidates,
            "candidates": candidates,
        }
    )
    return 0 if candidates else 1


def command_wait(args: argparse.Namespace) -> int:
    launched = launch_chiaki(args.chiaki_wrapper, args.process_pattern) if should_launch_chiaki(args) else False
    available, error = wait_for_replica(args)
    payload = {
        "launched_chiaki": launched,
        "chiaki_running": process_running(args.process_pattern),
        "remote_url": args.remote_url,
        "remote_name": args.remote_name,
        "remote_resolution": getattr(args, "remote_resolution", None),
        "replica_available": available,
        "replica_error": error,
    }
    json_print(payload)
    return 0 if available else 1


def command_wait_session(args: argparse.Namespace) -> int:
    """Launch Chiaki if needed, wait for replica, then wait for session connected."""
    t0 = time.monotonic()
    launched = launch_chiaki(args.chiaki_wrapper, args.process_pattern) if should_launch_chiaki(args) else False
    available, error = wait_for_replica(args)
    if not available:
        json_print({
            "ok": False,
            "error": error or "RemoteController replica did not initialize",
            "launched_chiaki": launched,
            "replica_available": False,
            "session_connected": False,
            "waited_ms": int((time.monotonic() - t0) * 1000),
        })
        return 1

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    remaining_ms = max(1000, args.timeout_ms - elapsed_ms)
    try:
        client = RemoteControllerClient(args.remote_url, args.remote_name)
        if not client.wait_ready(min(remaining_ms, args.probe_ms)):
            client.wait_ready(remaining_ms)
        connected = client.wait_session_connected(remaining_ms)
    except Exception as exc:
        json_print({
            "ok": False,
            "error": str(exc),
            "launched_chiaki": launched,
            "replica_available": True,
            "session_connected": False,
            "waited_ms": int((time.monotonic() - t0) * 1000),
        })
        return 1

    waited_ms = int((time.monotonic() - t0) * 1000)
    payload = {
        "ok": connected,
        "launched_chiaki": launched,
        "replica_available": True,
        "session_connected": connected,
        "waited_ms": waited_ms,
        "remote_url": args.remote_url,
        "remote_name": args.remote_name,
    }
    if not connected:
        payload["error"] = "Session did not connect within timeout"
    json_print(payload)
    return 0 if connected else 1


def capture_screenshot(args: argparse.Namespace) -> tuple[int, dict]:
    if should_launch_chiaki(args):
        launch_chiaki(args.chiaki_wrapper, args.process_pattern)
    deadline = time.monotonic() + args.timeout_ms / 1000.0
    output = resolve_output_path(args.output)
    client = RemoteControllerClient(args.remote_url, args.remote_name)
    if not client.wait_ready(args.probe_ms):
        remaining_ms = max(100, int((deadline - time.monotonic()) * 1000))
        if remaining_ms <= 100 or not client.wait_ready(remaining_ms):
            if args.window_fallback:
                ok, window_id, error = capture_visible_window_png(args.window_class, output)
                if ok:
                    return 0, {
                        "ok": True,
                        "output": str(output),
                        "bytes": output.stat().st_size,
                        "source": "x11_window",
                        "window_id": window_id,
                        "replica_available": False,
                    }
                return 1, {
                    "ok": False,
                    "error": "RemoteController replica did not initialize and window fallback failed",
                    "fallback_error": error,
                    "fallback_window_id": window_id,
                    "chiaki_running": process_running(args.process_pattern),
                    "remote_url": args.remote_url,
                    "remote_name": args.remote_name,
                }
            return 1, {
                "ok": False,
                "error": "RemoteController replica did not initialize",
                "chiaki_running": process_running(args.process_pattern),
                "remote_url": args.remote_url,
                "remote_name": args.remote_name,
            }

    empty_reads = 0
    invalid_reads = 0
    last_size = 0
    png = b""
    while time.monotonic() < deadline:
        png = client.screenshot()
        last_size = len(png)
        if png.startswith(PNG_SIGNATURE):
            break
        if png:
            invalid_reads += 1
        else:
            empty_reads += 1
        time.sleep(args.screenshot_retry_ms / 1000.0)

    if not png and args.window_fallback:
        ok, window_id, error = capture_visible_window_png(args.window_class, output)
        if ok:
            return 0, {
                "ok": True,
                "output": str(output),
                "bytes": output.stat().st_size,
                "source": "x11_window",
                "window_id": window_id,
                "remote_empty_reads": empty_reads,
            }

        return 1, {
            "ok": False,
            "error": "RemoteController returned empty screenshot and window fallback failed",
            "diagnosis": "RemoteController is reachable, but Chiaki has no decoded stream frame cached. X11 fallback also could not capture the visible Chiaki window.",
            "fallback_error": error,
            "fallback_window_id": window_id,
            "chiaki_running": process_running(args.process_pattern),
            "replica_available": client.replica.isInitialized(),
            "empty_reads": empty_reads,
        }

    if not png:
        return 1, {
            "ok": False,
            "error": "RemoteController returned empty screenshot",
            "diagnosis": "RemoteController is reachable, but Chiaki has no decoded stream frame cached yet. Start/connect the PlayStation stream, then retry.",
            "chiaki_running": process_running(args.process_pattern),
            "replica_available": client.replica.isInitialized(),
            "empty_reads": empty_reads,
        }
    if not png.startswith(PNG_SIGNATURE) and args.window_fallback:
        ok, window_id, error = capture_visible_window_png(args.window_class, output)
        if ok:
            return 0, {
                "ok": True,
                "output": str(output),
                "bytes": output.stat().st_size,
                "source": "x11_window",
                "window_id": window_id,
                "remote_invalid_reads": invalid_reads,
                "remote_empty_reads": empty_reads,
            }

        return 3, {
            "ok": False,
            "error": "RemoteController screenshot is not PNG data and window fallback failed",
            "fallback_error": error,
            "fallback_window_id": window_id,
            "bytes": last_size,
            "invalid_reads": invalid_reads,
            "empty_reads": empty_reads,
        }

    if not png.startswith(PNG_SIGNATURE):
        return 3, {
            "ok": False,
            "error": "RemoteController screenshot is not PNG data",
            "bytes": last_size,
            "invalid_reads": invalid_reads,
            "empty_reads": empty_reads,
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(png)
    return 0, {"ok": True, "output": str(output), "bytes": len(png), "source": "remote_controller"}


def command_screenshot(args: argparse.Namespace) -> int:
    code, payload = capture_screenshot(args)
    json_print(payload)
    return code


def capture_scene_state(
    args: argparse.Namespace,
    store: LearningStore,
    embedder: TorchvisionEmbedder,
    label: str | None = None,
) -> tuple[int, dict]:
    code, screenshot_payload = capture_screenshot(args)
    if code != 0:
        return code, {"ok": False, "error": "screenshot failed", "screenshot": screenshot_payload}

    path = Path(screenshot_payload["output"])
    try:
        embedding = embedder.embed(path)
        match = store.match_scene(embedding, args.scene_threshold)
        deepinfra_classification: dict | None = None

        # DeepInfra CLIP fallback when local match fails
        if not match["matched"]:
            try:
                classifier = DeepInfraClipClassifier()
                candidate_labels = [s["label"] for s in store.scenes()]
                candidate_labels.append("unknown")
                deepinfra_classification = classifier.classify(path, candidate_labels)
            except SceneLearningError:
                deepinfra_classification = None

        learned = store.add_scene(label, embedding, {"source": screenshot_payload["source"]}) if label else None
        return 0, {
            "ok": True,
            "source": screenshot_payload["source"],
            "match": match,
            "deepinfra_classification": deepinfra_classification,
            "learned_scene": {
                "id": learned["id"],
                "label": learned["label"],
            }
            if learned
            else None,
            "_embedding": embedding,
            "embedding": embedding if getattr(args, "include_embedding", False) else None,
        }
    except SceneLearningError as exc:
        return 4, {"ok": False, "error": str(exc)}
    finally:
        if not getattr(args, "keep_screenshot", False):
            path.unlink(missing_ok=True)


def command_scene(args: argparse.Namespace) -> int:
    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4
    code, payload = capture_scene_state(args, store, embedder)
    payload.pop("embedding", None)
    payload.pop("_embedding", None)
    json_print(payload)
    return code


def command_classify(args: argparse.Namespace) -> int:
    """Classify a screenshot: local embed first, DeepInfra CLIP fallback."""
    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4

    code, screenshot_payload = capture_screenshot(args)
    if code != 0:
        json_print({"ok": False, "error": "screenshot failed", "screenshot": screenshot_payload})
        return code

    path = Path(screenshot_payload["output"])
    result: dict[str, Any] = {
        "ok": True,
        "source": screenshot_payload["source"],
        "matched": False,
        "label": "unknown",
        "method": "none",
    }

    try:
        embedding = embedder.embed(path)
        match = store.match_scene(embedding, args.scene_threshold)
        result["local_score"] = match["score"]

        if match["matched"] and match["scene"]:
            result["matched"] = True
            result["label"] = match["scene"]["label"]
            result["method"] = "local"
        else:
            # DeepInfra fallback
            try:
                classifier = DeepInfraClipClassifier()
                candidate_labels = [s["label"] for s in store.scenes()]
                if not candidate_labels:
                    candidate_labels = ["unknown"]
                classification = classifier.classify(path, candidate_labels)
                result["deepinfra_classification"] = classification
                result["matched"] = classification["score"] >= args.scene_threshold
                result["label"] = classification["label"]
                result["method"] = "deepinfra"
            except SceneLearningError as exc:
                result["deepinfra_error"] = str(exc)
    except SceneLearningError as exc:
        result["ok"] = False
        result["error"] = str(exc)
    finally:
        if not getattr(args, "keep_screenshot", False):
            path.unlink(missing_ok=True)

    if getattr(args, "include_embedding", False):
        result["embedding"] = embedding

    json_print(result)
    return 0 if result["ok"] else 4


def command_background_learn(args: argparse.Namespace) -> int:
    """Capture screenshot, queue into buffer, spawn bg flush if threshold reached.

    Returns immediately so the main control loop stays responsive.
    The actual embedding + classification runs asynchronously.
    """
    code, screenshot_payload = capture_screenshot(args)
    if code != 0:
        json_print({"ok": False, "error": "screenshot failed", "screenshot": screenshot_payload})
        return code

    src = Path(screenshot_payload["output"])
    try:
        buffer = LearningBuffer(args.learning_root)
        # CLI arg overrides env default
        if args.buffer_threshold != buffer.threshold:
            buffer.threshold = args.buffer_threshold
        queued = buffer.queue(src)
        size = buffer.size()
        ready = buffer.ready()
    finally:
        if not getattr(args, "keep_screenshot", False):
            src.unlink(missing_ok=True)

    payload: dict[str, Any] = {
        "ok": True,
        "queued": str(queued),
        "buffer_size": size,
        "threshold": buffer.threshold,
        "ready": ready,
        "flush_spawned": False,
    }

    if ready:
        gateway = Path(__file__).expanduser()
        learning_root = str(args.learning_root.expanduser())
        env = os.environ.copy()
        env["HOME"] = os.environ.get("REAL_HOME", "/home/soloway")
        env.setdefault("CHIAKI_BUFFER_THRESHOLD", str(buffer.threshold))
        subprocess.Popen(
            [
                sys.executable,
                str(gateway),
                "--learning-root", learning_root,
                "flush-learn",
                "--keep-screenshot",
                "--buffer-threshold", str(buffer.threshold),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        payload["flush_spawned"] = True
        payload["message"] = (
            f"Buffer reached threshold ({size}/{buffer.threshold}). "
            "Background learning started."
        )

    json_print(payload)
    return 0


def command_flush_learn(args: argparse.Namespace) -> int:
    """Process all queued screenshots: embed, match, store unknown scenes.

    Iterates over every PNG in the buffer, embeds with CLIP, attempts local
    matching, and writes unknown scenes into the learning store. This is
    designed to be called as a background subprocess.
    """
    buffer = LearningBuffer(args.learning_root)
    if args.buffer_threshold != buffer.threshold:
        buffer.threshold = args.buffer_threshold
    paths = buffer.pop_all()
    if not paths:
        json_print({"ok": True, "flushed": 0, "message": "buffer empty"})
        return 0

    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc), "buffered": len(paths)})
        return 4

    processed = 0
    matched = 0
    unknown = 0
    errors = 0
    results: list[dict] = []

    for png in paths:
        try:
            embedding = embedder.embed(png)
            match = store.match_scene(embedding, args.scene_threshold)
            processed += 1

            if match["matched"]:
                matched += 1
                results.append({"file": str(png), "match": match["scene"]["label"], "score": match["score"]})
            else:
                unknown += 1
                label = f"auto-{int(time.time())}"
                scene = store.add_scene(label, embedding, {"source": "background-learn", "file": str(png.name)})
                results.append({"file": str(png), "new_scene": scene["id"], "label": label, "score": match["score"]})
        except Exception as exc:
            errors += 1
            results.append({"file": str(png), "error": str(exc)})
        finally:
            if not getattr(args, "keep_screenshot", False):
                png.unlink(missing_ok=True)

    payload = {
        "ok": True,
        "flushed": len(paths),
        "processed": processed,
        "matched": matched,
        "unknown": unknown,
        "errors": errors,
        "results": results if getattr(args, "verbose", False) else [],
        "total_scenes": len(store.scenes()),
    }
    json_print(payload)
    return 0 if errors == 0 else 4


def command_namespaces(args: argparse.Namespace) -> int:
    """List all available learning namespaces."""
    namespaces = LearningStore.namespaces(args.learning_root)
    json_print({
        "ok": True,
        "learning_root": str(args.learning_root),
        "namespaces": namespaces,
        "active": args.namespace,
    })
    return 0


def command_card_model_import(args: argparse.Namespace) -> int:
    """Import HUT builder card features into the learning store.

    Reads card images from hutbuilder/output/images/, embeds each card
    image with CLIP ViT-B/32, and stores them as scenes in the active
    namespace. Each card scene gets metadata (card_id, player_name,
    card_type) for downstream card identification in screenshots.
    """
    hutbuilder_output = args.hutbuilder_output.expanduser()
    images_dir = hutbuilder_output / "images"
    cards_jsonl = hutbuilder_output / "cards.jsonl"

    if not images_dir.exists():
        json_print({"ok": False, "error": f"images dir not found: {images_dir}"})
        return 1
    if not cards_jsonl.exists():
        json_print({"ok": False, "error": f"cards.jsonl not found: {cards_jsonl}"})
        return 1

    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4

    store = LearningStore(args.learning_root, namespace=args.namespace)

    # Load card metadata
    cards_by_id: dict[str, dict] = {}
    with open(cards_jsonl) as f:
        for line in f:
            card = json.loads(line.strip())
            cards_by_id[card["card_id"]] = card

    # Scan images
    image_paths = sorted(images_dir.glob("*.webp")) + sorted(images_dir.glob("*.png"))
    if not image_paths:
        json_print({"ok": False, "error": f"no card images in {images_dir}"})
        return 1

    imported = 0
    skipped = 0
    errors = 0

    batch = []
    for img_path in image_paths:
        card_id = img_path.stem
        card_meta = cards_by_id.get(card_id, {})
        batch.append((img_path, card_id, card_meta))

        if len(batch) >= args.batch_size:
            for b_img, b_id, b_meta in batch:
                try:
                    embedding = embedder.embed(b_img)
                    match = store.match_scene(embedding, args.card_threshold)
                    if match["matched"]:
                        skipped += 1
                        continue
                    store.add_scene(
                        f"card-{b_id}",
                        embedding,
                        {
                            "card_id": b_id,
                            "player_name": b_meta.get("player_name", ""),
                            "card_type": b_meta.get("card_type", ""),
                            "source": "hutbuilder-import",
                            "page": "card_view",
                        },
                    )
                    imported += 1
                except Exception:
                    errors += 1
            batch = []

    # Remaining batch
    for b_img, b_id, b_meta in batch:
        try:
            embedding = embedder.embed(b_img)
            match = store.match_scene(embedding, args.card_threshold)
            if match["matched"]:
                skipped += 1
                continue
            store.add_scene(
                f"card-{b_id}",
                embedding,
                {
                    "card_id": b_id,
                    "player_name": b_meta.get("player_name", ""),
                    "card_type": b_meta.get("card_type", ""),
                    "source": "hutbuilder-import",
                    "page": "card_view",
                },
            )
            imported += 1
        except Exception:
            errors += 1

    json_print({
        "ok": True,
        "namespace": args.namespace,
        "total_images": len(image_paths),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total_scenes": len(store.scenes()),
    })
    return 0 if errors == 0 else 4


def command_export_torchvision(args: argparse.Namespace) -> int:
    """Export learning store to torchvision dataset format."""
    store = LearningStore(args.learning_root, namespace=args.namespace)
    exporter = TorchvisionExporter(store)
    try:
        result = exporter.export(args.output_dir)
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4
    json_print({"ok": True, **result})
    return 0


def command_remember_scene(args: argparse.Namespace) -> int:
    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4
    code, payload = capture_scene_state(args, store, embedder, args.label)
    payload.pop("embedding", None)
    payload.pop("_embedding", None)
    json_print(payload)
    return code


def extract_json_object(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def action_prompt(goal: str) -> str:
    buttons = ", ".join(BUTTON_NAMES)
    return (
        "Analyze this Chiaki/PlayStation screenshot. Return JSON only. "
        f"Goal: {goal}\n"
        f"Allowed action values: {buttons}.\n"
        "Schema: {\"action\":\"one allowed value\","
        "\"confidence\":0.0,"
        "\"reason\":\"short visual reason\","
        "\"send\":true|false}.\n"
        "Use action \"none\" and send false when no safe single controller action is clear. "
        "Choose only one next button press."
    )


def action_menu_prompt() -> str:
    buttons = ", ".join(BUTTON_NAMES)
    return (
        "Analyze this Chiaki/PlayStation screenshot. Return JSON only. "
        f"Allowed button values: {buttons}.\n"
        "List available user-facing actions visible on this screen. "
        "Use the on-screen label as the action name when possible: "
        "for example if the screen says Dismiss for the Cross button, name it Dismiss, not Cross. "
        "Include navigation actions that are safe from the visible menu.\n"
        "Schema: {\"actions\":[{\"name\":\"visible action label\","
        "\"button\":\"one allowed button\","
        "\"confidence\":0.0,"
        "\"reason\":\"short visual evidence\"}]}\n"
        "Do not invent actions not supported by visible UI."
    )


def call_action_advisor(args: argparse.Namespace, screenshot_path: Path, goal: str) -> dict:
    prompt = action_prompt(goal)
    command = [
        args.codex_bin,
        "exec",
        "-s",
        "read-only",
        "--image",
        str(screenshot_path),
        "-",
    ]
    result = subprocess.run(
        command,
        check=False,
        input=prompt,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=args.model_timeout_ms / 1000.0,
    )
    suggestion = extract_json_object(result.stdout)
    return {
        "ok": result.returncode == 0 and suggestion is not None,
        "suggestion": suggestion,
        "raw": result.stdout.strip(),
        "stderr": result.stderr.strip() if result.returncode != 0 or getattr(args, "include_stderr", False) else "",
    }


def call_action_menu_advisor(args: argparse.Namespace, screenshot_path: Path) -> dict:
    command = [
        args.codex_bin,
        "exec",
        "-s",
        "read-only",
        "--image",
        str(screenshot_path),
        "-",
    ]
    result = subprocess.run(
        command,
        check=False,
        input=action_menu_prompt(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=args.model_timeout_ms / 1000.0,
    )
    payload = extract_json_object(result.stdout) or {}
    actions = payload.get("actions") if isinstance(payload, dict) else None
    valid_actions = []
    if isinstance(actions, list):
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                continue
            button = str(action.get("button", "none"))
            if button not in BUTTON_NAMES:
                continue
            name = str(action.get("name") or button)
            valid_actions.append(
                {
                    "id": index,
                    "name": name,
                    "button": button,
                    "type": "screen",
                    "confidence": float(action.get("confidence", 0.0) or 0.0),
                    "reason": str(action.get("reason", "")),
                    "command": f"press {button}" if button != "none" else None,
                }
            )
    return {
        "ok": result.returncode == 0 and bool(valid_actions),
        "actions": valid_actions,
        "raw": result.stdout.strip(),
        "stderr": result.stderr.strip() if result.returncode != 0 or getattr(args, "include_stderr", False) else "",
    }


def command_suggest(args: argparse.Namespace) -> int:
    code, screenshot_payload = capture_screenshot(args)
    if code != 0:
        json_print(
            {
                "ok": False,
                "error": "screenshot failed",
                "screenshot": screenshot_payload,
            }
        )
        return code

    try:
        advisor = call_action_advisor(args, Path(screenshot_payload["output"]), args.goal)
    except (OSError, subprocess.TimeoutExpired) as exc:
        json_print(
            {
                "ok": False,
                "error": "model analysis failed",
                "detail": str(exc),
                "screenshot": screenshot_payload,
                "prompt": action_prompt(args.goal),
            }
        )
        return 4

    suggestion = advisor["suggestion"]
    payload = {
        "ok": advisor["ok"],
        "screenshot": screenshot_payload,
        "suggestion": suggestion,
        "raw": advisor["raw"],
    }
    if advisor["stderr"]:
        payload["stderr"] = advisor["stderr"]
    if suggestion:
        action = str(suggestion.get("action", "none"))
        payload["valid_action"] = action in BUTTON_NAMES
        payload["press_command"] = (
            f"python3 {Path(__file__).expanduser()} press {action}"
            if action in BUTTON_NAMES and action != "none" and suggestion.get("send") is True
            else None
        )
    json_print(payload)
    return 0 if payload["ok"] and payload.get("valid_action", False) else 5


def wait_for_scene_after_action(args: argparse.Namespace, store: LearningStore, embedder: TorchvisionEmbedder, expected_scene_id: str | None) -> dict:
    deadline = time.monotonic() + args.max_wait_ms / 1000.0
    previous_embedding = None
    stable_hits = 0
    captures = 0
    time.sleep(args.initial_delay_ms / 1000.0)

    while time.monotonic() < deadline:
        code, state = capture_scene_state(args, store, embedder)
        captures += 1
        embedding = state.pop("_embedding", None)
        state.pop("embedding", None)
        if code != 0 or embedding is None:
            return {"ok": False, "captures": captures, "error": state.get("error"), "state": state}

        match = state["match"]
        scene = match.get("scene") or {}
        if expected_scene_id and match.get("matched") and scene.get("id") == expected_scene_id:
            return {"ok": True, "captures": captures, "reason": "expected_scene", "state": state}

        if previous_embedding is not None and cosine(previous_embedding, embedding) >= args.stable_threshold:
            stable_hits += 1
        else:
            stable_hits = 1
        previous_embedding = embedding

        if stable_hits >= args.stable_count:
            return {"ok": True, "captures": captures, "reason": "stable_scene", "state": state}
        time.sleep(args.poll_ms / 1000.0)

    return {"ok": False, "captures": captures, "error": "scene did not stabilize before timeout"}


def command_learn_task(args: argparse.Namespace) -> int:
    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4

    code, screenshot_payload = capture_screenshot(args)
    if code != 0:
        json_print({"ok": False, "error": "screenshot failed", "screenshot": screenshot_payload})
        return code

    screenshot_path = Path(screenshot_payload["output"])
    try:
        embedding = embedder.embed(screenshot_path)
        match = store.match_scene(embedding, args.scene_threshold)
        advisor = call_action_advisor(args, screenshot_path, args.goal)
        pending = {
            "goal": args.goal,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "start_match": match,
            "start_embedding": embedding,
            "advisor": advisor,
        }
        store.write_pending(pending)
        payload = {
            "ok": advisor["ok"],
            "needs_user_confirmation": True,
            "message": "Confirm task result after sending/testing action, then run confirm-learning.",
            "start_match": match,
            "advisor": advisor,
        }
        json_print(payload)
        return 0 if advisor["ok"] else 5
    except (SceneLearningError, OSError, subprocess.TimeoutExpired) as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4
    finally:
        if not args.keep_screenshot:
            screenshot_path.unlink(missing_ok=True)


def command_confirm_learning(args: argparse.Namespace) -> int:
    store = LearningStore(args.learning_root, namespace=args.namespace)
    pending = store.read_pending()
    if not pending:
        json_print({"ok": False, "error": "no pending learning"})
        return 1

    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4

    code, end_state = capture_scene_state(args, store, embedder, args.expected_label)
    if code != 0:
        json_print(end_state)
        return code

    start_match = pending.get("start_match", {})
    start_scene = (start_match.get("scene") or {}) if start_match.get("matched") else None
    if not start_scene:
        learned_start = store.add_scene(f"{args.goal} start", pending["start_embedding"], {"goal": args.goal})
        start_scene = {"id": learned_start["id"], "label": learned_start["label"]}

    expected_scene = end_state.get("learned_scene")
    advisor_action = ((pending.get("advisor") or {}).get("suggestion") or {}).get("action")
    action = args.action or advisor_action
    if action not in BUTTON_NAMES or action == "none":
        json_print({"ok": False, "error": "missing valid action for learning", "action": action})
        return 2

    task = store.get_task(args.goal) or {"steps": []}
    steps = list(task.get("steps", []))
    steps.append(
        {
            "from_scene_id": start_scene["id"],
            "action": action,
            "expected_scene_id": expected_scene["id"],
            "timing": update_timing(None, args.transition_ms),
        }
    )
    saved = store.save_task(args.goal, steps)
    store.clear_pending()
    json_print({"ok": True, "task": saved, "raw_screenshot_deleted": not args.keep_screenshot})
    return 0


def command_run_task(args: argparse.Namespace) -> int:
    store = LearningStore(args.learning_root, namespace=args.namespace)
    task = store.get_task(args.goal)
    if not task:
        return command_learn_task(args)

    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4

    client = RemoteControllerClient(args.remote_url, args.remote_name)
    if not client.wait_ready(args.timeout_ms):
        json_print({"ok": False, "error": "RemoteController replica did not initialize"})
        return 1

    trace = []
    steps = list(task.get("steps", []))[: args.max_steps]
    for index, step in enumerate(steps):
        action = step["action"]
        timing = step.get("timing") or {}
        if timing.get("avg_transition_ms") and args.use_learned_timing:
            args.initial_delay_ms = max(args.initial_delay_ms, int(float(timing["avg_transition_ms"]) * 0.5))

        sent_at = time.monotonic()
        sent = client.send(action, args.interval_ms)
        if not sent:
            json_print({"ok": False, "error": "button send failed", "step": index, "action": action, "trace": trace})
            return 2

        state = wait_for_scene_after_action(args, store, embedder, step.get("expected_scene_id"))
        transition_ms = (time.monotonic() - sent_at) * 1000.0
        step["timing"] = update_timing(step.get("timing"), transition_ms)
        trace.append({"step": index, "action": action, "sent": sent, "transition_ms": round(transition_ms, 3), "state": state})
        if not state.get("ok"):
            store.save_task(args.goal, steps)
            json_print({"ok": False, "error": "scene verification failed", "trace": trace})
            return 3

    store.save_task(args.goal, steps)
    json_print({"ok": True, "goal": args.goal, "trace": trace})
    return 0


def command_press(args: argparse.Namespace) -> int:
    if should_launch_chiaki(args):
        launch_chiaki(args.chiaki_wrapper, args.process_pattern)
    client = RemoteControllerClient(args.remote_url, args.remote_name)
    if not client.wait_ready(args.timeout_ms):
        json_print({"ok": False, "error": "RemoteController replica did not initialize"})
        return 1
    sent = client.send(args.button, args.interval_ms)
    ok = bool(sent)
    if ok:
        LearningStore(args.learning_root, namespace=args.namespace).write_last_action(
            {"type": "button", "button": sent, "requested_button": args.button}
        )
    payload = {"ok": ok, "button": args.button, "sent": sent}
    if args.interval_ms is not None:
        payload["interval_ms"] = args.interval_ms
    json_print(payload)
    return 0 if ok else 2


def current_scene_actions(args: argparse.Namespace) -> tuple[int, dict]:
    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        return 4, {"ok": False, "error": str(exc), "atomic_buttons": list(BUTTON_NAMES), "actions": [], "tasks": []}

    code, screenshot_payload = capture_screenshot(args)
    if code != 0:
        return code, {"ok": False, "error": "screenshot failed", "screenshot": screenshot_payload, "atomic_buttons": list(BUTTON_NAMES), "actions": [], "tasks": []}

    screenshot_path = Path(screenshot_payload["output"])
    try:
        embedding = embedder.embed(screenshot_path)
        match = store.match_scene(embedding, args.scene_threshold)
        menu = call_action_menu_advisor(args, screenshot_path)
    except (SceneLearningError, OSError, subprocess.TimeoutExpired) as exc:
        return 4, {"ok": False, "error": str(exc), "atomic_buttons": list(BUTTON_NAMES), "actions": [], "tasks": []}
    finally:
        if not args.keep_screenshot:
            screenshot_path.unlink(missing_ok=True)

    scene_ids: set[str] = set()
    if match.get("matched") and match.get("scene"):
        scene_ids.add(match["scene"]["id"])
    for scene in store.scenes():
        if cosine(embedding, scene.get("embedding", [])) >= args.scene_threshold:
            scene_ids.add(scene.get("id", ""))

    tasks = []
    action_id = len(menu.get("actions", [])) + 1
    for task in store.tasks().values():
        steps = task.get("steps") or []
        if not steps:
            continue
        first = steps[0]
        if first.get("from_scene_id") in scene_ids:
            goal = task.get("goal")
            item = {
                "id": action_id,
                "name": goal,
                "button": first.get("action"),
                "type": "learned_task",
                "key": task.get("key"),
                "steps": len(steps),
                "first_action": first.get("action"),
                "command": f"run-task {json.dumps(goal)}",
            }
            tasks.append(item)
            action_id += 1

    return 0, {
        "ok": True,
        "current_scene": match,
        "actions": menu.get("actions", []) + tasks,
        "atomic_buttons": list(BUTTON_NAMES),
        "tasks": tasks,
        "task_count": len(tasks),
        "screen_action_labels_ok": menu.get("ok", False),
        "screen_action_label_error": menu.get("stderr") or None,
    }


def command_actions(args: argparse.Namespace) -> int:
    if args.current:
        code, payload = current_scene_actions(args)
        json_print(payload)
        return code

    json_print(
        {
            "ok": True,
            "buttons": list(BUTTON_NAMES),
            "navigation": [
                "status",
                "screenshot",
                "scene",
                "remember-scene",
                "suggest",
                "learn-task",
                "confirm-learning",
                "run-task",
                "press",
            ],
            "rule": "Show available next actions as a list before sending controller input when user asks for navigation.",
        }
    )
    return 0


def command_feedback(args: argparse.Namespace) -> int:
    sentiment = args.sentiment.lower()
    if sentiment in {"ok", "good", "yes", "positive"}:
        sentiment = "positive"
    elif sentiment in {"bad", "non", "no", "negative"}:
        sentiment = "negative"
    else:
        json_print({"ok": False, "error": "sentiment must be ok/good/positive or bad/non/no/negative"})
        return 2

    store = LearningStore(args.learning_root, namespace=args.namespace)
    try:
        embedder = TorchvisionEmbedder()
    except SceneLearningError as exc:
        json_print({"ok": False, "error": str(exc)})
        return 4

    code, state = capture_scene_state(args, store, embedder)
    embedding = state.pop("_embedding", None)
    state.pop("embedding", None)
    if code != 0 or embedding is None:
        json_print({"ok": False, "error": state.get("error"), "state": state})
        return code or 1

    feedback = store.add_feedback(sentiment, embedding, state.get("match") or {}, args.note)
    actions_args = argparse.Namespace(**vars(args))
    actions_args.current = True
    actions_code, actions_payload = current_scene_actions(actions_args)
    json_print(
        {
            "ok": True,
            "sentiment": sentiment,
            "feedback_id": feedback["id"],
            "last_action": feedback.get("last_action"),
            "state": state,
            "available_actions": actions_payload if actions_code == 0 else None,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PySide6 RemoteController gateway for Chiaki")
    parser.add_argument(
        "--remote-url",
        default=DEFAULT_REMOTE_URL,
        help="RemoteController URL, 'auto' for local-first discovery, or 'lan-auto' to scan LAN.",
    )
    parser.add_argument("--remote-name", default=DEFAULT_REMOTE_NAME)
    parser.add_argument("--remote-index", type=int, help="1-based index to select when LAN discovery finds multiple controllers.")
    parser.add_argument("--lan-port", type=int, default=DEFAULT_LAN_PORT)
    parser.add_argument("--lan-connect-timeout-ms", type=int, default=120)
    parser.add_argument("--lan-max-hosts", type=int, default=256)
    parser.add_argument("--include-unready-lan", action="store_true")
    parser.add_argument("--chiaki-wrapper", type=Path, default=DEFAULT_CHIAKI_WRAPPER)
    parser.add_argument("--process-pattern", default=DEFAULT_PROCESS_PATTERN)
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--probe-ms", type=int, default=1000)
    parser.add_argument("--learning-root", type=Path, default=LearningStore().root)
    parser.add_argument(
        "--namespace", "-n",
        default=os.environ.get("CHIAKI_LEARNING_NAMESPACE", "ps"),
        help="Learning namespace (game/app, e.g. ps/nhl26/nhl25/fifa26/nhl-common).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    actions_parser = subparsers.add_parser("actions")
    subparsers.add_parser("discover-remote")
    subparsers.add_parser("status")
    subparsers.add_parser("wait")
    subparsers.add_parser("wait-session")

    def add_screenshot_args(command_parser):
        command_parser.add_argument("--output", type=Path, default=Path("/tmp/chiaki-current.png"))
        command_parser.add_argument("--screenshot-retry-ms", type=int, default=500)
        command_parser.add_argument("--window-class", default=DEFAULT_WINDOW_CLASS)
        command_parser.add_argument(
            "--no-window-fallback",
            action="store_false",
            dest="window_fallback",
            help="Disable X11 visible-window capture fallback when RemoteController has no frame.",
        )
        command_parser.set_defaults(window_fallback=True)

    def add_scene_args(command_parser):
        add_screenshot_args(command_parser)
        command_parser.add_argument("--scene-threshold", type=float, default=0.88)
        command_parser.add_argument("--keep-screenshot", action="store_true")
        command_parser.add_argument("--include-embedding", action="store_true")

    def add_advisor_args(command_parser):
        command_parser.add_argument("--codex-bin", default="codex")
        command_parser.add_argument("--model-timeout-ms", type=int, default=120000)
        command_parser.add_argument("--include-stderr", action="store_true")

    def add_wait_scene_args(command_parser):
        command_parser.add_argument("--initial-delay-ms", type=int, default=500)
        command_parser.add_argument("--poll-ms", type=int, default=500)
        command_parser.add_argument("--max-wait-ms", type=int, default=10000)
        command_parser.add_argument("--stable-count", type=int, default=2)
        command_parser.add_argument("--stable-threshold", type=float, default=0.995)

    add_scene_args(actions_parser)
    add_advisor_args(actions_parser)
    actions_parser.add_argument("--current", action="store_true")

    screenshot = subparsers.add_parser("screenshot")
    add_screenshot_args(screenshot)

    scene = subparsers.add_parser("scene")
    add_scene_args(scene)

    remember_scene = subparsers.add_parser("remember-scene")
    add_scene_args(remember_scene)
    remember_scene.add_argument("label")

    suggest = subparsers.add_parser("suggest")
    add_screenshot_args(suggest)
    suggest.add_argument("--goal", default="Suggest the next safest useful controller action.")
    add_advisor_args(suggest)

    learn_task = subparsers.add_parser("learn-task")
    add_scene_args(learn_task)
    add_advisor_args(learn_task)
    learn_task.add_argument("goal")

    confirm_learning = subparsers.add_parser("confirm-learning")
    add_scene_args(confirm_learning)
    confirm_learning.add_argument("goal")
    confirm_learning.add_argument("--expected-label", required=True)
    confirm_learning.add_argument("--action")
    confirm_learning.add_argument("--transition-ms", type=float, default=0.0)

    run_task = subparsers.add_parser("run-task")
    add_scene_args(run_task)
    add_advisor_args(run_task)
    add_wait_scene_args(run_task)
    run_task.add_argument("--interval-ms", type=int)
    run_task.add_argument("--max-steps", type=int, default=25)
    run_task.add_argument("--no-learned-timing", action="store_false", dest="use_learned_timing")
    run_task.set_defaults(use_learned_timing=True)
    run_task.add_argument("goal")

    feedback = subparsers.add_parser("feedback")
    add_scene_args(feedback)
    add_advisor_args(feedback)
    feedback.add_argument("sentiment")
    feedback.add_argument("--note", default="")

    press = subparsers.add_parser("press")
    press.add_argument("--interval-ms", type=int)
    press.add_argument("button")

    classify = subparsers.add_parser("classify")
    add_scene_args(classify)

    background_learn = subparsers.add_parser("background-learn")
    add_screenshot_args(background_learn)
    background_learn.add_argument("--keep-screenshot", action="store_true")
    background_learn.add_argument(
        "--buffer-threshold",
        type=int,
        default=LearningBuffer().threshold,
        help="Minimum buffer size before auto-flush (default: %(default)s)",
    )

    flush_learn = subparsers.add_parser("flush-learn")
    flush_learn.add_argument("--keep-screenshot", action="store_true")
    flush_learn.add_argument(
        "--buffer-threshold",
        type=int,
        default=LearningBuffer().threshold,
    )
    flush_learn.add_argument("--verbose", action="store_true")
    flush_learn.add_argument("--scene-threshold", type=float, default=0.88)

    namespaces_cmd = subparsers.add_parser("namespaces", help="List available learning namespaces")

    card_import = subparsers.add_parser("card-model-import")
    card_import.add_argument(
        "--hutbuilder-output",
        type=Path,
        default=Path("/run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/hutbuilder/output"),
    )
    card_import.add_argument("--card-threshold", type=float, default=0.78)
    card_import.add_argument("--batch-size", type=int, default=32)

    export_tv = subparsers.add_parser("export-torchvision")
    export_tv.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / ".local/share/chiaki-remote-gateway/exports",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "discover-remote":
        return command_discover_remote(args)
    try:
        resolve_remote_url(args)
    except RemoteSelectionRequired as exc:
        json_print(
            {
                "ok": False,
                "error": "multiple_remote_controllers_found",
                "message": "More than one LAN RemoteController was found. Re-run with --remote-index N or --remote-url tcp://HOST:15432.",
                "candidates": exc.candidates,
            }
        )
        return 2
    except RemoteDiscoveryError as exc:
        json_print({"ok": False, "error": "remote_discovery_failed", "message": str(exc), "candidates": exc.candidates})
        return 1

    if args.command == "actions":
        return command_actions(args)
    if args.command == "status":
        return command_status(args)
    if args.command == "wait":
        return command_wait(args)
    if args.command == "wait-session":
        return command_wait_session(args)
    if args.command == "screenshot":
        return command_screenshot(args)
    if args.command == "scene":
        return command_scene(args)
    if args.command == "remember-scene":
        return command_remember_scene(args)
    if args.command == "suggest":
        return command_suggest(args)
    if args.command == "learn-task":
        return command_learn_task(args)
    if args.command == "confirm-learning":
        return command_confirm_learning(args)
    if args.command == "run-task":
        return command_run_task(args)
    if args.command == "feedback":
        return command_feedback(args)
    if args.command == "press":
        return command_press(args)
    if args.command == "classify":
        return command_classify(args)
    if args.command == "background-learn":
        return command_background_learn(args)
    if args.command == "flush-learn":
        return command_flush_learn(args)
    if args.command == "namespaces":
        return command_namespaces(args)
    if args.command == "card-model-import":
        return command_card_model_import(args)
    if args.command == "export-torchvision":
        return command_export_torchvision(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
