---
name: chiaki-bridge
description: Use this skill whenever the user asks to launch, verify, control, screenshot, classify, learn scenes, identify cards, or troubleshoot PlayStation/PS5 games through Chiaki and Hermes, especially NHL26 and HUT workflows. Covers the current RemoteController Qt Remote Objects gateway, PySide6 helper commands, CLIP scene/card recognition, per-game namespaces, NHL26/HUT learned navigation, startup/profile pitfalls, and legacy Hermes bridge failures.
compatibility: Requires Python 3, PySide6 with QtRemoteObjects, and a Chiaki build that exposes `RemoteController` at `local:chiaki-current-session`. Scene learning requires `torch`, `transformers`, Pillow, and `openai/clip-vit-large-patch14` by default.
---

# Chiaki Bridge Setup

## Purpose

Use this skill for PlayStation control through Chiaki on this machine:

- launch or verify Chiaki in the `ps-main` Hermes profile
- connect to the current `RemoteController` replica
- press controller buttons
- capture screenshots from `getScreenShot()`
- classify/remember PlayStation and game scenes with CLIP
- identify NHL26/HUT cards from imported card images
- run learned tasks only after scene confirmation
- debug old `local:hermes_chiaki_bridge` bridge setup when explicitly needed

Prefer the current RemoteController path. Use the legacy Hermes bridge path only when troubleshooting old setup or old logs.

## Safety Invariant

Never kill, restart, replace, or relaunch Chiaki when the `RemoteController` replica is available.

If `status` reports `replica_available: true`, treat the active Chiaki process as authoritative even when `chiaki_running` is false due to a wrong `--process-pattern`, profile wrapper mismatch, or build-vs-prod binary mismatch. Update the process pattern or continue using the replica; do not terminate Chiaki.

## Current Architecture

Current control path:

- Source object: `RemoteController`
- Default local URL: `local:chiaki-current-session`
- Optional LAN URL: `tcp://0.0.0.0:15432`
- Wrapper: `/home/soloway/.hermes/profiles/ps-main/bin/chiaki-launcher`
- Chiaki binary: `/run/media/soloway/workspace/prod/games/ps/chiaki/bin/chiaki`
- Bridge root: `/run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge`
- Gateway script: `/run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py`
- MCP server: `/run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_mcp_server.py`
- Learning root: `/home/soloway/.local/share/chiaki-remote-gateway/learning/`
- HUT card source: `/run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/hutbuilder/output/`

Always prefix host-side helper commands with `HOME=/home/soloway` to avoid Hermes profile site-package contamination.

Use the Hermes `ps-main` profile as the control surface. Direct gateway commands are allowed for bridge diagnostics and script-level verification only; routine PlayStation control should run through Hermes with the `chiaki-bridge` skill loaded from the production bridge path.

Learning advisor model:

- Use the current Hermes `ps-main` model/provider as the advisor model.
- Do not hard-code a model in the skill; if the user switches `ps-main` to a Codex model, that Codex model becomes the advisor.
- The advisor proposes state labels, action hypotheses, route candidates, and training metadata from screenshots.
- The advisor does not override safety rules: unknown or conflicting next actions still require user confirmation before pressing buttons.

## Routine Commands

