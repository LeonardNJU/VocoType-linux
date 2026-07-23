#!/usr/bin/env bash
set -euo pipefail
package=${1:?usage: $0 PACKAGE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_version=${2:?usage: $0 PACKAGE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_flavor=${3:?usage: $0 PACKAGE EXPECTED_VERSION EXPECTED_FLAVOR}
package=$(realpath "$package")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

runtime_executable() {
  local name=$1 candidate
  for candidate in \
    "$work/root/usr/libexec/$name" \
    "$work/root/usr/lib/vocotype/$name" \
    "$work/root/usr/lib64/vocotype/$name"; do
    [[ -x "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

case "$package" in
  *.deb)
    expected_manager=apt
    dpkg-deb -x "$package" "$work/root"
    ;;
  *.rpm)
    expected_manager=dnf
    mkdir -p "$work/root"
    (cd "$work/root" && rpm2cpio "$package" | cpio -idm --quiet)
    ;;
  *.pkg.tar.*)
    expected_manager=pacman
    mkdir -p "$work/root"
    bsdtar -xf "$package" -C "$work/root"
    ;;
  *) echo "unsupported package archive" >&2; exit 2 ;;
esac

marker="$work/root/usr/share/vocotype/.system-package"
test -f "$marker"
grep -Fxq "version=$expected_version" "$marker"
grep -Fxq "flavor=$expected_flavor" "$marker"
grep -Fxq "manager=$expected_manager" "$marker"
grep -Fxq 'runtime=native' "$marker"

if find "$work/root" -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.whl' \) \
    -print -quit | grep -q .; then
  echo "runtime package contains a Python artifact" >&2
  exit 1
fi

library_roots=()
for candidate in "$work/root/usr/lib" "$work/root/usr/lib64"; do
  [[ -d "$candidate" ]] && library_roots+=("$candidate")
done
[[ ${#library_roots[@]} -gt 0 ]] || {
  echo "package contains no library root" >&2
  exit 1
}
native_dir=$(find "${library_roots[@]}" \
  -path '*/vocotype/.native-payload.sha256' -printf '%h\n' -quit)
[[ -n "$native_dir" ]] || {
  echo "package contains no native payload checksum" >&2
  exit 1
}
(cd "$native_dir" && sha256sum -c .native-payload.sha256)
for executable in vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  test -x "$native_dir/$executable"
done

file "$work/root/usr/bin/vocotype-settings" | grep -q ELF
for executable in vocotype-audio-recorder vocotype-model-manager; do
  resolved=$(runtime_executable "$executable")
  file "$resolved" | grep -q ELF
done

case "$expected_flavor" in
  universal|ibus)
    ibus_engine=$(runtime_executable vocotype-ibus-engine)
    file "$ibus_engine" | grep -q ELF
    component="$work/root/usr/share/ibus/component/vocotype.xml"
    test -f "$component"
    grep -Fq "<version>$expected_version</version>" "$component"
    ;;
  fcitx5)
    ! runtime_executable vocotype-ibus-engine >/dev/null 2>&1
    ;;
esac

case "$expected_flavor" in
  universal|fcitx5)
    test -f "$work/root/usr/share/fcitx5/addon/vocotype.conf"
    find "${library_roots[@]}" \
      -path '*/fcitx5/vocotype.so' -print -quit | grep -q .
    ;;
  ibus)
    test ! -e "$work/root/usr/share/fcitx5/addon/vocotype.conf"
    ;;
esac

echo "BUILT_PACKAGE_AUDIT_OK $(basename "$package") flavor=$expected_flavor manager=$expected_manager"
