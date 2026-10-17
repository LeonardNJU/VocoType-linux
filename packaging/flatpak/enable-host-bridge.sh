#!/usr/bin/env bash
set -euo pipefail
APP=org.fcitx.Fcitx5
if [[ "${1:-}" != --accept-host-spawn ]]; then
  echo "Usage: $0 --accept-host-spawn" >&2
  echo "WARNING: Grants Fcitx5 Flatpak permission to run arbitrary host commands." >&2
  echo "Trust the Fcitx5 Flatpak and every addon before enabling this." >&2
  exit 2
fi
command -v flatpak >/dev/null || { echo "flatpak missing" >&2; exit 78; }
flatpak info "$APP" >/dev/null || {
  echo "Install org.fcitx.Fcitx5 first." >&2; exit 78;
}
RUNTIME=${XDG_RUNTIME_DIR:-"/run/user/$(id -u)"}
[[ "$RUNTIME" == /* ]] || { echo "Invalid XDG_RUNTIME_DIR" >&2; exit 2; }
mkdir -p "$RUNTIME/vocotype-flatpak"
chmod 0700 "$RUNTIME/vocotype-flatpak"
flatpak override --user   --filesystem=xdg-run/vocotype-flatpak   --talk-name=org.freedesktop.Flatpak   --env=VOCOTYPE_FCITX5_SOCKET="$RUNTIME/vocotype-flatpak/core.sock"   "$APP"
echo "Permissions applied. Fully restart Flatpak Fcitx5."
