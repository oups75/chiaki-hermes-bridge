---
name: tasktree
description: Use to create, organize, run, and track hierarchical tasks via the tasktree MCP server (Soloway.QtTaskTree over Qt6::TaskTree). Trigger when the user wants a task list / work plan that can be executed and whose progress is tracked across the session.
---

# Task Tree

Generic hierarchical task manager exposed by the `tasktree` MCP server
(`http://127.0.0.1:8791/mcp`, see `.mcp.json`). Tasks form a tree; leaf tasks of
type `Process` execute real commands on the Qt6::TaskTree engine, `Group` nodes
compose children Sequentially / in Parallel. Status and live run progress are
persisted (CouchDB when `AGENTKIT_COUCHDB_URL` is set, else in-memory) and stream
over SSE.

## Start the server

```bash
scripts/tasktree_mcp.sh            # port 8791 (TASKTREE_MCP_PORT to override)
```

Reload the MCP connection afterwards so the `mcp__tasktree__*` tools appear.

## Tools

- `task_add` — `{parent_id?, title, type, payload?, deps?}` → `{id}`.
  `type`: `Group | Process | Manual | Network | Nested`.
  Process `payload`: `{command, args?}`. Group `payload`: `{mode: Sequential|Parallel|ParallelLimit, limit?}`.
- `task_update` — `{id, fields}` (title/status/type/payload/deps).
- `task_move` — `{id, new_parent, order}`.
- `task_remove` — `{id}` (removes subtree).
- `task_set_status` — `{id, status}` (`Todo|Ready|Running|Done|Failed|Blocked`).
- `task_list_tree` — `{root_id?}` → nested tree snapshot.
- `task_run` — `{root_id}` executes the subtree; progress streams over SSE.
- `task_stop` — cancels the active run.
- `task_next` — `{root_id?}` → next runnable leaf (Todo/Ready, deps satisfied).

## Typical flow

1. `task_add` a `Group` (e.g. "deploy"), then child `Process` tasks under it.
2. `task_next` to see what runs next; `task_run` the group to execute.
3. Watch status via `task_list_tree` or the SSE `/events` stream.

## Notes

- Manual / Network task types are placeholders in this PoC (succeed immediately).
- `deps` drive `task_next` readiness; intra-group DAG wiring is not yet executed.
