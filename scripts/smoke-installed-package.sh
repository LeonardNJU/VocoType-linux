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
  /usr/share/vocotype/.system-package; do
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

echo PACKAGE_INSTALL_SMOKE_OK
