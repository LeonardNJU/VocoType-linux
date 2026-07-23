#!/usr/bin/env bash
set -euo pipefail
settings=${1:?usage: $0 VOCOTYPE_SETTINGS_BINARY}
test -x "$settings"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/.config/fcitx5/conf"
cat > "$tmp/.config/kglobalshortcutsrc" <<'KDE'
[kwin]
TestAction=Alt+F8,Alt+F8,Test action
KDE
cat > "$tmp/.config/fcitx5/config" <<'FCITX'
[Hotkey]
EnumerateForwardKeys=Control+F8
FCITX

probe() {
  env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
    HOME="$tmp" XDG_CONFIG_HOME="$tmp/.config" "$settings" --check-hotkey "$1"
}

set +e
plain=$(probe a); plain_rc=$?
shift_printable=$(probe Shift+7); shift_rc=$?
navigation=$(probe Left); navigation_rc=$?
reserved=$(probe Ctrl+C); reserved_rc=$?
kde=$(probe Alt+F8); kde_rc=$?
fcitx=$(probe Ctrl+F8); fcitx_rc=$?
set -e
available=$(probe Ctrl+Shift+F12)
right_alt=$(probe Alt_R)

for item in "$plain" "$shift_printable" "$navigation" "$reserved"; do
  jq -e '.success == false and .kind == "unsafe"' <<<"$item" >/dev/null
done
for rc in "$plain_rc" "$shift_rc" "$navigation_rc" "$reserved_rc"; do
  test "$rc" -eq 20
done
jq -e '.success == false and .kind == "occupied" and (.reason | contains("KDE"))' \
  <<<"$kde" >/dev/null
test "$kde_rc" -eq 21
jq -e '.success == false and .kind == "occupied" and (.reason | contains("Fcitx"))' \
  <<<"$fcitx" >/dev/null
test "$fcitx_rc" -eq 21
jq -e '.success == true and .kind == "available"' <<<"$available" >/dev/null
jq -e '.success == true and .shortcut == "Alt_R"' <<<"$right_alt" >/dev/null

echo HOTKEY_SETTINGS_TEST_OK
