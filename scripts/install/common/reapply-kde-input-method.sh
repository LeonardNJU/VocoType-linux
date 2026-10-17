#!/usr/bin/env bash
set -euo pipefail

if [[ "${XDG_SESSION_TYPE:-}" != wayland ||
      ( "${XDG_CURRENT_DESKTOP:-}" != *KDE* && "${XDG_CURRENT_DESKTOP:-}" != *Plasma* ) ]]; then
  echo "自动重新应用输入法仅支持 KDE Plasma Wayland。" >&2
  exit 2
fi
for tool in kreadconfig6 kwriteconfig6 busctl timeout flock; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "缺少 $tool；请在系统设置 → 虚拟键盘中手动重新应用 Fcitx 5。" >&2
    exit 2
  }
done

umask 077
exec 9>"${XDG_RUNTIME_DIR:?missing XDG_RUNTIME_DIR}/vocotype-kde-input-method.lock"
flock -n 9 || { echo "另一个输入法重新应用操作正在进行。" >&2; exit 1; }

read_selection() {
  timeout 5s kreadconfig6 --file kwinrc --group Wayland --key InputMethod
}
write_selection() {
  timeout 5s kwriteconfig6 --notify --file kwinrc --group Wayland --key InputMethod --type path "$1"
}
keyboard_property() {
  timeout 3s busctl --user get-property org.kde.KWin /VirtualKeyboard org.kde.kwin.VirtualKeyboard "$1"
}
fcitx_owner() {
  timeout 3s busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus GetNameOwner s org.fcitx.Fcitx5
}
wait_available() {
  local expected=$1 deadline=$((SECONDS + 10)) state
  while ((SECONDS < deadline)); do
    state=$(keyboard_property available) || return 1
    [[ "$state" == "b $expected" ]] && return 0
    sleep 0.1
  done
  echo "等待 KWin 输入法 available=$expected 超时。" >&2
  return 1
}

original=$(read_selection)
case "$original" in
  */fcitx5.desktop|*/fcitx5-wayland-launcher.desktop) ;;
  *) echo "当前 KDE 输入法不是 Fcitx 5，已保留原选项：$original" >&2; exit 2 ;;
esac
[[ -f "$original" ]] || { echo "输入法桌面文件不存在：$original" >&2; exit 1; }
[[ "$(keyboard_property enabled)" == 'b true' ]] || {
  echo "KDE 输入法未启用，已保留当前状态；请在系统设置中启用 Fcitx 5。" >&2
  exit 2
}
[[ "$(keyboard_property available)" == 'b true' ]] || {
  echo "KWin 输入法接口不可用；请通过系统设置重新应用 Fcitx 5。" >&2
  exit 2
}
previous_owner=$(fcitx_owner 2>/dev/null || true)

finish() {
  local status=$1 current owner deadline attempt restored=false
  trap - EXIT HUP INT TERM
  for attempt in 1 2; do
    if write_selection "$original" && wait_available true &&
       current=$(read_selection) && [[ "$current" == "$original" ]]; then
      restored=true
      break
    fi
  done
  if [[ "$restored" != true ]]; then
    echo "恢复 KDE 输入法失败；请立即在系统设置 → 虚拟键盘中重新选择 Fcitx 5 并应用。" >&2
    printf '原选项：%s\n' "$original" >&2
    exit 1
  fi
  if [[ "$status" == 0 ]]; then
    deadline=$((SECONDS + 10))
    while ((SECONDS < deadline)); do
      owner=$(fcitx_owner 2>/dev/null || true)
      if [[ -n "$owner" && "$owner" != "$previous_owner" ]]; then
        echo "KDE 已重新应用 Fcitx 5；原输入法选项已恢复，Fcitx 实例已更换。"
        exit 0
      fi
      sleep 0.1
    done
    echo "KDE 输入法选项已恢复，但等待新的 Fcitx 实例超时。" >&2
    exit 1
  fi
  exit "$status"
}
# shortcut: SIGKILL cannot restore the selection; recover in System Settings after a forced kill.
trap 'finish $?' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

write_selection '' || exit 1
wait_available false || exit 1
# EXIT restores the original selection on both success and failure.
