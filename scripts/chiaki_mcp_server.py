#!/usr/bin/env python3
"""
MCP server for Chiaki PlayStation remote control.

Wraps chiaki_remote_gateway.py as async subprocesses so the blocking
PySide6/Qt event loop runs in isolation from the MCP server loop.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field, field_validator, ConfigDict
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GATEWAY = Path(__file__).parent / "chiaki_remote_gateway.py"
PYTHON = sys.executable

BUTTON_NAMES = [
    "cross", "circle", "box", "triangle",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
    "l1", "r1", "l3", "r3",
    "options", "touchpad", "ps", "none",
]
BUTTON_LITERAL = ", ".join(BUTTON_NAMES)

DEFAULT_NAMESPACE = os.environ.get("CHIAKI_LEARNING_NAMESPACE", "ps")
DEFAULT_REMOTE_URL = os.environ.get("CHIAKI_REMOTE_CONTROLLER_URL", "auto")
DEFAULT_SCREENSHOT_PATH = "/tmp/chiaki-current.png"

mcp = FastMCP("chiaki_mcp")


# ---------------------------------------------------------------------------
# Core subprocess helper
# ---------------------------------------------------------------------------

async def _run_gateway(*args: str, timeout: float = 30.0) -> dict:
    """Run gateway script as subprocess, parse JSON stdout."""
    env = os.environ.copy()
    env["HOME"] = "/home/soloway"

    proc = await asyncio.create_subprocess_exec(
        PYTHON, str(GATEWAY), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"ok": False, "error": f"Gateway timed out after {timeout}s"}

    text = stdout.decode().strip()
    if not text:
        return {
            "ok": False,
            "error": stderr.decode().strip() or "No output from gateway",
            "returncode": proc.returncode,
        }

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON from gateway", "raw": text[:500]}


def _fmt(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Pydantic input models
# ---------------------------------------------------------------------------

class RemoteParams(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    remote_url: str = Field(
        default=DEFAULT_REMOTE_URL,
        description="RemoteController URL. 'auto' = local-first, 'lan-auto' = LAN scan.",
    )
    timeout_ms: int = Field(
        default=15000,
        description="Max wait time in ms for RemoteController replica.",
        ge=1000, le=120000,
    )


class NamespaceParams(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    namespace: str = Field(
        default=DEFAULT_NAMESPACE,
        description="Learning namespace (e.g. 'ps', 'nhl26', 'fifa26').",
        min_length=1, max_length=64,
    )


class SceneParams(NamespaceParams):
    scene_threshold: float = Field(
        default=0.88,
        description="Cosine similarity threshold for scene matching (0–1).",
        ge=0.0, le=1.0,
    )


class PressInput(RemoteParams):
    button: str = Field(
        ...,
        description=f"Button to press. One of: {BUTTON_LITERAL}",
    )
    interval_ms: Optional[int] = Field(
        default=None,
        description="Press duration in ms (default: controller default ~120ms).",
        ge=10, le=5000,
    )

    @field_validator("button")
    @classmethod
    def validate_button(cls, v: str) -> str:
        if v not in BUTTON_NAMES:
            raise ValueError(f"Unknown button '{v}'. Valid: {BUTTON_LITERAL}")
        return v


class ScreenshotInput(RemoteParams):
    output: str = Field(
        default=DEFAULT_SCREENSHOT_PATH,
        description="Output PNG path.",
    )
    window_fallback: bool = Field(
        default=True,
        description="Fall back to X11 window capture if RemoteController has no frame.",
    )


class SceneInput(SceneParams, ScreenshotInput):
    pass


class RememberSceneInput(SceneInput):
    label: str = Field(..., description="Scene label to store.", min_length=1, max_length=128)


class SuggestInput(ScreenshotInput):
    goal: str = Field(
        default="Suggest the next safest useful controller action.",
        description="Natural language goal for the AI action advisor.",
        max_length=512,
    )


class FeedbackInput(SceneInput):
    sentiment: str = Field(
        ...,
        description="Feedback: 'good'/'ok'/'positive' or 'bad'/'no'/'negative'.",
    )
    note: str = Field(default="", description="Optional free-text note.", max_length=512)

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: str) -> str:
        positive = {"ok", "good", "yes", "positive"}
        negative = {"bad", "non", "no", "negative"}
        if v.lower() not in positive | negative:
            raise ValueError("sentiment must be ok/good/positive or bad/non/no/negative")
        return v


class LearnTaskInput(SceneInput):
    goal: str = Field(..., description="Natural language goal for the task to learn.", min_length=1, max_length=512)


class ConfirmLearningInput(SceneInput):
    goal: str = Field(..., description="Goal that was passed to learn-task.", min_length=1, max_length=512)
    expected_label: str = Field(..., description="Expected scene label after the action.", min_length=1, max_length=128)
    action: Optional[str] = Field(default=None, description=f"Override action button. One of: {BUTTON_LITERAL}")
    transition_ms: float = Field(default=0.0, description="Observed transition time in ms.", ge=0.0)


class RunTaskInput(SceneInput, RemoteParams):
    goal: str = Field(..., description="Natural language goal matching a learned task.", min_length=1, max_length=512)
    interval_ms: Optional[int] = Field(default=None, description="Button press interval ms override.", ge=10, le=5000)
    max_steps: int = Field(default=25, description="Max task steps to execute.", ge=1, le=100)


class CardImportInput(NamespaceParams):
    hutbuilder_output: str = Field(
        default="/run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/hutbuilder/output",
        description="Path to hutbuilder output directory containing images/ and cards.jsonl.",
    )
    card_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    batch_size: int = Field(default=32, ge=1, le=256)


class ExportTorchvisionInput(NamespaceParams):
    output_dir: str = Field(
        default="/home/soloway/.local/share/chiaki-remote-gateway/exports",
        description="Output directory for torchvision dataset.",
    )


class FlushLearnInput(NamespaceParams):
    buffer_threshold: int = Field(default=5, ge=1, le=100)
    scene_threshold: float = Field(default=0.88, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="chiaki_status",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def chiaki_status(params: RemoteParams) -> str:
    """Check Chiaki and RemoteController status.

    Returns:
        JSON with: py_side6_available, chiaki_running, chiaki_wrapper_exists,
        remote_url, replica_available, replica_error.
    """
    args = ["--remote-url", params.remote_url, "--timeout-ms", str(min(params.timeout_ms, 3000)), "status"]
    return _fmt(await _run_gateway(*args, timeout=10.0))


@mcp.tool(
    name="chiaki_discover_remote",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def chiaki_discover_remote(params: RemoteParams) -> str:
    """Discover available RemoteController endpoints (local + LAN scan).

    Returns:
        JSON with: local status, lan_candidates list, all candidates.
    """
    args = ["--remote-url", params.remote_url, "--timeout-ms", str(params.timeout_ms), "discover-remote"]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 5.0))


@mcp.tool(
    name="chiaki_wait",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_wait(params: RemoteParams) -> str:
    """Launch Chiaki if not running, then wait for RemoteController replica.

    Use before any other tool when status shows Chiaki is not running.

    Returns:
        JSON with: launched_chiaki, chiaki_running, replica_available, replica_error.
    """
    args = ["--remote-url", params.remote_url, "--timeout-ms", str(params.timeout_ms), "wait"]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 5.0))


@mcp.tool(
    name="chiaki_wait_session",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_wait_session(params: RemoteParams) -> str:
    """Launch Chiaki if needed and wait for the PS5 session to be connected and displayed.

    Use as the single on-demand startup call before any PS5 interaction.
    Launches Chiaki, waits for the RemoteController replica, then waits for
    sessionConnectedtoPs to become true (stream active and visible).

    Returns:
        JSON with: ok, launched_chiaki, replica_available, session_connected, waited_ms.
        ok is true only when session_connected is true.
    """
    args = ["--remote-url", params.remote_url, "--timeout-ms", str(params.timeout_ms), "wait-session"]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 10.0))


@mcp.tool(
    name="chiaki_screenshot",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_screenshot(params: ScreenshotInput) -> str:
    """Capture a screenshot from the active Chiaki stream.

    Falls back to X11 window capture when the stream has no cached frame.

    Returns:
        JSON with: ok, output (file path), bytes, source ('remote_controller' or 'x11_window').
        On error: error, diagnosis.
    """
    args = [
        "--remote-url", params.remote_url,
        "--timeout-ms", str(params.timeout_ms),
        "screenshot",
        "--output", params.output,
    ]
    if not params.window_fallback:
        args.append("--no-window-fallback")
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 10.0))


@mcp.tool(
    name="chiaki_press",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_press(params: PressInput) -> str:
    """Press a PlayStation controller button.

    Button is sent via the Chiaki RemoteController Qt Remote Objects replica.

    Args:
        params.button: One of: cross, circle, box, triangle, dpad_up/down/left/right,
                       l1, r1, l3, r3, options, touchpad, ps, none.

    Returns:
        JSON with: ok, button, sent (normalized button name).
    """
    args = [
        "--remote-url", params.remote_url,
        "--timeout-ms", str(params.timeout_ms),
        "press",
    ]
    if params.interval_ms is not None:
        args += ["--interval-ms", str(params.interval_ms)]
    args.append(params.button)
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 5.0))


@mcp.tool(
    name="chiaki_scene",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_scene(params: SceneInput) -> str:
    """Capture screenshot and match against learned scenes using CLIP embeddings.

    Returns:
        JSON with: ok, source, match (matched, score, scene).
    """
    args = _scene_args(params) + ["scene"]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 20.0))


@mcp.tool(
    name="chiaki_remember_scene",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_remember_scene(params: RememberSceneInput) -> str:
    """Capture the current screen and store it as a named scene in the learning store.

    Call only after user confirmation. First use chiaki_scene to check if the
    scene is already known.

    Returns:
        JSON with: ok, match, learned_scene (id, label).
    """
    args = _scene_args(params) + ["remember-scene", params.label]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 20.0))


@mcp.tool(
    name="chiaki_classify",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def chiaki_classify(params: SceneInput) -> str:
    """Classify current screenshot with local CLIP matching and OCR text.

    More thorough than chiaki_scene because it records OCR text and saves a classification record.

    Returns:
        JSON with: ok, matched, label, method, local_score, ocr_text.
    """
    args = _scene_args(params) + ["classify"]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 30.0))


@mcp.tool(
    name="chiaki_actions",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_actions(params: SceneInput) -> str:
    """List available actions for the current screen.

    Combines: screen-visible action labels (via AI menu advisor) and
    learned tasks whose first step matches the current scene.

    Returns:
        JSON with: ok, current_scene, actions (list with id/name/button/type),
        atomic_buttons, tasks.
    """
    args = _scene_args(params) + ["actions", "--current"]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 30.0))


@mcp.tool(
    name="chiaki_suggest",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def chiaki_suggest(params: SuggestInput) -> str:
    """Take a screenshot and ask the AI advisor for the next controller action.

    Returns a single button suggestion with confidence and reasoning.
    Do NOT send the action automatically — show the suggestion to the user first.

    Returns:
        JSON with: ok, suggestion (action, confidence, reason, send), screenshot.
    """
    args = [
        "--remote-url", params.remote_url,
        "--timeout-ms", str(params.timeout_ms),
        "suggest",
        "--output", params.output,
        "--goal", params.goal,
    ]
    if not params.window_fallback:
        args.append("--no-window-fallback")
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 60.0))


@mcp.tool(
    name="chiaki_feedback",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_feedback(params: FeedbackInput) -> str:
    """Record positive or negative feedback for the current scene.

    Captures a screenshot, embeds it, and writes feedback to the learning store.

    Returns:
        JSON with: ok, sentiment, feedback_id, last_action, state, available_actions.
    """
    args = _scene_args(params) + ["feedback", params.sentiment]
    if params.note:
        args += ["--note", params.note]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 30.0))


@mcp.tool(
    name="chiaki_learn_task",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def chiaki_learn_task(params: LearnTaskInput) -> str:
    """Start learning a task: capture current scene, get AI action suggestion.

    After calling this, send the suggested button with chiaki_press, then
    call chiaki_confirm_learning to store the learned step.

    Returns:
        JSON with: ok, needs_user_confirmation, start_match, advisor (suggestion).
    """
    args = _scene_args(params) + ["learn-task", params.goal]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 60.0))


@mcp.tool(
    name="chiaki_confirm_learning",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_confirm_learning(params: ConfirmLearningInput) -> str:
    """Confirm and store a learned task step after user verification.

    Call after chiaki_learn_task + chiaki_press + user confirms result.

    Returns:
        JSON with: ok, task (goal, steps).
    """
    args = _scene_args(params) + [
        "confirm-learning", params.goal,
        "--expected-label", params.expected_label,
    ]
    if params.action:
        args += ["--action", params.action]
    if params.transition_ms:
        args += ["--transition-ms", str(params.transition_ms)]
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 30.0))


@mcp.tool(
    name="chiaki_run_task",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_run_task(params: RunTaskInput) -> str:
    """Execute a previously learned multi-step task.

    Sends button presses and waits for scene transitions at each step.

    Returns:
        JSON with: ok, goal, trace (list of step results with action/transition_ms/state).
    """
    args = _scene_args(params) + [
        "--remote-url", params.remote_url,
        "run-task",
        "--max-steps", str(params.max_steps),
        params.goal,
    ]
    if params.interval_ms is not None:
        args += ["--interval-ms", str(params.interval_ms)]
    timeout = params.timeout_ms / 1000.0 + params.max_steps * 15.0
    return _fmt(await _run_gateway(*args, timeout=timeout))


@mcp.tool(
    name="chiaki_namespaces",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def chiaki_namespaces(params: NamespaceParams) -> str:
    """List all available learning namespaces.

    Returns:
        JSON with: ok, learning_root, namespaces (list), active namespace.
    """
    return _fmt(await _run_gateway("namespaces", timeout=5.0))


@mcp.tool(
    name="chiaki_background_learn",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_background_learn(params: ScreenshotInput) -> str:
    """Capture a screenshot and queue it for background scene learning.

    Returns immediately. Spawns background flush when buffer threshold is reached.

    Returns:
        JSON with: ok, queued, buffer_size, threshold, ready, flush_spawned.
    """
    args = [
        "--remote-url", params.remote_url,
        "--timeout-ms", str(params.timeout_ms),
        "background-learn",
        "--output", params.output,
    ]
    if not params.window_fallback:
        args.append("--no-window-fallback")
    return _fmt(await _run_gateway(*args, timeout=params.timeout_ms / 1000.0 + 15.0))


@mcp.tool(
    name="chiaki_flush_learn",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def chiaki_flush_learn(params: FlushLearnInput) -> str:
    """Process all queued background screenshots: embed + classify + store unknown scenes.

    Returns:
        JSON with: ok, flushed, processed, matched, unknown, errors, total_scenes.
    """
    args = [
        "--namespace", params.namespace,
        "flush-learn",
        "--scene-threshold", str(params.scene_threshold),
        "--buffer-threshold", str(params.buffer_threshold),
    ]
    return _fmt(await _run_gateway(*args, timeout=120.0))


@mcp.tool(
    name="chiaki_card_model_import",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def chiaki_card_model_import(params: CardImportInput) -> str:
    """Import HUT builder card images into the learning store as scenes.

    Embeds each card image with CLIP ViT-B/32 and skips already-known cards.

    Returns:
        JSON with: ok, namespace, total_images, imported, skipped, errors, total_scenes.
    """
    args = [
        "--namespace", params.namespace,
        "card-model-import",
        "--hutbuilder-output", params.hutbuilder_output,
        "--card-threshold", str(params.card_threshold),
        "--batch-size", str(params.batch_size),
    ]
    return _fmt(await _run_gateway(*args, timeout=600.0))


@mcp.tool(
    name="chiaki_export_torchvision",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def chiaki_export_torchvision(params: ExportTorchvisionInput) -> str:
    """Export the learning store to torchvision dataset format.

    Returns:
        JSON with: ok, output directory, scene count, export stats.
    """
    args = [
        "--namespace", params.namespace,
        "export-torchvision",
        "--output-dir", params.output_dir,
    ]
    return _fmt(await _run_gateway(*args, timeout=120.0))


# ---------------------------------------------------------------------------
# Shared arg builder
# ---------------------------------------------------------------------------

def _scene_args(params) -> list[str]:
    """Build common args from any model that has RemoteParams + SceneParams fields."""
    args = []
    if hasattr(params, "remote_url"):
        args += ["--remote-url", params.remote_url]
    if hasattr(params, "timeout_ms"):
        args += ["--timeout-ms", str(params.timeout_ms)]
    if hasattr(params, "namespace"):
        args += ["--namespace", params.namespace]
    return args


# ---------------------------------------------------------------------------
# Persistent replica (signal/slot, cached latest-state)
# ---------------------------------------------------------------------------

import atexit  # noqa: E402
import base64  # noqa: E402
import threading  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import chiaki_replica  # noqa: E402

DEFAULT_REMOTE_NAME = os.environ.get("CHIAKI_REMOTE_CONTROLLER_NAME", "RemoteController")
LOCAL_REMOTE_URL = "local:chiaki-current-session"

_manager = None
_manager_lock = threading.Lock()


def _resolve_persistent_url() -> str:
    url = DEFAULT_REMOTE_URL
    if not url or url == "auto":
        return LOCAL_REMOTE_URL
    return url


def _get_manager():
    """Lazily start the single persistent replica manager (thread-safe)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            mgr = chiaki_replica.ReplicaManager(_resolve_persistent_url(), DEFAULT_REMOTE_NAME)
            mgr.start(ready_timeout=5.0)
            atexit.register(mgr.stop)
            _manager = mgr
        return _manager


