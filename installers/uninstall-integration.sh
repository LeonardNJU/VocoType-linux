#!/usr/bin/env bash
# Shared user-level uninstaller for the IBus and Fcitx 5 integrations.
set -euo pipefail

FRAMEWORK=""
NON_INTERACTIVE=false
PURGE_RUNTIME=false
REMOVE_USER_DATA=false
REMOVE_SYSTEM_COMPONENT=false
ASSUME_YES=false

usage() {
    cat <<'USAGE'
Usage: uninstall-integration.sh --framework ibus|fcitx5 [options]

Options:
  --non-interactive          Do not read from the terminal.
  --yes                      Confirm the requested removal.
  --purge-runtime            Remove the integration runtime, virtualenv, and caches.
  --remove-user-data         Also remove shared configuration under ~/.config/vocotype.
  --remove-system-component  Remove a legacy unmanaged system IBus component via Polkit.
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
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$FRAMEWORK" in
    ibus|fcitx5) ;;
    *) echo "--framework 必须是 ibus 或 fcitx5" >&2; exit 2 ;;
esac
if [[ "$FRAMEWORK" != ibus && "$REMOVE_SYSTEM_COMPONENT" == true ]]; then
    echo "--remove-system-component 仅适用于 IBus" >&2
    exit 2
fi
if [[ "$NON_INTERACTIVE" == true && "$ASSUME_YES" != true ]]; then
    echo "非交互卸载必须显式传入 --yes" >&2
    exit 2
fi

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
VOCOTYPE_CONFIG_DIR="$XDG_CONFIG_HOME/vocotype"
FCITX_RUNTIME="$HOME/.local/share/vocotype-fcitx5"
IBUS_RUNTIME="$HOME/.local/share/vocotype"
SYSTEM_COMPONENT="/usr/share/ibus/component/vocotype.xml"
NATIVE_MARKERS=("$PROJECT_DIR/.system-package" "/usr/share/vocotype/.system-package")

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
        [[ -f "$HOME/.local/lib64/fcitx5/vocotype.so" ]]
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

remove_ibus() {
    remove_runtime_code "$IBUS_RUNTIME"
    rm -f "$HOME/.local/share/ibus/component/vocotype.xml"
    rm -f "$HOME/.local/libexec/ibus-engine-vocotype"

    if [[ -f "$SYSTEM_COMPONENT" ]]; then
        if native_package_present; then
            echo "系统 IBus component 由 vocotype-linux 软件包管理，本操作不会直接删除。"
        elif [[ "$REMOVE_SYSTEM_COMPONENT" == true ]]; then
            if ! command -v pkexec >/dev/null 2>&1; then
                echo "未检测到 pkexec，无法移除旧的系统 IBus component。" >&2
                return 1
            fi
            echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以移除旧的系统 IBus component。"
            pkexec "$(command -v rm)" -f "$SYSTEM_COMPONENT"
        else
            echo "检测到旧的系统 IBus component，未请求删除：$SYSTEM_COMPONENT"
        fi
    fi
    if command -v ibus >/dev/null 2>&1; then
        ibus restart >/dev/null 2>&1 || true
    fi
    echo "IBus 用户级集成已卸载。"
}

remove_fcitx() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now vocotype-fcitx5-backend.service >/dev/null 2>&1 || true
    fi

    remove_runtime_code "$FCITX_RUNTIME"
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

    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
    if command -v fcitx5 >/dev/null 2>&1; then
        if command -v timeout >/dev/null 2>&1; then
            timeout 8s fcitx5 -r >/dev/null 2>&1 || true
        else
            fcitx5 -r >/dev/null 2>&1 &
        fi
    fi
    echo "Fcitx 5 用户级集成已卸载。"
}

print_plan() {
    local name="IBus"
    [[ "$FRAMEWORK" == fcitx5 ]] && name="Fcitx 5"
    echo "=== VoCoType $name 卸载 ==="
    if [[ "$PURGE_RUNTIME" == true ]]; then
        echo "- 删除该 integration 的运行时、虚拟环境和缓存"
    else
        echo "- 删除程序代码与 integration 文件，保留虚拟环境和缓存"
    fi
    if [[ "$REMOVE_USER_DATA" == true ]]; then
        echo "- 删除共享用户配置、术语和音频设置：$VOCOTYPE_CONFIG_DIR"
    else
        echo "- 保留共享用户配置、术语和音频设置：$VOCOTYPE_CONFIG_DIR"
    fi
    if native_package_present; then
        echo "- 检测到原生软件包；/usr 下的文件继续由包管理器管理"
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
    echo "共享用户数据已删除。"
else
    echo "用户配置已保留：$VOCOTYPE_CONFIG_DIR"
fi

remove_shared_launchers_if_unused

if native_package_present; then
    echo "NATIVE_PACKAGE_COMMAND: $(native_package_command)"
fi

echo "卸载完成。"
