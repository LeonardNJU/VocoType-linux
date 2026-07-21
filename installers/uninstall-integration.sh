#!/usr/bin/env bash
# Shared user-level uninstaller for the IBus and Fcitx 5 integrations.
set -euo pipefail

FRAMEWORK=""
NON_INTERACTIVE=false
PURGE_RUNTIME=false
REMOVE_USER_DATA=false
REMOVE_SYSTEM_COMPONENT=true
REMOVE_SYSTEM_INTEGRATION=true
ASSUME_YES=false

usage() {
    cat <<'USAGE'
Usage: uninstall-integration.sh --framework ibus|fcitx5 [options]

Options:
  --non-interactive          Do not read from the terminal.
  --yes                      Confirm the requested removal.
  --purge-runtime            Remove the integration runtime, virtualenv, and caches.
  --remove-user-data         Also remove shared configuration under ~/.config/vocotype.
  --remove-system-component  Remove an unmanaged system IBus component (default).
  --keep-system-component    Keep an unmanaged system IBus component.
  --remove-system-integration Remove source-managed system Fcitx integration (default).
  --keep-system-integration  Keep source-managed system Fcitx integration.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --framework) FRAMEWORK="${2:?missing framework}"; shift 2 ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        --purge-runtime) PURGE_RUNTIME=true; shift ;;
        --remove-user-data) REMOVE_USER_DATA=true; shift ;;
        --remove-system-component) REMOVE_SYSTEM_COMPONENT=true; shift ;;
        --keep-system-component) REMOVE_SYSTEM_COMPONENT=false; shift ;;
        --remove-system-integration) REMOVE_SYSTEM_INTEGRATION=true; shift ;;
        --keep-system-integration) REMOVE_SYSTEM_INTEGRATION=false; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$FRAMEWORK" in
    ibus) REMOVE_SYSTEM_INTEGRATION=false ;;
    fcitx5) REMOVE_SYSTEM_COMPONENT=false ;;
    *) echo "--framework 必须是 ibus 或 fcitx5" >&2; exit 2 ;;
esac
if [[ "$NON_INTERACTIVE" == true && "$ASSUME_YES" != true ]]; then
    echo "非交互卸载必须显式传入 --yes" >&2
    exit 2
fi

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
VOCOTYPE_CONFIG_DIR="$XDG_CONFIG_HOME/vocotype"
FCITX_RUNTIME="$HOME/.local/share/vocotype-fcitx5"
IBUS_RUNTIME="$HOME/.local/share/vocotype"
SYSTEM_PREFIX=${VOCOTYPE_SYSTEM_PREFIX:-/usr}
SYSTEM_COMPONENT="$SYSTEM_PREFIX/share/ibus/component/vocotype.xml"
SYSTEM_FCITX_MARKER="$SYSTEM_PREFIX/share/vocotype/.source-fcitx-integration"
SYSTEM_FCITX_HELPER="$PROJECT_DIR/installers/manage-fcitx-system-integration.sh"
NATIVE_MARKERS=("$PROJECT_DIR/.system-package" "$SYSTEM_PREFIX/share/vocotype/.system-package")


run_pkexec() {
    if [[ "$NON_INTERACTIVE" == true ]]; then
        pkexec --disable-internal-agent "$@"
    else
        pkexec "$@"
    fi
}

native_package_present() {
    local marker
    for marker in "${NATIVE_MARKERS[@]}"; do
        [[ -f "$marker" ]] && return 0
    done
    return 1
}

native_package_command() {
    if command -v pacman >/dev/null 2>&1; then
        printf '%s\n' 'sudo pacman -Rns vocotype-linux'
    elif command -v dnf >/dev/null 2>&1; then
        printf '%s\n' 'sudo dnf remove vocotype-linux'
    elif command -v apt-get >/dev/null 2>&1; then
        printf '%s\n' 'sudo apt remove vocotype-linux'
    else
        printf '%s\n' '请使用系统包管理器卸载 vocotype-linux'
    fi
}

fcitx_user_present() {
    [[ -f "$FCITX_RUNTIME/backend/fcitx5_server.py" ]] ||
        [[ -f "$HOME/.local/share/fcitx5/addon/vocotype.conf" ]] ||
        [[ -f "$HOME/.local/lib/fcitx5/vocotype.so" ]] ||
        [[ -f "$HOME/.local/lib64/fcitx5/vocotype.so" ]] ||
        [[ -f "$SYSTEM_FCITX_MARKER" ]]
}

ibus_user_present() {
    [[ -f "$IBUS_RUNTIME/ibus/main.py" ]] ||
        [[ -f "$HOME/.local/libexec/ibus-engine-vocotype" ]] ||
        [[ -f "$HOME/.local/share/ibus/component/vocotype.xml" ]]
}

