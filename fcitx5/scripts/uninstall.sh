#!/usr/bin/env bash
# Interactive CLI uninstaller for the Fcitx 5 integration.
set -euo pipefail
PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec bash "$PROJECT_DIR/installers/uninstall-integration.sh" --framework fcitx5 "$@"