Run from any directory:

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py status
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py wait
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py screenshot --output /tmp/chiaki-current.png
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py press cross
```

All gateway commands accept:

- `--remote-url URL`
- `--remote-name RemoteController`
- `--learning-root PATH`
- `--namespace NS` or `-n NS`

Button names:

`cross`, `moon`/`circle`, `box`/`square`, `pyramid`/`triangle`, `d-pad_up`, `d-pad_down`, `d-pad_left`, `d-pad_right`, `l1`, `r1`, `l2`, `r2`, `l3`, `r3`, `left_stick_up`, `left_stick_down`, `left_stick_left`, `left_stick_right`, `options`, `touchpad`, `ps`, `none`

Aliases (`circle`, `square`, `triangle`, `dpad_*`, `lstick_*`, `rstick_*`) resolve automatically.

## Gateway Subcommands

Basic control:

- `status` checks process state, PySide6 availability, selected remote URL, and replica availability.
- `wait` launches Chiaki if needed and waits for `RemoteController`.
- `screenshot` calls `getScreenShot()` and writes PNG output.
- `press BUTTON` sends one controller button to the active session.

Scene learning:

- `scene` matches the current screenshot against learned scenes.
- `remember-scene` records a labeled scene after user confirmation.
- `classify` runs CLIP-based classification against candidates.
- `background-learn` queues non-blocking screenshots.
- `flush-learn` processes queued screenshots from `learning/buffer/`.
- `feedback good|bad` records scene feedback.

Task learning:

- `learn-task` records a multi-step route.
- `confirm-learning` promotes learned route data after verification.
- `run-task` executes learned route steps using stored timing.

Dataset/card import:

- `card-model-import --namespace nhl26` imports HUT builder card images with metadata.
- `namespaces` lists learning namespaces.
- `export-torchvision` exports learned scene data for training.

## Scene Learning Rules

Learning store is namespaced: `learning/<namespace>/`.

Common namespaces:

- `ps`
- `nhl26`
- `nhl25`
- `nhl-common`
- `fifa26`

Use namespaces deliberately:

- PlayStation home/system UI: `--namespace ps`
- NHL26 UI, HUT menus, active NHL26 routes: `--namespace nhl26`
- Shared NHL/HUT patterns that transfer across yearly games: `--namespace nhl-common`
- Older NHL25-only screens: `--namespace nhl25`

When the user says "PlayStation", "PS5", "PS games", or asks to inspect the console without naming a game, default to `ps`. When they say "NHL26", "HUT", "cards", "auction", "squad", "objectives", or "packs", default to `nhl26`. Use `nhl-common` only for shared generic NHL navigation patterns, not current NHL26 card/player identity.

Scene metadata should include:

- `page`
- `active_element`
- `available_actions`
- `game` for game-specific scenes, e.g. `nhl26`
- `permanent_regions` for stable UI geometry, e.g. toolbar, title, status row, page number, selected focus box
- `analysis_lag_ms` from screenshot capture time to detector/model analysis time
- optional `card_id`
- optional `player_name`
- optional `card_type`

Unknown scene policy:

- User is the primary adviser.
- Unknown scene means stop and ask user for step-by-step directives.
- Never guess action sequences.
- The current Hermes model may summarize evidence and propose labels/actions, but it must mark them unconfirmed and ask the user before pressing or saving.
- First-time classifications require explicit user confirmation before saving.
- Remember scenes only after confirmation.

NHL/HUT card identification should focus on card ID and player name before card category.

## Task State Screenshot Learning

During task execution, every screenshot is training data, including frames between the user's task request and the final action result.

For each captured frame, record:

- task name
- step index
- namespace, usually `nhl26`
- `captured_at`
- `analyzed_at`
- `analysis_lag_ms`
- action before or after the frame, if any
- result state: `pending`, `ok`, or `ko`
- detected permanent regions
- advisor model/provider from current Hermes `ps-main` config
- advisor hypothesis, if used

Keep `analysis_lag_ms` as close to zero as practical. Prefer the fastest reliable detector for action gating:

1. Permanent-region/geometry detector
2. Cheap OCR for title, status, and page number
3. Local scene embedding
4. Full CLIP/OCR classification

Drop stale frames for live decisions during fast transitions, but keep them as training samples with lag metadata.

Use the current Hermes model as learning advisor after fast detectors run. Advisor output should include:

- state hypothesis
- visible action prompts
- next safe edge candidates
- confidence and uncertainty
- whether user confirmation is required
- ok/ko training summary after the task result

The advisor should prefer evidence from fresh screenshots, YOLO permanent regions, OCR prompts, and local scene matches. If advisor output conflicts with visible prompts or graph preconditions, ask the user.

## YOLO Permanent Region Training

Use stable screen regions as YOLO training targets when they identify page or state:

- top toolbar
- page title
- status strip
- page number
- selected row/card focus box
- modal/dialog title
- loading indicator
- disabled or enabled action prompt

Dynamic content should not overwrite permanent-region labels. Example: an auction page may have the same toolbar and page number while card tiles are still loading. Label both:

- permanent page identity regions
- dynamic content readiness regions

Use YOLO detections for fast state gating before heavier models. Example: if page number changes, page transition happened; if card grid readiness box is absent, wait before treating the page as loaded.

When the user marks a task result `ok`, promote all region labels and state transitions as positive training data. When the result is `ko`, keep the full sequence as negative data for recovery and next-cycle training.

## Game State Graph Learning

Treat each game page as a state in a directed graph. Treat each confirmed button press, wait, page load, or menu transition as an edge from one state to another.

State records should include:

- namespace and game, e.g. `nhl26`
- page label
- permanent region detections
- dynamic readiness detections
- selected element or focus location
- available actions shown on screen
- screenshot references
- confidence score

Edge records should include:

- source state
- target state, when known
- action or wait condition
- held buttons or modifier state, if any
- press/release timing for multi-key actions
- required preconditions
- observed delay range
- intermediate screenshots
- result: `pending`, `ok`, `ko`, or `recovered`

Learn recursive, linear, branching, and parallel task paths as graph routes, not only flat button lists. Use the graph to choose the next safe action only when the current state is freshly detected and matches the route preconditions.

Some task edges are multi-key or held-modifier interactions. Represent these as explicit action phases, not one flat button name:

1. press and hold modifier, e.g. `l2`
2. navigate while held, e.g. stick scroll or d-pad movement
3. detect selected element/focus change
4. release modifier
5. confirm resulting activation or state transition

Held-button state changes available actions. Example: holding `l2` while scrolling a menu can select a different element or mode than scrolling without `l2`. Store the held state in the graph edge preconditions and in every screenshot captured during the chord.

Most game pages show available actions in permanent screen positions, often near bottom or side toolbars. Detect and store these action-prompt regions as first-class permanent regions:

- button glyph or text, e.g. `X SELECT`, `O BACK`, `L2 FILTER`, `R1 NEXT`
- region bounding box
- page/state where prompt appears
- whether prompt changes under held modifiers
- whether prompt is enabled, disabled, hidden, or loading

Use visible action prompts to constrain the next graph edge. If the needed action for the next task step is not visible, not detected, or conflicts with current state, stop and ask the user before pressing.

## PlayStation/Game CLIP Workflow

CLIP is for visual state recognition, not autonomous decision-making. Use it to answer: "what screen/card/menu is visible?" Then use learned routes or user instructions for actions.

For any PlayStation/game context:

1. Ensure the stream is ready:

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py wait-session
```