remove_shared_launchers_if_unused() {
    if fcitx_user_present || ibus_user_present || native_package_present; then
        echo "保留共享设置中心入口：另一套 integration 或原生软件包仍在使用。"
        return
    fi
    rm -f "$HOME/.local/bin/vocotype-settings"
    rm -f "$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
    rm -f "$HOME/.local/share/icons/hicolor/192x192/apps/vocotype.png"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    fi
}

remove_runtime_code() {
    local runtime="$1"
    if [[ "$PURGE_RUNTIME" == true ]]; then
        rm -rf "$runtime"
        return
    fi
    rm -rf \
        "$runtime/app" \
        "$runtime/ibus" \
        "$runtime/settings_center" \
        "$runtime/backend" \
        "$runtime/scripts"
    rm -f "$runtime/vocotype_version.py"
    if [[ -d "$runtime" ]] && [[ -z "$(find "$runtime" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        rmdir "$runtime"
    fi
}

RESTART_TIMEOUT_SECONDS=${VOCOTYPE_RESTART_TIMEOUT_SECONDS:-8}

run_bounded_restart() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "${RESTART_TIMEOUT_SECONDS}s" "$@" >/dev/null 2>&1
    else
        "$@" >/dev/null 2>&1
    fi
}

desktop_session_available() {
    [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] &&
        [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]
}

remove_ibus() {
    echo "正在清理 VoCoType（IBus）用户级运行代码…"
    remove_runtime_code "$IBUS_RUNTIME"
    echo "正在移除 VoCoType（IBus）component 与 launcher…"
    rm -f "$HOME/.local/share/ibus/component/vocotype.xml"
    rm -f "$HOME/.local/libexec/ibus-engine-vocotype"

    if [[ -f "$SYSTEM_COMPONENT" ]]; then
        if native_package_present; then
            echo "系统 IBus component 由 vocotype-linux 软件包管理，本操作不会直接删除。"
        elif [[ "$REMOVE_SYSTEM_COMPONENT" == true ]]; then
            echo "正在移除源码/旧版安装器写入的系统 IBus component…"
            if [[ "$SYSTEM_PREFIX" != /usr ]]; then
                rm -f "$SYSTEM_COMPONENT"
            else
                if ! command -v pkexec >/dev/null 2>&1; then
                    echo "SYSTEM_COMPONENT_REMOVE_FAILED: 未检测到 pkexec，系统 IBus component 未移除。" >&2
                    return 1
                fi
                echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以移除系统 VoCoType（IBus）component。"
                if ! run_pkexec "$(command -v rm)" -f "$SYSTEM_COMPONENT"; then
                    echo "SYSTEM_COMPONENT_REMOVE_FAILED: 管理员授权被取消或系统 component 删除失败：$SYSTEM_COMPONENT" >&2
                    return 1
                fi
            fi
            if [[ -e "$SYSTEM_COMPONENT" ]]; then
                echo "SYSTEM_COMPONENT_REMOVE_FAILED: 系统 component 删除后仍然存在：$SYSTEM_COMPONENT" >&2
                return 1
            fi
            echo "✓ 系统 VoCoType（IBus）component 已移除"
        else
            echo "保留系统 IBus component：$SYSTEM_COMPONENT"
        fi
    fi
    if command -v ibus >/dev/null 2>&1 && desktop_session_available; then
        echo "正在刷新 IBus 注册信息…"
        if ! run_bounded_restart ibus restart; then
            echo "RESTART_FAILED: VoCoType 文件已清理，但 IBus 重启失败。" >&2
            return 1
        fi
    else
        echo "未检测到可用桌面会话，跳过 IBus 重启。"
    fi
    echo "VoCoType（IBus）integration 已卸载。"
}

remove_fcitx() {
    if command -v systemctl >/dev/null 2>&1; then
        echo "正在停止 VoCoType（Fcitx 5）后台服务…"
        systemctl --user disable --now vocotype-fcitx5-backend.service >/dev/null 2>&1 || true
    fi

    echo "正在清理 VoCoType（Fcitx 5）用户级运行代码…"
    remove_runtime_code "$FCITX_RUNTIME"
    echo "正在移除 VoCoType Fcitx 5 module、addon 与 launcher…"
    rm -f \
        "$HOME/.local/lib/fcitx5/vocotype.so" \
        "$HOME/.local/lib/fcitx5/libvocotype.so" \
        "$HOME/.local/lib64/fcitx5/vocotype.so" \
        "$HOME/.local/lib64/fcitx5/libvocotype.so" \
        "$HOME/.local/share/fcitx5/addon/vocotype.conf" \
        "$HOME/.local/share/fcitx5/inputmethod/vocotype.conf" \
        "$XDG_CONFIG_HOME/environment.d/fcitx5-vocotype.conf" \
        "$XDG_CONFIG_HOME/systemd/user/vocotype-fcitx5-backend.service" \
        "$HOME/.local/bin/vocotype-fcitx5-backend" \
        "$HOME/.local/bin/vocotype-fcitx5-recorder"

    if native_package_present; then
        echo "系统 Fcitx addon 由 vocotype-linux 软件包管理，本操作不会直接删除。"
    elif [[ -f "$SYSTEM_FCITX_MARKER" ]]; then
        if [[ "$REMOVE_SYSTEM_INTEGRATION" == true ]]; then
            echo "正在移除源码安装器管理的系统 VoCoType（Fcitx 5）addon…"
            if [[ "$SYSTEM_PREFIX" != /usr ]]; then
                if ! bash "$SYSTEM_FCITX_HELPER" uninstall; then
                    echo "SYSTEM_FCITX_REMOVE_FAILED: 测试前缀中的系统 addon 删除失败。" >&2
                    return 1
                fi
            else
                if ! command -v pkexec >/dev/null 2>&1; then
                    echo "SYSTEM_FCITX_REMOVE_FAILED: 未检测到 pkexec，系统 Fcitx addon 未移除。" >&2
                    return 1
                fi
                echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以移除系统 VoCoType（Fcitx 5）addon。"
                if ! run_pkexec "$(command -v bash)" "$SYSTEM_FCITX_HELPER" uninstall; then
                    echo "SYSTEM_FCITX_REMOVE_FAILED: 管理员授权被取消或系统 addon 删除失败。" >&2
                    return 1
                fi
            fi
            if [[ -e "$SYSTEM_FCITX_MARKER" ]]; then
                echo "SYSTEM_FCITX_REMOVE_FAILED: 删除后 ownership marker 仍然存在：$SYSTEM_FCITX_MARKER" >&2
                return 1
            fi
            echo "✓ 系统 VoCoType（Fcitx 5）addon 已移除"
        else
            echo "保留源码安装器管理的系统 Fcitx addon：$SYSTEM_FCITX_MARKER"
        fi
    fi

    if command -v systemctl >/dev/null 2>&1; then
        echo "正在刷新 VoCoType 用户服务定义…"
        systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
    if command -v fcitx5 >/dev/null 2>&1 && desktop_session_available; then
        echo "正在重启 Fcitx 5 以加载 VoCoType 变更…"
        # `fcitx5 -r` remains in the foreground. Always daemonize the
        # replacement so a timeout wrapper cannot kill the new instance.
        if ! run_bounded_restart env -u FCITX_ADDON_DIRS fcitx5 -r -d; then
            echo "RESTART_FAILED: VoCoType 文件已清理，但 Fcitx 5 重启失败。" >&2
            return 1
        fi
    else
        echo "未检测到可用桌面会话，跳过 Fcitx 5 重启。"
    fi
    echo "VoCoType（Fcitx 5）integration 已卸载。"
}

print_plan() {
    local name="IBus"
    [[ "$FRAMEWORK" == fcitx5 ]] && name="Fcitx 5"
    echo "=== 卸载 VoCoType（$name） ==="
    if [[ "$PURGE_RUNTIME" == true ]]; then
        echo "- 删除该 integration 的运行时、虚拟环境和缓存"
    else
        echo "- 删除程序代码与 integration 文件，保留虚拟环境和缓存"
    fi
    if [[ "$REMOVE_USER_DATA" == true ]]; then
        echo "- 删除 VoCoType 用户配置、术语和音频设置：$VOCOTYPE_CONFIG_DIR"
    else
        echo "- 保留 VoCoType 用户配置、术语和音频设置：$VOCOTYPE_CONFIG_DIR"
    fi
    if native_package_present; then
        echo "- 检测到原生软件包；/usr 下的文件继续由包管理器管理"
    elif [[ "$FRAMEWORK" == fcitx5 && -f "$SYSTEM_FCITX_MARKER" ]]; then
        if [[ "$REMOVE_SYSTEM_INTEGRATION" == true ]]; then
            echo "- 通过 Polkit 删除源码安装器管理的系统级 Fcitx addon"
        else
            echo "- 保留源码安装器管理的系统级 Fcitx addon"
        fi
    fi
}

print_plan
if [[ "$ASSUME_YES" != true ]]; then
    read -r -p "确认卸载？(y/N): " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "已取消"; exit 0; }
fi

case "$FRAMEWORK" in
    ibus) remove_ibus ;;
    fcitx5) remove_fcitx ;;
esac

if [[ "$REMOVE_USER_DATA" == true ]]; then
    rm -rf "$VOCOTYPE_CONFIG_DIR"
    if [[ "$FRAMEWORK" == fcitx5 ]]; then
        rm -f "$XDG_CONFIG_HOME/fcitx5/conf/vocotype.conf"
    fi
    echo "VoCoType 用户数据已删除。"
else
    echo "用户配置已保留：$VOCOTYPE_CONFIG_DIR"
fi

remove_shared_launchers_if_unused

if native_package_present; then
    echo "NATIVE_PACKAGE_COMMAND: $(native_package_command)"
fi

echo "卸载完成。"
