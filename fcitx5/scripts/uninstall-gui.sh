#!/usr/bin/env bash
# Non-interactive Fcitx 5 uninstaller used by the graphical settings center.
set -euo pipefail
PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec bash "$PROJECT_DIR/installers/uninstall-integration.sh" \
    --framework fcitx5 --non-interactive --yes "$@"
