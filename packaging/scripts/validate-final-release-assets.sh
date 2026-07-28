#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$ROOT/packaging/scripts/package-common.sh"
ASSET_ROOT=${1:?usage: $0 ROOT --version VERSION}
shift
VERSION=""
while [[ $# -gt 0 ]]; do case "$1" in --version) VERSION=${2:?}; shift 2;; *) echo "Unknown argument $1" >&2; exit 2;; esac; done
[[ -n "$VERSION" ]] || { echo "--version required" >&2; exit 2; }
DEB=$(vocotype_version_field "$VERSION" debian); DEB=${DEB//\~/.}
ARCH=$(vocotype_version_field "$VERSION" arch)
RPM_VERSION=$(vocotype_version_field "$VERSION" rpm_version)
RPM_RELEASE=$(vocotype_version_field "$VERSION" rpm_release)
mapfile -t files < <(find "$ASSET_ROOT" -maxdepth 1 -type f -printf '%f\n' | sort)
[[ ${#files[@]} -eq 11 ]] || { echo "Expected 10 installers and SHA256SUMS, got ${#files[@]}" >&2; printf '%s\n' "${files[@]}" >&2; exit 1; }
[[ -f "$ASSET_ROOT/SHA256SUMS" ]] || { echo "SHA256SUMS missing" >&2; exit 1; }
packages=(vocotype-linux vocotype-linux-ibus vocotype-linux-fcitx5)
[[ -f "$ASSET_ROOT/VoCoType-linux-${VERSION}-macOS-arm64.dmg" ]] || { echo "Missing macOS arm64 DMG" >&2; exit 1; }
for package in "${packages[@]}"; do
  [[ -f "$ASSET_ROOT/${package}_${DEB}-1_amd64.deb" ]] || { echo "Missing DEB for $package" >&2; exit 1; }
  [[ -f "$ASSET_ROOT/${package}-${ARCH}-1-x86_64.pkg.tar.zst" ]] || { echo "Missing Arch package for $package" >&2; exit 1; }
  mapfile -t rpms < <(find "$ASSET_ROOT" -maxdepth 1 -type f -name "${package}-${RPM_VERSION}-${RPM_RELEASE}*.x86_64.rpm")
  [[ ${#rpms[@]} -eq 1 ]] || { echo "Expected one RPM for $package, got ${#rpms[@]}" >&2; exit 1; }
done
if find "$ASSET_ROOT" -maxdepth 1 -type f -name '*.src.rpm' -print -quit | grep -q .; then echo "Source RPM forbidden" >&2; exit 1; fi
(cd "$ASSET_ROOT" && sha256sum -c SHA256SUMS)
checksum_count=$(wc -l < "$ASSET_ROOT/SHA256SUMS")
[[ $checksum_count -eq 10 ]] || { echo "Checksum set must contain 10 installers" >&2; exit 1; }
echo "FINAL_RELEASE_INSTALLERS_OK files=11"
