#!/usr/bin/env bash
set -euo pipefail

component=/usr/share/ibus/component/vocotype.xml
[[ -f "$component" ]] || {
  echo "IBus component not found: $component" >&2
  exit 1
}
command -v dbus-run-session >/dev/null 2>&1 || {
  echo "dbus-run-session is required for the IBus registry smoke" >&2
  exit 1
}
command -v ibus-daemon >/dev/null 2>&1 || {
  echo "ibus-daemon is required for the IBus registry smoke" >&2
  exit 1
}
command -v ibus >/dev/null 2>&1 || {
  echo "ibus CLI is required for the IBus registry smoke" >&2
  exit 1
}

tmp=$(mktemp -d)
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

mkdir -p \
  "$tmp/home" \
  "$tmp/config" \
  "$tmp/cache" \
  "$tmp/runtime"
chmod 700 "$tmp/runtime"
env -u IBUS_ADDRESS -u DISPLAY -u WAYLAND_DISPLAY \
  HOME="$tmp/home" \
  XDG_CONFIG_HOME="$tmp/config" \
  XDG_CACHE_HOME="$tmp/cache" \
  XDG_RUNTIME_DIR="$tmp/runtime" \
  GIO_USE_VFS=local \
  timeout 20s dbus-run-session -- bash -s <<'INNER'
set -euo pipefail
unset IBUS_ADDRESS

ibus-daemon \
  --single \
  --cache refresh \
  --panel disable \
  --config disable \
  --emoji-extension disable \
  >"$XDG_CACHE_HOME/ibus-daemon.log" 2>&1 &
daemon_pid=$!
cleanup_daemon() {
  kill "$daemon_pid" >/dev/null 2>&1 || true
  wait "$daemon_pid" >/dev/null 2>&1 || true
}
trap cleanup_daemon EXIT

for _ in $(seq 1 50); do
  engines=$(ibus list-engine 2>/dev/null || true)
  if grep -Eq '^[[:space:]]*vocotype[[:space:]]+-[[:space:]]+VoCoType Voice Input' <<<"$engines"; then
    echo IBUS_REGISTRY_SMOKE_OK
    exit 0
  fi
  if ! kill -0 "$daemon_pid" >/dev/null 2>&1; then
    cat "$XDG_CACHE_HOME/ibus-daemon.log" >&2
    echo "IBus daemon exited before registering VoCoType" >&2
    exit 1
  fi
  sleep 0.2
done

cat "$XDG_CACHE_HOME/ibus-daemon.log" >&2
echo "VoCoType was not discovered by the isolated IBus registry" >&2
exit 1
INNER
