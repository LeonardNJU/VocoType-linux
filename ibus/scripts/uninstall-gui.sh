#!/usr/bin/env bash
# Non-interactive IBus uninstaller used by the graphical settings center.
set -euo pipefail
PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec bash "$PROJECT_DIR/installers/uninstall-integration.sh" \
    --framework ibus --non-interactive --yes "$@"
