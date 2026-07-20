#!/usr/bin/env bash
set -euo pipefail

expected_version="${1:?usage: $0 EXPECTED_VERSION}"

check_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "missing packaged path: $path" >&2
    exit 1
  fi
  echo "PACKAGE_PATH_OK $path"
}

for path in \
  /usr/bin/vocotype-settings \
  /usr/bin/vocotype-fcitx5-backend \
  /usr/bin/vocotype-fcitx5-recorder \
  /usr/share/ibus/component/vocotype.xml \
  /usr/share/vocotype/.system-package \
  /usr/share/vocotype/installers/runtime-common.sh \
  /usr/share/vocotype/installers/uninstall-integration.sh \
  /usr/share/vocotype/ibus/scripts/install-gui.sh \
  /usr/share/vocotype/ibus/scripts/uninstall-gui.sh \
  /usr/share/vocotype/fcitx5/scripts/install-gui.sh \
  /usr/share/vocotype/fcitx5/scripts/uninstall-gui.sh; do
  check_path "$path"
done

ibus_exec=$(sed -n 's:.*<exec>\(.*\) --ibus</exec>.*:\1:p' /usr/share/ibus/component/vocotype.xml)
if [[ -z "$ibus_exec" ]]; then
  echo 'unable to parse IBus launcher from component XML' >&2
  exit 1
fi
check_path "$ibus_exec"

module=$(find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' -type f -print -quit 2>/dev/null)
if [[ -z "$module" ]]; then
  echo 'Fcitx module not found under /usr/lib64 or /usr/lib' >&2
  exit 1
fi
echo "PACKAGE_MODULE_OK $module"
if ldd "$module" | grep -q 'not found'; then
  echo "unresolved library dependency in $module" >&2
  ldd "$module" >&2
  exit 1
fi

grep -Fxq 'managed-by=native-package' /usr/share/vocotype/.system-package
grep -Fq 'PYTHONDONTWRITEBYTECODE=1' /usr/bin/vocotype-settings
grep -Fq 'PYTHONDONTWRITEBYTECODE=1' "$ibus_exec"
echo PACKAGE_METADATA_OK

find /usr/share/vocotype -type d -name __pycache__ -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/share/vocotype python3 - "$expected_version" "$ibus_exec" <<'PY'
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from settings_center.config_service import load_runtime_config
from settings_center.setup_manager import installation_paths
from vocotype_version import __version__

expected, expected_ibus_exec = sys.argv[1:]
assert __version__ == expected, (__version__, expected)
paths = installation_paths()
assert any(str(path) == expected_ibus_exec for path in paths.ibus_launchers), (
    paths.ibus_launchers,
    expected_ibus_exec,
)
assert isinstance(load_runtime_config(), dict)
print("PACKAGE_GUI_RUNTIME_IMPORT_OK", Gtk.get_major_version(), __version__)
PY

if find /usr/share/vocotype -type d -name __pycache__ -print -quit | grep -q .; then
  echo 'runtime import wrote __pycache__ into the immutable package tree' >&2
  exit 1
fi


lifecycle_home=$(mktemp -d)
trap 'rm -rf "$lifecycle_home"' EXIT
for framework in ibus fcitx5; do
  log="$lifecycle_home/$framework-uninstall.log"
  HOME="$lifecycle_home" XDG_CONFIG_HOME="$lifecycle_home/.config" \
    bash "/usr/share/vocotype/$framework/scripts/uninstall-gui.sh" \
    --purge-runtime >"$log" 2>&1
  grep -Fq 'NATIVE_PACKAGE_COMMAND:' "$log"
done
check_path /usr/share/ibus/component/vocotype.xml
check_path "$module"
echo PACKAGE_UNINSTALL_OWNERSHIP_OK

echo PACKAGE_INSTALL_SMOKE_OK