def _events_json_safe(events: list) -> list:
    safe = []
    for ev in events:
        item = dict(ev)
        shot = item.pop("screenshot", None)
        if shot is not None:
            try:
                item["screenshot_bytes"] = len(shot)
            except Exception:
                item["screenshot_bytes"] = 0
        safe.append(item)
    return safe


@mcp.tool(
    name="chiaki_live_status",
    description="Persistent-replica status (no per-call reconnect): replica_available, session_connected, last_button, cached event/screenshot sizes.",
)
async def chiaki_live_status() -> str:
    snap = _get_manager().state.snapshot()
    return _fmt({
        "ok": True,
        "remote_url": _resolve_persistent_url(),
        "replica_available": snap["replica_available"],
        "session_connected": snap["session_connected"],
        "last_button": snap["last_button"],
        "event_count": len(snap["events"]),
        "screenshot_bytes": len(snap["screenshot"]),
    })


@mcp.tool(
    name="chiaki_session_state",
    description="Latest cached Chiaki session state (opened/closed) from the persistent replica.",
)
async def chiaki_session_state() -> str:
    snap = _get_manager().state.snapshot()
    return _fmt({
        "ok": True,
        "session_connected": snap["session_connected"],
        "replica_available": snap["replica_available"],
    })


@mcp.tool(
    name="chiaki_recent_events",
    description="Recent controller/keyboard events captured via the persistent replica (newest last; screenshot bytes summarised).",
)
async def chiaki_recent_events(count: int = 50) -> str:
    events = _get_manager().state.recent_events(max(1, min(count, 256)))
    return _fmt({"ok": True, "count": len(events), "events": _events_json_safe(events)})


