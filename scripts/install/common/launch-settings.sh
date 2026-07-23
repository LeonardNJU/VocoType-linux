#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
export VOCOTYPE_PROJECT_DIR="$ROOT"
for settings in \
  "$ROOT/build/native-desktop/vocotype-settings" \
  "$ROOT/build/native-desktop-ibus/vocotype-settings" \
  "$HOME/.local/lib/vocotype-native/bin/vocotype-settings" \
  /usr/bin/vocotype-settings; do
  [[ -x "$settings" ]] || continue
  exec "$settings" "$@"
done
echo "Native settings center is not built. Run the native installer first." >&2
exit 78
