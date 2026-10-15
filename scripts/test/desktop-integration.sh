#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

export TEST_DESKTOP_DIR
TEST_DESKTOP_DIR=$(mktemp -d)
trap 'command rm -rf "$TEST_DESKTOP_DIR"' EXIT
export XDG_DATA_HOME="$TEST_DESKTOP_DIR/data" XDG_CONFIG_HOME="$TEST_DESKTOP_DIR/config"
export XDG_CACHE_HOME="$TEST_DESKTOP_DIR/cache"
# Keep the test desktop isolated from the real session.
export XDG_RUNTIME_DIR="$TEST_DESKTOP_DIR/runtime"
mkdir -m700 "$XDG_RUNTIME_DIR"
export GTK_USE_PORTAL=0 NO_AT_BRIDGE=1 GSETTINGS_BACKEND=memory
. "$ROOT/scripts/install/common/desktop-integration.sh"
# Exercise Exec quoting instead of assuming the home directory has no spaces,
# percent field codes, backslashes, quotes, or shell metacharacters.
# shellcheck disable=SC1003
launcher="$TEST_DESKTOP_DIR/"'settings space % $ ` " \'
# The child launcher expands the exported test path when invoked.
# shellcheck disable=SC2016
printf '#!/usr/bin/env bash\ntouch "$TEST_DESKTOP_DIR/launched"\n' > "$launcher"
chmod +x "$launcher"
install_desktop_entries "$ROOT" "$launcher"
settings="$XDG_DATA_HOME/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
desktop-file-validate "$settings"
grep -Fxq "Icon=$XDG_DATA_HOME/icons/hicolor/192x192/apps/vocotype.png" "$settings"
cmp "$ROOT/web/icon-192.png" "$XDG_DATA_HOME/icons/hicolor/192x192/apps/vocotype.png"
timeout 10 dbus-run-session -- gio launch "$settings"
for _ in {1..50}; do
  [[ -f "$TEST_DESKTOP_DIR/launched" ]] && break
  sleep 0.1
done
[[ -f "$TEST_DESKTOP_DIR/launched" ]]
remove_desktop_entries
[[ ! -e "$settings" ]]
[[ ! -e "$XDG_DATA_HOME/icons/hicolor/192x192/apps/vocotype.png" ]]
printf 'Application menu installation, launch, and uninstall: PASS\n'
