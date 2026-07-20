#!/usr/bin/env bash
# Non-interactive Fcitx 5 installer used by the graphical settings center.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
for argument in "$@"; do
    if [[ "$argument" == --non-interactive ]]; then
        exec bash "$SCRIPT_DIR/install.sh" "$@"
    fi
done
exec bash "$SCRIPT_DIR/install.sh" --non-interactive "$@"
