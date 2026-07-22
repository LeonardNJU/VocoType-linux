#!/usr/bin/env bash
# Install native build/runtime dependencies for source installations.
set -euo pipefail
ACTION=${1:-}
if [[ "$ACTION" == --print-plan ]]; then PRINT_PLAN=1; ACTION=${2:-}; else PRINT_PLAN=0; fi
case "$ACTION" in fcitx5|fcitx5-source|ibus|ibus-rime|universal) ;; *) echo "Usage: $0 [--print-plan] {fcitx5|fcitx5-source|ibus|ibus-rime|universal}" >&2; exit 2;; esac
. /etc/os-release
keys=" ${ID:-} ${ID_LIKE:-} "
manager=""
common_deb=(build-essential cmake pkg-config libportaudio2-dev libgtk-3-dev libyaml-cpp-dev libcurl4-openssl-dev libssl-dev nlohmann-json3-dev)
common_rpm=(gcc-c++ make cmake pkgconf-pkg-config portaudio-devel gtk3-devel yaml-cpp-devel libcurl-devel openssl-devel nlohmann-json-devel)
common_arch=(base-devel cmake pkgconf portaudio gtk3 yaml-cpp curl openssl nlohmann-json)
packages=()
if [[ "$keys" == *" debian "* || "$keys" == *" ubuntu "* ]]; then
  manager=apt-get; packages=("${common_deb[@]}")
  case "$ACTION" in
    fcitx5|fcitx5-source) packages+=(fcitx5 fcitx5-config-qt libfcitx5core-dev) ;;
    ibus) packages+=(ibus libibus-1.0-dev) ;;
    ibus-rime) packages+=(ibus libibus-1.0-dev librime-dev librime-bin librime-data rime-data-luna-pinyin) ;;
    universal) packages+=(fcitx5 fcitx5-config-qt libfcitx5core-dev ibus libibus-1.0-dev librime-dev librime-bin librime-data rime-data-luna-pinyin) ;;
  esac
elif [[ "$keys" == *" fedora "* || "$keys" == *" rhel "* || "$keys" == *" centos "* ]]; then
  manager=dnf; packages=("${common_rpm[@]}")
  case "$ACTION" in
    fcitx5|fcitx5-source) packages+=(fcitx5 fcitx5-configtool fcitx5-devel) ;;
    ibus) packages+=(ibus ibus-devel) ;;
    ibus-rime) packages+=(ibus ibus-devel librime librime-devel librime-tools brise) ;;
    universal) packages+=(fcitx5 fcitx5-configtool fcitx5-devel ibus ibus-devel librime librime-devel librime-tools brise) ;;
  esac
elif [[ "$keys" == *" arch "* || "$keys" == *" manjaro "* ]]; then
  manager=pacman; packages=("${common_arch[@]}")
  case "$ACTION" in
    fcitx5|fcitx5-source) packages+=(fcitx5 fcitx5-configtool) ;;
    ibus) packages+=(ibus) ;;
    ibus-rime) packages+=(ibus librime librime-data) ;;
    universal) packages+=(fcitx5 fcitx5-configtool ibus librime librime-data) ;;
  esac
else
  echo "Unsupported distribution: ID=${ID:-unknown}" >&2; exit 4
fi
if [[ $PRINT_PLAN == 1 ]]; then printf '%s\n' "$manager" "${packages[@]}"; exit 0; fi
[[ $EUID -eq 0 ]] || { echo "Run through pkexec or sudo." >&2; exit 5; }
case "$manager" in
  apt-get) export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y --no-install-recommends "${packages[@]}" ;;
  dnf) dnf install -y "${packages[@]}" ;;
  pacman) pacman -S --needed --noconfirm "${packages[@]}" ;;
esac
