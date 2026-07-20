#!/usr/bin/env bash
set -euo pipefail

command -v dbus-run-session >/dev/null 2>&1 || {
  echo 'dbus-run-session is required for Fcitx addon smoke' >&2
  exit 1
}
command -v fcitx5 >/dev/null 2>&1 || {
  echo 'fcitx5 is required for addon smoke' >&2
  exit 1
}

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/home" "$root/config" "$root/data" "$root/runtime"
chmod 700 "$root/runtime"
log="$root/fcitx.log"

set +e
dbus-run-session -- env -u FCITX_ADDON_DIRS \
  HOME="$root/home" \
  XDG_CONFIG_HOME="$root/config" \
  XDG_DATA_HOME="$root/data" \
  XDG_DATA_DIRS="/usr/share" \
  XDG_RUNTIME_DIR="$root/runtime" \
  timeout 7s fcitx5 >"$log" 2>&1
status=$?
set -e

# timeout(1) normally ends the foreground daemon after startup. A clean early
# exit is also acceptable; any other status still gets judged by the log below.
if [[ "$status" -ne 0 && "$status" -ne 124 ]]; then
  cat "$log" >&2
  echo "isolated Fcitx exited unexpectedly: $status" >&2
  exit 1
fi
if grep -qiE 'Failed to create addon: vocotype|Could not load addon vocotype' "$log"; then
  cat "$log" >&2
  echo 'VoCoType addon was discovered but failed to instantiate' >&2
  exit 1
fi
if ! grep -qi 'Loaded addon vocotype' "$log"; then
  cat "$log" >&2
  echo 'isolated Fcitx did not confirm Loaded addon vocotype' >&2
  exit 1
fi

echo FCITX_ADDON_LOAD_OK
