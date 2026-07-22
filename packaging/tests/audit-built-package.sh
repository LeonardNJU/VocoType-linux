#!/usr/bin/env bash
set -euo pipefail
package=${1:?usage: $0 PACKAGE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_version=${2:?usage: $0 PACKAGE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_flavor=${3:?usage: $0 PACKAGE EXPECTED_VERSION EXPECTED_FLAVOR}
package=$(realpath "$package")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
case "$package" in
  *.deb) expected_manager=apt; dpkg-deb -x "$package" "$work/root" ;;
  *.rpm) expected_manager=dnf; mkdir -p "$work/root"; (cd "$work/root" && rpm2cpio "$package" | cpio -idm --quiet) ;;
  *.pkg.tar.*) expected_manager=pacman; mkdir -p "$work/root"; bsdtar -xf "$package" -C "$work/root" ;;
  *) echo "unsupported package archive" >&2; exit 2 ;;
esac
project="$work/root/usr/share/vocotype"
marker="$project/.system-package"
test -f "$marker"
grep -Fxq "version=$expected_version" "$marker"
grep -Fxq "flavor=$expected_flavor" "$marker"
grep -Fxq "manager=$expected_manager" "$marker"
grep -Fxq 'runtime=native' "$marker"
if find "$work/root" -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.whl' \) -print -quit | grep -q .; then
  echo "runtime package contains a Python artifact" >&2; exit 1
fi
native_dir=$(find "$work/root/usr/lib" "$work/root/usr/lib64" 2>/dev/null \
  -path '*/vocotype/.native-payload.sha256' -printf '%h\n' -quit)
test -n "$native_dir"
(cd "$native_dir" && sha256sum -c .native-payload.sha256)
for executable in vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  test -x "$work/root/usr/libexec/$executable"
done
for executable in \
  "$work/root/usr/bin/vocotype-settings" \
  "$work/root/usr/libexec/vocotype-audio-recorder" \
  "$work/root/usr/libexec/vocotype-model-manager"; do
  test -x "$executable"
  file "$executable" | grep -q 'ELF'
done
case "$expected_flavor" in
  universal|ibus)
    file "$work/root/usr/libexec/vocotype-ibus-engine" | grep -q 'ELF'
    test -f "$work/root/usr/share/ibus/component/vocotype.xml" ;;
  fcitx5) test ! -e "$work/root/usr/libexec/vocotype-ibus-engine" ;;
esac
case "$expected_flavor" in
  universal|fcitx5)
    test -f "$work/root/usr/share/fcitx5/addon/vocotype.conf"
    find "$work/root/usr/lib" "$work/root/usr/lib64" -path '*/fcitx5/vocotype.so' -print -quit | grep -q . ;;
  ibus) test ! -e "$work/root/usr/share/fcitx5/addon/vocotype.conf" ;;
esac
echo "BUILT_PACKAGE_AUDIT_OK $(basename "$package") flavor=$expected_flavor manager=$expected_manager"
