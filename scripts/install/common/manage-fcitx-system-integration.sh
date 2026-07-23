#!/usr/bin/env bash
# Install or remove the unmanaged system-wide Fcitx 5 integration used by
# source-based VoCoType installations. Native packages remain package-owned.
set -euo pipefail

ACTION=${1:-}
PREFIX=${VOCOTYPE_SYSTEM_PREFIX:-/usr}

usage() {
    cat <<'EOF'
Usage:
  manage-fcitx-system-integration.sh install MODULE ADDON VERSION
  manage-fcitx-system-integration.sh uninstall
  manage-fcitx-system-integration.sh status
EOF
}

if [[ "$PREFIX" != /* ]]; then
    echo "VOCOTYPE_SYSTEM_PREFIX 必须是绝对路径。" >&2
    exit 2
fi
if [[ "$PREFIX" == /usr && $EUID -ne 0 ]]; then
    echo "系统级 Fcitx integration 必须通过 pkexec 或 sudo 安装。" >&2
    exit 5
fi

resolve_libdir() {
    if [[ -n ${VOCOTYPE_SYSTEM_LIBDIR:-} ]]; then
        printf '%s\n' "$VOCOTYPE_SYSTEM_LIBDIR"
        return
    fi
    if [[ "$PREFIX" == /usr ]] && command -v pkg-config >/dev/null 2>&1; then
        local detected
        detected=$(pkg-config --variable=libdir Fcitx5Core 2>/dev/null || true)
        if [[ "$detected" == /* ]]; then
            printf '%s\n' "$detected"
            return
        fi
    fi
    printf '%s/lib\n' "$PREFIX"
}

LIBDIR=$(resolve_libdir)
MODULE_DEST="$LIBDIR/fcitx5/vocotype.so"
ADDON_DEST="$PREFIX/share/fcitx5/addon/vocotype.conf"
MARKER="$PREFIX/share/vocotype/.source-fcitx-integration"

package_owner() {
    local path=$1 output owner
    # A custom prefix is a test/staging root and cannot be owned by the host's
    # package database. Querying it is both meaningless and distro-dependent.
    [[ "$PREFIX" == /usr ]] || return 0

    if command -v pacman >/dev/null 2>&1; then
        if output=$(pacman -Qo -- "$path" 2>/dev/null); then
            owner=$(sed -n 's/.* is owned by \([^ ]*\) .*/\1/p' <<<"$output" | head -1)
        fi
    elif command -v rpm >/dev/null 2>&1; then
        if output=$(rpm -qf --qf '%{NAME}\n' -- "$path" 2>/dev/null); then
            owner=${output%%$'\n'*}
        fi
    elif command -v dpkg-query >/dev/null 2>&1; then
        if output=$(dpkg-query -S -- "$path" 2>/dev/null); then
            output=${output%%$'\n'*}
            owner=${output%%: *}
        fi
    fi

    # Package names never contain whitespace or prose. This final validation
    # prevents diagnostics such as "file ... is not owned" becoming owners.
    case ${owner:-} in
        ''|*[!A-Za-z0-9+_.:@-]*) return 0 ;;
        *) printf '%s\n' "$owner" ;;
    esac
}

refuse_package_owned_file() {
    local path=$1 owner
    [[ -e "$path" ]] || return 0
    owner=$(package_owner "$path")
    if [[ -n "$owner" ]]; then
        echo "拒绝修改软件包 $owner 管理的文件：$path" >&2
        echo "请使用系统包管理器升级或卸载 $owner。" >&2
        exit 6
    fi
}

install_integration() {
    local module=${1:-} addon=${2:-} version=${3:-}
    [[ -f "$module" ]] || { echo "Fcitx module 不存在：$module" >&2; exit 3; }
    [[ -f "$addon" ]] || { echo "Fcitx addon 元数据不存在：$addon" >&2; exit 3; }
    grep -q '^Library=vocotype$' "$addon" || {
        echo "addon 元数据不是 VoCoType：$addon" >&2
        exit 3
    }
    refuse_package_owned_file "$MODULE_DEST"
    refuse_package_owned_file "$ADDON_DEST"

    install -Dm755 "$module" "$MODULE_DEST"
    install -Dm644 "$addon" "$ADDON_DEST"
    mkdir -p "$(dirname "$MARKER")"
    cat > "$MARKER" <<EOF
managed-by=source-installer
version=$version
module=$MODULE_DEST
addon=$ADDON_DEST
EOF
    chmod 0644 "$MARKER"
    echo "SYSTEM_FCITX_MODULE=$MODULE_DEST"
    echo "SYSTEM_FCITX_ADDON=$ADDON_DEST"
}

uninstall_integration() {
    if [[ ! -f "$MARKER" ]]; then
        echo "未检测到由源码安装器管理的系统级 Fcitx integration。"
        return 0
    fi
    refuse_package_owned_file "$MODULE_DEST"
    refuse_package_owned_file "$ADDON_DEST"
    rm -f "$MODULE_DEST" "$ADDON_DEST" "$MARKER"
    rmdir "$PREFIX/share/vocotype" 2>/dev/null || true
    echo "已移除源码安装器管理的系统级 VoCoType（Fcitx 5）integration。"
}

status_integration() {
    if [[ -f "$MODULE_DEST" && -f "$ADDON_DEST" ]]; then
        if [[ -f "$MARKER" ]]; then
            echo "source-managed"
        else
            echo "present-unmanaged-or-package-owned"
        fi
        printf 'module=%s\naddon=%s\n' "$MODULE_DEST" "$ADDON_DEST"
        return 0
    fi
    echo "absent"
    return 1
}

case "$ACTION" in
    install) shift; install_integration "$@" ;;
    uninstall) uninstall_integration ;;
    status) status_integration ;;
    -h|--help|"") usage; [[ -n "$ACTION" ]] || exit 2 ;;
    *) echo "未知操作：$ACTION" >&2; usage >&2; exit 2 ;;
esac
