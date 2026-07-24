#!/usr/bin/env bash
set -euo pipefail
settings=${1:?usage: $0 VOCOTYPE_SETTINGS_BINARY}
test -x "$settings"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/.config/fcitx5/conf" "$tmp/.local/share/fcitx5/addon"
cat > "$tmp/.config/kglobalshortcutsrc" <<'KDE'
[kwin]
TestAction=Alt+F8,Alt+F8,Test action
KDE
cat > "$tmp/.config/fcitx5/config" <<'FCITX'
[Hotkey]
EnumerateForwardKeys=Control+F8
FCITX
cat > "$tmp/.config/fcitx5/conf/removed-vinput.conf" <<'STALE'
[Hotkey]
TriggerKey=Alt_R
STALE
cat > "$tmp/.config/fcitx5/conf/installed-addon.conf" <<'ACTIVE'
[Hotkey]
TriggerKey=Ctrl+Alt+F11
ACTIVE
cat > "$tmp/.local/share/fcitx5/addon/installed-addon.conf" <<'ADDON'
[Addon]
Name=Installed test addon
ADDON

probe() {
  env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
    HOME="$tmp" XDG_CONFIG_HOME="$tmp/.config" \
    XDG_DATA_HOME="$tmp/.local/share" XDG_DATA_DIRS="$tmp/system-share" \
    "$settings" --check-hotkey "$1"
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
set +e
installed_addon=$(probe Ctrl+Alt+F11); installed_addon_rc=$?
set -e

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
jq -e '.success == false and .kind == "occupied" and (.reason | contains("installed-addon.conf"))' \
  <<<"$installed_addon" >/dev/null
test "$installed_addon_rc" -eq 21

# Exercise the complete settings -> Controller1.SetConfig -> live readback ->
# persisted XDG config path. The fake controller starts at F9, so this catches
# the regression where only the settings JSON/UI changed.
live="$tmp/live"
mkdir -p "$live/bin" "$live/config/fcitx5/conf" "$live/home"
cp "$(dirname "$0")/fake-fcitx-controller.sh" "$live/bin/busctl"
cat > "$live/state.json" <<'STATE'
{"PTTKey":"F9","PolishKey":"Shift+F9","EditKey":"Control+F9"}
STATE
cat > "$live/config/fcitx5/conf/vocotype.conf" <<'CONF'
PTTKey=F9
PolishKey=Shift+F9
EditKey=Control+F9
CONF
apply=$(env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  HOME="$live/home" XDG_CONFIG_HOME="$live/config" \
  VOCOTYPE_FAKE_FCITX_STATE="$live/state.json" \
  VOCOTYPE_FAKE_FCITX_LOG="$live/busctl.log" \
  PATH="$live/bin:$PATH" \
  "$settings" --apply-fcitx-hotkeys Alt_R Shift+F8 Ctrl+F8)
jq -e '.success == true and .values.PTTKey == "Alt_R" and
       .values.PolishKey == "Shift+F8" and .values.EditKey == "Control+F8" and
       (.path | endswith("/live/config/fcitx5/conf/vocotype.conf"))' \
  <<<"$apply" >/dev/null
jq -e '.PTTKey == "Alt_R" and .PolishKey == "Shift+F8" and
       .EditKey == "Control+F8"' "$live/state.json" >/dev/null
grep -Fxq 'PTTKey=Alt_R' "$live/config/fcitx5/conf/vocotype.conf"
grep -Fxq 'PolishKey=Shift+F8' "$live/config/fcitx5/conf/vocotype.conf"
grep -Fxq 'EditKey=Control+F8' "$live/config/fcitx5/conf/vocotype.conf"
grep -q ' SetConfig ' "$live/busctl.log"
test "$(grep -c ' GetConfig ' "$live/busctl.log")" -ge 2
! grep -q ' Restart' "$live/busctl.log"

# Legacy JSON must never override an existing Fcitx persistent config. The
# layout migration extracts shared settings into config.json and IBus hotkeys
# into ibus.json, archives the old file, then reconciles live Fcitx from the
# authoritative vocotype.conf value (F9 here).
cat > "$live/state.json" <<'STATE'
{"PTTKey":"F9","PolishKey":"Shift+F9","EditKey":"Control+F9"}
STATE
cat > "$live/config/fcitx5/conf/vocotype.conf" <<'CONF'
PTTKey=F9
PolishKey=Shift+F9
EditKey=Control+F9
CONF
rm -rf "$live/config/vocotype"
mkdir -p "$live/config/vocotype"
cat > "$live/config/vocotype/fcitx5-backend.json" <<'JSON'
{"audio":{"sample_rate":48000},"hotkeys":{"transcribe":"Alt_R","polish":"Shift+F8","edit":"Ctrl+F8"}}
JSON
: > "$live/busctl.log"
reconciled=$(env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  HOME="$live/home" XDG_CONFIG_HOME="$live/config" \
  VOCOTYPE_FAKE_FCITX_STATE="$live/state.json" \
  VOCOTYPE_FAKE_FCITX_LOG="$live/busctl.log" \
  PATH="$live/bin:$PATH" \
  "$settings" --reconcile-fcitx-hotkeys-from-config)
jq -e '.success == true and .changed == false and .layout_migrated == true and
       .values.PTTKey == "F9"' <<<"$reconciled" >/dev/null
jq -e '.PTTKey == "F9" and .PolishKey == "Shift+F9" and
       .EditKey == "Control+F9"' "$live/state.json" >/dev/null
grep -Fxq 'PTTKey=F9' "$live/config/fcitx5/conf/vocotype.conf"
jq -e '.audio.sample_rate == 48000 and has("hotkeys") == false' \
  "$live/config/vocotype/config.json" >/dev/null
jq -e '.hotkeys.transcribe == "Alt_R" and
       (.hotkeys | keys) == ["edit", "polish", "transcribe"] and
       keys == ["hotkeys"]' \
  "$live/config/vocotype/ibus.json" >/dev/null
test -f "$live/config/vocotype/fcitx5-backend.json.migrated"
test ! -e "$live/config/vocotype/fcitx5-backend.json"
! grep -q ' SetConfig ' "$live/busctl.log"
! grep -q ' Restart' "$live/busctl.log"
second=$(env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  HOME="$live/home" XDG_CONFIG_HOME="$live/config" \
  VOCOTYPE_FAKE_FCITX_STATE="$live/state.json" \
  VOCOTYPE_FAKE_FCITX_LOG="$live/busctl.log" \
  PATH="$live/bin:$PATH" \
  "$settings" --reconcile-fcitx-hotkeys-from-config)
jq -e '.success == true and .changed == false and .layout_migrated == false' \
  <<<"$second" >/dev/null

# When no Fcitx persistent config exists yet, legacy hotkeys may seed it once
# through the normalized IBus runtime config. This is the only case where old
# JSON can influence Fcitx.
seed="$tmp/seed"
mkdir -p "$seed/bin" "$seed/config/vocotype" "$seed/config/fcitx5/conf" "$seed/home"
cp "$(dirname "$0")/fake-fcitx-controller.sh" "$seed/bin/busctl"
cat > "$seed/state.json" <<'STATE'
{"PTTKey":"F9","PolishKey":"Shift+F9","EditKey":"Control+F9"}
STATE
cat > "$seed/config/vocotype/fcitx5-backend.json" <<'JSON'
{"audio":{"sample_rate":44100},"hotkeys":{"transcribe":"Alt_R","polish":"Shift+F8","edit":"Ctrl+F8"}}
JSON
: > "$seed/busctl.log"
seeded=$(env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  HOME="$seed/home" XDG_CONFIG_HOME="$seed/config" \
  VOCOTYPE_FAKE_FCITX_STATE="$seed/state.json" \
  VOCOTYPE_FAKE_FCITX_LOG="$seed/busctl.log" \
  PATH="$seed/bin:$PATH" \
  "$settings" --reconcile-fcitx-hotkeys-from-config)
jq -e '.success == true and .changed == true and .layout_migrated == true and
       .values.PTTKey == "Alt_R"' <<<"$seeded" >/dev/null
jq -e '.PTTKey == "Alt_R" and .PolishKey == "Shift+F8" and
       .EditKey == "Control+F8"' "$seed/state.json" >/dev/null
grep -Fxq 'PTTKey=Alt_R' "$seed/config/fcitx5/conf/vocotype.conf"
test "$(grep -c ' SetConfig ' "$seed/busctl.log")" -eq 1
! grep -q ' Restart' "$seed/busctl.log"

echo HOTKEY_SETTINGS_TEST_OK
