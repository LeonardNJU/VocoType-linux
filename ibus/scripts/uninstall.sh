#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec bash "$ROOT/installers/uninstall-native-user.sh" --framework ibus "$@"
