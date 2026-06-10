# Chiaki Task UI — context-aware learned-task manager (design)

## Context

The `taskui/` app (chiaki PS5 task manager over `Soloway.QtTaskTree`) currently does
CRUD + run on learned tasks. This adds the features needed to make it a practical,
live tool: fit-to-window layout, user-editable status, drag-drop task composition,
task preconditions (initial state), live sync of newly-learned tasks, a learned-task
approval workflow, and a dynamic mode that filters tasks by the live PlayStation
screen.

## Decisions (locked)

- **Current context** = gateway `classify` **on-demand** (a Refresh button runs
  `chiaki_remote_gateway.py classify`, returns the current `page`).
- **Preconditions** = per-task `start_scene` + `end_scene` (page labels). Dynamic
  filter matches `start_scene`; composition chains `end_scene → next.start_scene`.
- **Live sync** = **both** `QFileSystemWatcher` on `<ns>/tasks.json` and the existing
  CouchDB `_changes` feed.
- **Approval** = payload `source` (`learned`|`user`) + `approved` (bool). Learned land
  `approved:false` (pending); Approve sets `approved:true`.

## Data model

All new fields live in `TaskItem.payload` (a `QVariantMap`) — no `TaskItem` struct or
DB schema change; CouchDB docs and `tasks.json` carry them transparently:

| key | type | meaning |
|-----|------|---------|
| `start_scene` | string | page the task must run from |
| `end_scene` | string | page after the task completes (context change) |
| `source` | `"learned"`\|`"user"` | provenance |
| `approved` | bool | learned tasks start false (pending) |

Task lifecycle status stays the existing `TaskTree::Status` enum, now user-editable.

## Phases

### Phase A — UI polish (no model change)
- **Fit width**: `TreeView.columnWidthProvider` returns the view width so rows span
  the window (`forceLayout` on width change).
- **Editable status**: status chip on task rows; tap opens a `Menu` of the six
  statuses → `TaskTreeModel.setStatus`.

### Phase B — model + editing
- **StepEditor**: task dialog gains `start_scene` / `end_scene` fields (steps
  unchanged). Writes to `payload`.
- **Approval**: `ChiakiTaskBridge.importJson` tags imported tasks
  `source:"learned", approved:false`; UI "+ Task" creates `source:"user",
  approved:true`. Pending tasks badged; an **Approve** action sets `approved:true`
  (persisted via the model's store).
- **Drag-drop compose**: a `DropArea` on each task row accepts a dragged task and
  calls `TaskTreeModel.moveTask(dragged, target, 0)` to nest it.

### Phase C — integration
- **Live sync**: `ChiakiTaskBridge` gains a `QFileSystemWatcher` on the active
  namespace's `tasks.json`; on change it merges task keys not already in the model
  (preserving in-flight edits). CouchDB `_changes` already reaches the model via
  `CouchTaskStore.startWatching → remoteChanged`.
- **Dynamic mode**: a toggle + **Refresh context** button. `ChiakiTaskBridge.classify()`
  runs the gateway and emits the current `page`. A `SortFilterProxyModel`
  (`QtQml.Models`, per the project QML rules) over `TaskTreeModel` shows tasks whose
  `start_scene == currentPage` (or empty) **plus** context-changers (`end_scene` set),
  so navigation/composed tasks that reach the page stay visible.

## Components touched

- `taskui/qml/Main.qml` — width fix, status menu, approval badge/button, drag-drop,
  dynamic-mode toggle + proxy model.
- `taskui/qml/StepEditor.qml` — start/end scene fields.
- `taskui/ChiakiTaskBridge.{h,cpp}` — `classify()`, file watcher + merge, approval
  defaults on import.
- (generic lib stays unchanged unless a new `Q_INVOKABLE` is required, e.g.
  `mergeTask`/`setPayload`.)

## Testing

- `tst_bridge` extended: import sets `source/approved`; merge adds only new keys;
  `classify()` parses a stubbed gateway output (fake script) → page.
- UI: build clean, `tst_bridge` green, visual checks via Xvfb (width, status menu,
  approve badge, dynamic filter).

## Sequencing

A → B → C, each committed and tested independently.