2. Capture and match the scene in the right namespace:

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py --namespace ps scene
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py --namespace nhl26 scene
```

3. Use `classify` only when local scene match is missing or low-confidence. It uses local CLIP embeddings and OCR only; there is no remote model fallback:

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py --namespace nhl26 classify --keep-screenshot
```

4. If result is unknown, ask the user what the visible screen is before pressing buttons or saving a route.

5. After user confirms a label, save it with a game-specific name:

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py --namespace nhl26 remember-scene "nhl26 hut auction search results"
```

Use labels that encode game + mode + page + active element when possible, for example:

- `ps home game tile chiaki`
- `nhl26 hut main menu`
- `nhl26 hut auction search results`
- `nhl26 hut item details card focused`
- `nhl26 world of chel main menu`

Never learn vague labels like `menu`, `screen1`, or `unknown`.

## NHL26/HUT Card Recognition

For NHL26 card workflows, seed CLIP with HUT builder card images before relying on screenshots:

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py --namespace nhl26 card-model-import --hutbuilder-output /run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/hutbuilder/output/
```

The import stores labels as `card-<card_id>` with metadata:

- `card_id`
- `player_name`
- `card_type`
- `source: hutbuilder-import`
- `page: card_view`

When a visible NHL26 card is in focus:

1. Run `--namespace nhl26 scene` or `classify`.
2. Prefer exact `card_id` metadata over category/card type.
3. Report player name and card ID together when available.
4. If CLIP score is below threshold, keep screenshot and ask user before recording feedback.

Use `feedback good|bad` after user confirms whether a match was correct. Good feedback strengthens trusted labels; bad feedback prevents repeating wrong card/menu assumptions.

## CLIP Embedder

Scene embeddings use `openai/clip-vit-large-patch14` through Hugging Face `transformers` by default. Override with `CHIAKI_VISION_MODEL` only after running `scripts/evaluate_vision_models.py` on current learning screenshots.

Current local evaluation on 55 saved Chiaki screenshots ranked:

