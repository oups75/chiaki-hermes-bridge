# Repository Guidelines

## Project Structure & Module Organization

This repository contains the Hermes/Chiaki bridge used by the `ps-main` Hermes profile to control and inspect a live PlayStation session through Chiaki.

- `scripts/chiaki_remote_gateway.py`: command-line gateway for RemoteController discovery, button presses, screenshots, scene learning, and card imports.
- `scripts/chiaki_mcp_server.py`: MCP wrapper around the gateway. It requires the Python `mcp` package.
- `scripts/scene_learning.py`: scene embedding, CLIP classification helpers, learning-store persistence, and dataset export support.
- `skills/chiaki-bridge/`: agent-facing skill documentation installed or mirrored into the `ps-main` profile.

Runtime learning data is stored outside the repo, normally under `~/.local/share/chiaki-remote-gateway/learning/`. Do not commit generated screenshots, caches, or local scene state.

## Build, Test, and Development Commands

No compile step exists. Validate changes with focused Python and gateway checks:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/chiaki_remote_gateway.py --help
HOME=/home/soloway python3 scripts/chiaki_remote_gateway.py status
HOME=/home/soloway python3 scripts/chiaki_remote_gateway.py screenshot --output /tmp/chiaki-current.png
```

Use live `status` or `screenshot` only when a Chiaki session should already be running. Production checks should use the `ps-main` installed script path under `/home/soloway/.hermes/profiles/ps-main/skills/chiaki-bridge-setup/scripts/`. For MCP changes, verify dependencies first; a missing `mcp` module is an environment issue, not necessarily a code failure.

## Coding Style & Naming Conventions

Use Python 3 with 4-space indentation, type hints for public helpers, and `Path` for filesystem paths. Keep gateway output JSON-serializable and stable because callers parse it. Prefer explicit constants for button names, namespaces, paths, and default URLs. Scene labels should be lowercase, descriptive, and namespace-aware, for example `nhl26 hut auction search results`.

## Testing Guidelines

There is no committed test suite yet. Add tests near new logic when behavior can be isolated without a live Chiaki session. Name tests after behavior, such as `test_slugify_handles_empty_label`. For live checks, avoid destructive actions: start with `status`, then `screenshot`, and only use `press` when the visible scene and intended input are confirmed.

## Commit & Pull Request Guidelines

Git history uses short imperative subjects, for example `Add --approve flag to scene/classify` and `Sync BUTTON_NAMES to actual RemoteController mapping`. Keep commits focused on one behavior or workflow.

Pull requests should include a concise summary, commands run, live-session assumptions, and screenshots or JSON snippets when UI state, scene learning, or controller output changes. Link related issues when available and call out any new environment variables or external dependencies.

## Agent-Specific Instructions

Do not kill, restart, or relaunch Chiaki when `status` reports `replica_available: true`. Treat the active RemoteController replica as authoritative. Prefix live helper commands with `HOME=/home/soloway` to avoid Hermes profile contamination. For unknown scenes, ask for confirmation before saving labels or pressing buttons.
