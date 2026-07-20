#!/usr/bin/env bash
# Interactive CLI uninstaller for the IBus integration.
set -euo pipefail
PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec bash "$PROJECT_DIR/installers/uninstall-integration.sh" --framework ibus "$@"
