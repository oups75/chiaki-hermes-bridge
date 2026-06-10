#!/bin/bash
# Launcher for a deployed chiaki-taskui (prod tree). Resolves the deployment
# prefix from its own location; Qt comes from the development kit until the
# alpha bundles its own runtime.
SELF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
QT_DIR="${QT_DIR:-/run/media/soloway/workspace/Devel/Tools/Qt/6.11.1/gcc_64}"
export LD_LIBRARY_PATH="$SELF_DIR/lib:$QT_DIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QML_IMPORT_PATH="$SELF_DIR/qml${QML_IMPORT_PATH:+:$QML_IMPORT_PATH}"
exec "$SELF_DIR/bin/chiaki-taskui" "$@"
