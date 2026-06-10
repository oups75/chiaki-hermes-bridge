#!/usr/bin/env bash
# Launch the Soloway.QtTaskTree MCP server (HTTP + SSE) that backs the
# `tasktree` MCP server entry in .mcp.json. The chiaki-bridge agent connects to
# http://127.0.0.1:${TASKTREE_MCP_PORT:-8791}/mcp and gets the task_* tools.
#
# Persistence: set AGENTKIT_COUCHDB_URL (http://admin:pw@127.0.0.1:5984) to use
# CouchDB (db from --db, default kit_tasks); otherwise an in-memory store.
set -euo pipefail

QT_DIR="${QT_DIR:-/run/media/soloway/workspace/Devel/Tools/Qt/6.11.1/gcc_64}"
TASKTREE_ROOT="${TASKTREE_ROOT:-/run/media/soloway/workspace/Devel/Projects/soloway/libs/qt/tasktree}"
BIN="${TASKTREE_ROOT}/build/tasktree-mcp"
PORT="${TASKTREE_MCP_PORT:-8791}"

if [[ ! -x "$BIN" ]]; then
  echo "tasktree-mcp not built at $BIN" >&2
  echo "build it: cmake -S \"$TASKTREE_ROOT\" -B \"$TASKTREE_ROOT/build\" -DCMAKE_PREFIX_PATH=\"$QT_DIR\" && cmake --build \"$TASKTREE_ROOT/build\"" >&2
  exit 1
fi

export LD_LIBRARY_PATH="${QT_DIR}/lib:${TASKTREE_ROOT}/build:${LD_LIBRARY_PATH:-}"
exec "$BIN" --port "$PORT" --host 127.0.0.1 "$@"
