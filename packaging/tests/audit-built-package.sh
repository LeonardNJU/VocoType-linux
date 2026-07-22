#!/usr/bin/env bash
set -euo pipefail

package=${1:?usage: $0 PACKAGE EXPECTED_WHEELHOUSE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_wheelhouse=${2:?usage: $0 PACKAGE EXPECTED_WHEELHOUSE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_version=${3:?usage: $0 PACKAGE EXPECTED_WHEELHOUSE EXPECTED_VERSION EXPECTED_FLAVOR}
expected_flavor=${4:?usage: $0 PACKAGE EXPECTED_WHEELHOUSE EXPECTED_VERSION EXPECTED_FLAVOR}
package=$(realpath "$package")
expected_wheelhouse=$(realpath "$expected_wheelhouse")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

case "$package" in
  *.deb)
    expected_manager=apt
    command -v dpkg-deb >/dev/null
    dpkg-deb -x "$package" "$work/root"
    ;;
  *.rpm)
    expected_manager=dnf
    command -v rpm2cpio >/dev/null
    command -v cpio >/dev/null
    mkdir -p "$work/root"
    (cd "$work/root" && rpm2cpio "$package" | cpio -idm --quiet)
    ;;
  *.pkg.tar.*)
    expected_manager=pacman
    command -v bsdtar >/dev/null
    mkdir -p "$work/root"
    bsdtar -xf "$package" -C "$work/root"
    ;;
  *)
    echo "unsupported package archive: $package" >&2
    exit 2
    ;;
esac

project="$work/root/usr/share/vocotype"
marker="$project/.system-package"
test -f "$marker"
grep -Fxq "version=$expected_version" "$marker"
grep -Fxq "flavor=$expected_flavor" "$marker"
grep -Fxq "manager=$expected_manager" "$marker"
python3 "$root/packaging/tools/audit-wheelhouse.py" \
  "$project/wheelhouse" --flavor "$expected_flavor"
(cd "$project" && sha256sum -c .wheelhouse.sha256)

expected_hashes="$work/expected-wheelhouse.sha256"
actual_hashes="$work/actual-wheelhouse.sha256"
(
  cd "$expected_wheelhouse"
  find . -maxdepth 1 -type f -name '*.whl' -print0 \
    | sort -z | xargs -0 sha256sum
) > "$expected_hashes"
(
  cd "$project/wheelhouse"
  find . -maxdepth 1 -type f -name '*.whl' -print0 \
    | sort -z | xargs -0 sha256sum
) > "$actual_hashes"
diff -u "$expected_hashes" "$actual_hashes"

native_roots=()
for candidate in "$work/root/usr/lib" "$work/root/usr/lib64"; do
  [[ -d "$candidate" ]] && native_roots+=("$candidate")
done
[[ "${#native_roots[@]}" -gt 0 ]]
native_dir=$(find "${native_roots[@]}" \
  -path '*/vocotype/.native-payload.sha256' -printf '%h\n' -quit)
test -n "$native_dir"
(cd "$native_dir" && sha256sum -c .native-payload.sha256)

for executable in \
  vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  libexec_launcher="$work/root/usr/libexec/$executable"
  test -f "$libexec_launcher"
  test ! -L "$libexec_launcher"
  grep -Fq 'exec /usr/' "$libexec_launcher"
done

echo "BUILT_PACKAGE_AUDIT_OK $(basename "$package") flavor=$expected_flavor manager=$expected_manager"