@mcp.tool(
    name="chiaki_live_screenshot",
    description="Request a fresh frame via the persistent replica and return the cached PNG (base64). No per-call reconnect.",
)
async def chiaki_live_screenshot(wait_ms: int = 1500) -> str:
    mgr = _get_manager()
    before_seq = mgr.state.snapshot()["screenshot_seq"]
    mgr.request_screenshot()
    # Only accept a frame observed AFTER this request; a cached frame with an
    # unchanged sequence is stale (session loss / no signal / timeout).
    budget_ms = max(0, min(wait_ms, 5000))
    waited = 0
    snap = mgr.state.snapshot()
    while snap["screenshot_seq"] == before_seq and waited < budget_ms:
        await asyncio.sleep(0.05)
        waited += 50
        snap = mgr.state.snapshot()
    if snap["screenshot_seq"] == before_seq or not snap["screenshot"]:
        return _fmt({
            "ok": False,
            "error": "no fresh frame",
            "replica_available": snap["replica_available"],
            "session_connected": snap["session_connected"],
        })
    data = snap["screenshot"]
    return _fmt({"ok": True, "png_base64": base64.b64encode(data).decode("ascii"), "bytes": len(data)})


@mcp.tool(
    name="chiaki_live_press",
    description="Send a button via the persistent replica (no per-call reconnect). Returns the last observed button from cache.",
)
async def chiaki_live_press(button: str, interval_ms: Optional[int] = None) -> str:
    if button not in BUTTON_NAMES:
        return _fmt({"ok": False, "error": f"invalid button: {button}"})
    mgr = _get_manager()
    # press() reports whether the replica actually accepted the input; do not
    # claim success when the replica is unavailable or rejected the property.
    result = await asyncio.to_thread(mgr.press, button, interval_ms)
    snap = mgr.state.snapshot()
    return _fmt({
        "ok": bool(result.get("ok")),
        "button": button,
        "error": result.get("error"),
        "last_button": snap["last_button"],
        "session_connected": snap["session_connected"],
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