1. `openai/clip-vit-large-patch14` — top-1 `0.8261`
2. `google/siglip-base-patch16-224` — top-1 `0.7826`
3. `openai/clip-vit-base-patch32` — top-1 `0.7391`

Dependency check:

```bash
HOME=/home/soloway python3 -c "from transformers import CLIPModel, CLIPProcessor; model = CLIPModel.from_pretrained('openai/clip-vit-large-patch14'); print('CLIP OK dim:', model.config.projection_dim)"
```

Important pitfall:

- In transformers 5.x, `CLIPModel.get_image_features()` and `get_text_features()` return a dict-like `BaseModelOutputWithPooling`, not a raw tensor.
- Extract `['pooler_output']` before normalization.
- Do not use `.image_embeds`; it is not present on this return object.
- Do not use `['last_hidden_state']` for similarity; it is sequence output, not pooled CLIP embedding.
- Local scene matching compares normalized CLIP image embeddings with cosine similarity; default threshold is `0.88`.
- For NHL26 cards, start with the default card threshold, then lower only when user accepts more false positives.

Embedding dimension:

- CLIP ViT-L/14 produces 768-dimensional normalized embeddings.
- CLIP ViT-B/32 produces 512-dimensional normalized embeddings and is now legacy for this bridge.
- Old ResNet50 embeddings were 2048-dimensional and are incompatible. Re-learn old scenes after switching stores/models.

See nested reference:

`chiaki-clip-embedder/SKILL.md`

## Cron Jobs

Expected scheduled jobs:

- `chiaki-torchvision-export`: daily export to `/home/soloway/.local/share/chiaki-remote-gateway/exports`.
- `nhl-hutbuilder-daily-learning`: 22:01 CET, scrape HUT builder data, import new card images, export updated store.
- `sleeveless-nhl26-transcripts`: 03:00 CET, YouTube transcript fetch for NHL26 videos.

Keep cron prompts pointing at:

`/run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py`

Keep cron skill name as:

`chiaki-bridge-setup`

## Startup Verification

Preferred routine (on-demand, single call):

```bash
HOME=/home/soloway python3 /run/media/soloway/workspace/prod/games/ps/chiaki/hermes-bridge/scripts/chiaki_remote_gateway.py wait-session
```

Or via MCP tool: `chiaki_wait_session`.

This single command:

1. Launches Chiaki via the wrapper if not running.
2. Waits for the `RemoteController` replica to initialize.
3. Polls `sessionConnectedtoPs` every 500 ms until `true` (stream active and visible) or timeout.
4. Returns `{"ok": true, "session_connected": true, "waited_ms": N}` when ready.

Tell the user **"PlayStation ready"** when `ok` and `session_connected` are both `true`.

If `ok` is `false`:

- `replica_available: false` → Chiaki failed to start or Remote Objects not published.
- `session_connected: false`, `replica_available: true` → Replica connected but PS5 stream not active within timeout. Check PS5 is on and not in rest mode.

Legacy multi-step verification (fallback only):

1. Run `status` to check current state.
2. If `replica_available: false`, run `wait` then re-check.
3. Confirm `replica_available: true` and `py_side6_available: true`.
4. Confirm screenshot returns non-empty PNG before proceeding.

## Profile Pitfalls

Wrong profile context can produce false bridge failure.

Old profile-context command:

```bash
HERMES_HOME=/home/soloway/.hermes/profiles/playstation-main python3 skills/playstation-main/scripts/playstation_main.py status
```

Current profile directory on this host is:

`/home/soloway/.hermes/profiles/ps-main`

Use `HOME=/home/soloway` for host helper commands. Use `HERMES_HOME` only for old Hermes profile helper troubleshooting.

Startup may exceed short timeout values:

- Symptom: launch command exits early or appears frozen.
- Cause: `playstation.timeout_ms` too low.
- Fix: raise to `15000` or `20000` and retry.

## Legacy Hermes Bridge Troubleshooting

Legacy bridge:

- URL: `local:hermes_chiaki_bridge`
- Old args:
  - `--hermes`
  - `--hermes-mode=control`
  - `--hermes-ro-url=local:hermes_chiaki_bridge`

Use this only for old installs/logs. Do not prefer it for new control.

If legacy client reports:

`Hermes bridge is not reachable on local:hermes_chiaki_bridge`

Check:

