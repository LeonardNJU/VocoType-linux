#!/usr/bin/env bash
set -euo pipefail
socket=${VOCOTYPE_SOCKET_PATH:-/tmp/vocotype-fcitx5.sock}
pass=0; fail=0
check() { local title=$1; shift; if "$@" >/dev/null 2>&1; then echo "[PASS] $title"; ((pass+=1)); else echo "[FAIL] $title"; ((fail+=1)); fi; }
find_exec() { command -v "$1" >/dev/null 2>&1 || [[ -x "$HOME/.local/lib/vocotype-native/bin/$1" || -x "$HOME/.local/lib/vocotype-streaming/bin/$1" || -x "/usr/libexec/$1" || -x "/usr/lib/vocotype/$1" ]]; }
check "native core ELF" find_exec vocotype-core
check "native recorder ELF" find_exec vocotype-audio-recorder
check "native settings ELF" find_exec vocotype-settings
check "native model manager ELF" find_exec vocotype-model-manager
check "core socket" test -S "$socket"
check "runtime config" test -f "$HOME/.config/vocotype/fcitx5-backend.json"
check "terms file" test -f "$HOME/.config/vocotype/terms.yaml"
if pgrep -af 'python.*(vocotype|fcitx5_server|ibus\.main|settings_center)' >/dev/null; then
  echo "[FAIL] zero Python client processes"; ((fail+=1))
else
  echo "[PASS] zero Python client processes"; ((pass+=1))
fi
if command -v fcitx5 >/dev/null 2>&1; then
  check "Fcitx module" sh -c 'find "$HOME/.local/lib/fcitx5" /usr/lib /usr/lib64 -path "*/fcitx5/vocotype.so" -type f -print -quit 2>/dev/null | grep -q .'
fi
if command -v ibus >/dev/null 2>&1; then
  check "IBus component" sh -c 'test -f "$HOME/.local/share/ibus/component/vocotype.xml" || test -f /usr/share/ibus/component/vocotype.xml'
fi
printf 'Doctor: %d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
