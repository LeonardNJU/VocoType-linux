#!/usr/bin/env bash
set -euo pipefail

expected_version="${1:?usage: $0 EXPECTED_VERSION}"

for path in \
  /usr/bin/vocotype-settings \
  /usr/bin/vocotype-fcitx5-backend \
  /usr/bin/vocotype-fcitx5-recorder \
  /usr/libexec/vocotype-ibus-engine \
  /usr/share/ibus/component/vocotype.xml \
  /usr/share/vocotype/.system-package; do
  test -e "$path"
done

module=$(find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' -type f -print -quit 2>/dev/null)
test -n "$module"
if ldd "$module" | grep -q 'not found'; then
  echo "unresolved library dependency in $module" >&2
  ldd "$module" >&2
  exit 1
fi

grep -Fq '<exec>/usr/libexec/vocotype-ibus-engine --ibus</exec>' /usr/share/ibus/component/vocotype.xml
grep -Fxq 'managed-by=native-package' /usr/share/vocotype/.system-package
grep -Fq 'PYTHONDONTWRITEBYTECODE=1' /usr/bin/vocotype-settings
grep -Fq 'PYTHONDONTWRITEBYTECODE=1' /usr/libexec/vocotype-ibus-engine

find /usr/share/vocotype -type d -name __pycache__ -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/share/vocotype python3 - "$expected_version" <<'PY'
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from settings_center.config_service import load_runtime_config
from settings_center.setup_manager import installation_paths
from vocotype_version import __version__

expected = sys.argv[1]
assert __version__ == expected, (__version__, expected)
paths = installation_paths()
assert any(str(path) == "/usr/libexec/vocotype-ibus-engine" for path in paths.ibus_launchers)
assert isinstance(load_runtime_config(), dict)
print("PACKAGE_GUI_RUNTIME_IMPORT_OK", Gtk.get_major_version(), __version__)
PY

if find /usr/share/vocotype -type d -name __pycache__ -print -quit | grep -q .; then
  echo 'runtime import wrote __pycache__ into the immutable package tree' >&2
  exit 1
fi

echo PACKAGE_INSTALL_SMOKE_OK