1. Chiaki launched with Remote Objects/Hermes args.
2. Correct profile context.
3. `libchiaki_lib.so.1` exists in Chiaki `lib/`.
4. Qt display and `xcb` plugin are available.
5. Launcher wrapper sets `LD_LIBRARY_PATH`.

Old direct launch example:

```bash
DISPLAY=:1 \
  LD_LIBRARY_PATH=/home/soloway/Applications/Games/ps/chiaki/lib \
  HOME=/home/soloway \
  XAUTHORITY=/home/soloway/.Xauthority \
  /home/soloway/Applications/Games/ps/chiaki/chiaki \
  --remote-objects-enable=1 \
  --remote-objects-url="QProcess::local:hermes_chiaki_bridge" \
  --hermes
```

Old config peer location:

`~/.config/chiaki/remote_objects/config.json`

Minimal old config:

```json
{
  "peers": {
    "hermes_chiaki_bridge": {
      "id": "hermes_chiaki_bridge",
      "replicas": ["QProcess::local:hermes_chiaki_bridge"],
      "enabled": "1"
    }
  },
  "host": {
    "enabled": "1",
    "url": "QProcess::local:hermes_chiaki_bridge"
  }
}
```

Missing library fix:

```bash
cp /run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/chiaki-ng/build/lib/libchiaki_lib.so.1 /home/soloway/Applications/Games/ps/chiaki/lib/
```

## NHL26 HUT STORE Navigation

The STORE left panel (ITEM INBOX / UNOPENED PACKS / PURCHASE NHL POINTS) uses **left stick**, not d-pad:

- `left_stick_right` → selects UNOPENED PACKS (d-pad right goes to carousel panel, skipping UNOPENED PACKS)
- `left_stick_left` → returns to ITEM INBOX
- d-pad `down` from ITEM INBOX → PURCHASE NHL POINTS
- `cross` on ITEM INBOX → enters inbox (INBOX EMPTY if empty)
- `cross` on UNOPENED PACKS → enters pack list view
- `pyramid`/`triangle` from UNOPENED PACKS view → GO TO STORE pack browser

## NHL26 HUT Task: Open Pack and Send to Collection

From STORE tab, ITEM INBOX selected:

```
left_stick_right          # select UNOPENED PACKS
cross                     # enter pack list
cross                     # select first pack → "OPEN NOW / OPEN LATER" dialog
cross                     # OPEN NOW → pack animation, first card revealed
box                       # □ REVEAL ALL (reveals all remaining face-down cards)
l3                        # QUICK OPTIONS → "SEND ALL TO MY COLLECTION / QUICK SELL ALL / CANCEL"
cross                     # SEND ALL TO MY COLLECTION → "ITEM(S) SENT TO COLLECTION"
```

Notes:
- `box` = square button (alias `square` also works after gateway fix)
- `l3` = left stick click = QUICK OPTIONS shortcut for batch collection/sell
- Pack opening screenshots saved to: `learning/screenshots/ps/tasks/open-pack/`
- Use visual state machine (screenshot-driven polling) — never fixed-time waits; animations vary per pack

## CHOICE PACK Rule — NEVER Auto-Open

Choice packs (title contains "CHOICE PACK", bottom text shows "Choose N Item(s) / N Selection Round") require manual player selection. The game shows a player list before revealing cards.

**Automation must NEVER press OPEN NOW on a choice pack.**

Detection: read pack title text from the UNOPENED PACKS list view — title is visible on screen when pack is selected/highlighted, before any dialog opens. If "CHOICE" in title → do NOT press cross. No need to open the dialog to detect a choice pack.

Recovery (if OPEN NOW accidentally pressed): press circle/moon immediately to back out before selection round starts. If already in selection round, make a pick — do NOT abandon mid-selection.

Choice packs to open manually via player list UI, not batch automation.

## Debugging Checklist

- `HOME=/home/soloway` used for host helper command.
- Current gateway script path exists.
- `status` returns `py_side6_available: true`.
- Chiaki process matches expected binary.
- Remote URL is current `local:chiaki-current-session` unless explicitly debugging legacy.
- `RemoteController` source is published.
- For screenshots, decoded frame cache exists.
- For learned scenes, namespace is explicit.
- Unknown scene decisions came from user, not model guess.
