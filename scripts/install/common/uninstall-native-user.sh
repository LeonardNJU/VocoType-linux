#!/usr/bin/env bash
set -euo pipefail
FRAMEWORK=universal
PURGE_RUNTIME=false
REMOVE_USER_DATA=false
KEEP_SYSTEM_INTEGRATION=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --framework) FRAMEWORK="${2:?missing framework}"; shift 2 ;;
    --purge-runtime) PURGE_RUNTIME=true; shift ;;
    --remove-user-data) REMOVE_USER_DATA=true; shift ;;
    --keep-system-integration) KEEP_SYSTEM_INTEGRATION=true; shift ;;
    --remove-system-integration) KEEP_SYSTEM_INTEGRATION=false; shift ;;
    --keep-system-component|--remove-system-component|--yes|--non-interactive) shift ;;
    -h|--help) echo "Usage: $0 --framework fcitx5|ibus|universal [--purge-runtime] [--remove-user-data] [--keep-system-integration]"; exit 0 ;;
    *) shift ;;
  esac
done
case "$FRAMEWORK" in fcitx5|ibus|universal) ;; *) echo "invalid framework" >&2; exit 2;; esac
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/../../.." && pwd)
SYSTEM_PREFIX=${VOCOTYPE_SYSTEM_PREFIX:-/usr}
manager_cmd() { printf 'system%s' ctl; }
manager_user() { local manager; manager=$(manager_cmd); "$manager" --user "$@"; }

remove_source_fcitx() {
  local marker="$SYSTEM_PREFIX/share/vocotype/.source-fcitx-integration"
  [[ -f "$marker" ]] || return 0
  if [[ "$KEEP_SYSTEM_INTEGRATION" == true ]]; then
    echo "保留源码安装器管理的系统 Fcitx addon。"
    return 0
  fi
  local helper="$PROJECT_DIR/scripts/install/common/manage-fcitx-system-integration.sh"
  if [[ -w "$SYSTEM_PREFIX" || $EUID -eq 0 ]]; then
    VOCOTYPE_SYSTEM_PREFIX="$SYSTEM_PREFIX" bash "$helper" uninstall
  elif command -v pkexec >/dev/null 2>&1; then
    pkexec env VOCOTYPE_SYSTEM_PREFIX="$SYSTEM_PREFIX" bash "$helper" uninstall
  else
    sudo env VOCOTYPE_SYSTEM_PREFIX="$SYSTEM_PREFIX" bash "$helper" uninstall
  fi
  echo "系统 VoCoType（Fcitx 5）addon 已移除。"
}

fcitx_present() {
  [[ -f "$HOME/.config/systemd/user/vocotype-fcitx5-backend.service" ||
     -x "$HOME/.local/bin/vocotype-fcitx5-backend" ||
     -d "$HOME/.local/share/vocotype-fcitx5/backend" ]]
}
ibus_present() {
  [[ -f "$HOME/.local/share/ibus/component/vocotype.xml" ||
     -x "$HOME/.local/libexec/ibus-engine-vocotype" ||
     -d "$HOME/.local/share/vocotype/ibus" ]]
}

restart_ibus_bounded() {
  command -v ibus >/dev/null 2>&1 || return 0
  [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" || -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]] || return 0
  local seconds=${VOCOTYPE_RESTART_TIMEOUT_SECONDS:-8}
  if command -v timeout >/dev/null 2>&1; then
    if ! timeout --signal=TERM "${seconds}s" ibus restart >/dev/null 2>&1; then
      echo "RESTART_FAILED: VoCoType 文件已清理，但 IBus 重启失败" >&2
      return 1
    fi
  elif ! ibus restart >/dev/null 2>&1; then
    echo "RESTART_FAILED: VoCoType 文件已清理，但 IBus 重启失败" >&2
    return 1
  fi
}

case "$FRAMEWORK" in
  fcitx5|universal)
    if command -v "$(manager_cmd)" >/dev/null 2>&1; then
      manager_user disable --now vocotype-fcitx5-backend.service >/dev/null 2>&1 || true
    fi
    rm -f "$HOME/.config/systemd/user/vocotype-fcitx5-backend.service"
    rm -f "$HOME/.local/bin/vocotype-fcitx5-backend" "$HOME/.local/bin/vocotype-fcitx5-recorder"
    rm -f "$HOME/.local/lib/fcitx5/vocotype.so" "$HOME/.local/lib64/fcitx5/libvocotype.so"
    rm -f "$HOME/.local/share/fcitx5/addon/vocotype.conf"
    rm -f "$HOME/.config/environment.d/fcitx5-vocotype.conf"
    remove_source_fcitx
    [[ "$PURGE_RUNTIME" == true ]] && rm -rf "$HOME/.local/share/vocotype-fcitx5"
    command -v fcitx5-remote >/dev/null 2>&1 && fcitx5-remote -r >/dev/null 2>&1 || true
    ;;
  *) ;;
esac

ibus_restart_failed=false
case "$FRAMEWORK" in
  ibus|universal)
    rm -f "$HOME/.local/share/ibus/component/vocotype.xml" "$HOME/.local/libexec/ibus-engine-vocotype"
    legacy="$HOME/.local/share/vocotype"
    rm -rf "$legacy/app" "$legacy/ibus" "$legacy/settings_center"
    rm -f "$legacy/vocotype_version.py"
    if [[ "$PURGE_RUNTIME" == true ]]; then rm -rf "$legacy"; fi
    restart_ibus_bounded || ibus_restart_failed=true
    ;;
  *) ;;
esac

if [[ "$PURGE_RUNTIME" == true ]]; then
  rm -rf "$HOME/.local/lib/vocotype-native" "$HOME/.local/lib/vocotype-streaming"
fi
if ! fcitx_present && ! ibus_present; then
  rm -f "$HOME/.local/bin/vocotype-settings"
fi
if [[ "$REMOVE_USER_DATA" == true ]]; then
  rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/vocotype"
fi
if command -v "$(manager_cmd)" >/dev/null 2>&1; then
  manager_user daemon-reload >/dev/null 2>&1 || true
fi
case "$FRAMEWORK" in
  fcitx5) echo "VoCoType（Fcitx 5）integration 已卸载；用户配置已保留。" ;;
  ibus) echo "VoCoType（IBus）integration 已卸载；用户配置已保留。" ;;
  universal) echo "VoCoType integrations 已卸载；用户配置已保留。" ;;
esac
[[ "$ibus_restart_failed" == false ]]
