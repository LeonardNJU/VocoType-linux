#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PACKAGE_DIR=${1:-"$ROOT/dist/packages"}
. "$ROOT/packaging/tools/package-common.sh"
version=$(vocotype_version "$ROOT")
current_package=

cleanup() {
  [[ -z "$current_package" ]] || dnf remove -y "$current_package" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'echo "RPM flavor validation failed at line $LINENO" >&2' ERR

for flavor in universal ibus fcitx5; do
  package_name=$(vocotype_flavor_field "$flavor" package_name)
  echo "=== VALIDATE RPM flavor=$flavor package=$package_name ==="
  package=$($ROOT/packaging/tools/find-rpm-package.sh "$PACKAGE_DIR" "$package_name")
  echo "Selected RPM: $package"
  "$ROOT/packaging/tests/audit-built-package.sh" "$package" "$version" "$flavor"
  dnf install -y "$package"
  current_package=$package_name
  "$ROOT/packaging/tests/smoke-installed-package.sh" "$version" "$flavor"
  "$ROOT/packaging/tests/smoke-binary-runtime.sh"
  dnf remove -y "$package_name"
  current_package=
  "$ROOT/packaging/tests/smoke-removed-package.sh"
  echo "RPM_FLAVOR_VALIDATION_OK flavor=$flavor"
done

echo "RPM_ALL_FLAVORS_VALIDATION_OK version=$version"
