#!/usr/bin/env bash
# Install fixed dependency sets for graphical setup. Release actions contain
# runtime libraries only; source-only Fcitx builds use an explicit source action.
set -euo pipefail

ACTION="${1:-}"
if [[ "$ACTION" == "--print-plan" ]]; then
    PRINT_PLAN=1
    ACTION="${2:-}"
else
    PRINT_PLAN=0
fi

case "$ACTION" in
    fcitx5|fcitx5-source|ibus|ibus-rime) ;;
    *)
        echo "Usage: $0 [--print-plan] {fcitx5|fcitx5-source|ibus|ibus-rime}" >&2
        exit 2
        ;;
esac

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
else
    echo "无法识别 Linux 发行版：缺少 /etc/os-release" >&2
    exit 3
fi

DISTRO_KEYS=" ${ID:-} ${ID_LIKE:-} "
MANAGER=""
PACKAGES=()

if [[ "$DISTRO_KEYS" == *" debian "* ]] || [[ "$DISTRO_KEYS" == *" ubuntu "* ]]; then
    MANAGER="apt-get"
    case "$ACTION" in
        fcitx5)
            PACKAGES=(fcitx5 fcitx5-config-qt libportaudio2)
            ;;
        fcitx5-source)
            PACKAGES=(
                fcitx5 fcitx5-config-qt build-essential cmake pkg-config
                libfcitx5-dev nlohmann-json3-dev libportaudio2
            )
            ;;
        ibus)
            PACKAGES=(
                ibus libportaudio2 python3-gi
                gir1.2-ibus-1.0 gir1.2-gtk-3.0
            )
            ;;
        ibus-rime)
            PACKAGES=(
                ibus libportaudio2 python3-gi
                gir1.2-ibus-1.0 gir1.2-gtk-3.0
                ibus-rime librime-data-luna-pinyin
            )
            ;;
    esac
elif [[ "$DISTRO_KEYS" == *" fedora "* ]] || [[ "$DISTRO_KEYS" == *" rhel "* ]] || [[ "$DISTRO_KEYS" == *" centos "* ]]; then
    MANAGER="dnf"
    case "$ACTION" in
        fcitx5)
            PACKAGES=(fcitx5 fcitx5-configtool portaudio)
            ;;
        fcitx5-source)
            PACKAGES=(
                fcitx5 fcitx5-configtool gcc-c++ make cmake pkgconf-pkg-config
                fcitx5-devel json-devel portaudio
            )
            ;;
        ibus)
            PACKAGES=(ibus python3-gobject gtk3 portaudio)
            ;;
        ibus-rime)
            PACKAGES=(ibus python3-gobject gtk3 portaudio ibus-rime rime-data)
            ;;
    esac
elif [[ "$DISTRO_KEYS" == *" arch "* ]] || [[ "$DISTRO_KEYS" == *" manjaro "* ]]; then
    MANAGER="pacman"
    case "$ACTION" in
        fcitx5)
            PACKAGES=(fcitx5 fcitx5-configtool portaudio)
            ;;
        fcitx5-source)
            PACKAGES=(
                base-devel fcitx5 fcitx5-configtool cmake pkgconf
                nlohmann-json portaudio
            )
            ;;
        ibus)
            PACKAGES=(ibus python-gobject gtk3 python portaudio)
            ;;
        ibus-rime)
            PACKAGES=(
                ibus python-gobject gtk3 python portaudio
                librime ibus-rime rime-data
            )
            ;;
    esac
else
    echo "暂不支持自动安装该发行版的系统依赖：ID=${ID:-unknown}, ID_LIKE=${ID_LIKE:-unknown}" >&2
    exit 4
fi

if [[ "$PRINT_PLAN" == "1" ]]; then
    printf '%s\n' "$MANAGER"
    printf '%s\n' "${PACKAGES[@]}"
    exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "该辅助程序必须由 pkexec 以管理员权限运行。" >&2
    exit 5
fi

case "$MANAGER" in
    apt-get)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends "${PACKAGES[@]}"
        ;;
    dnf)
        dnf install -y "${PACKAGES[@]}"
        ;;
    pacman)
        pacman -S --needed --noconfirm "${PACKAGES[@]}"
        ;;
esac
