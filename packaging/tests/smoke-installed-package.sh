#!/usr/bin/env bash
set -euo pipefail

expected_version="${1:?usage: $0 EXPECTED_VERSION [EXPECTED_FLAVOR]}"
expected_flavor="${2:-}"
marker=/usr/share/vocotype/.system-package

check_path() {
  local path="$1"
  [[ -e "$path" ]] || { echo "missing packaged path: $path" >&2; exit 1; }
  echo "PACKAGE_PATH_OK $path"
}

reject_path() {
  local path="$1"
  [[ ! -e "$path" ]] || { echo "excluded package path present: $path" >&2; exit 1; }
  echo "PACKAGE_PATH_ABSENT_OK $path"
}

for path in \
  /usr/bin/vocotype-settings \
  "$marker" \
  /usr/share/vocotype/installers/runtime-common.sh \
  /usr/share/vocotype/installers/uninstall-integration.sh \
  /usr/share/vocotype/installers/validate-installed-integration.py; do
  check_path "$path"
done

grep -Fxq 'managed-by=native-package' "$marker"
flavor=$(sed -n 's/^flavor=//p' "$marker")
package_name=$(sed -n 's/^package=//p' "$marker")
case "$flavor" in universal|ibus|fcitx5) ;; *) echo "invalid package flavor: $flavor" >&2; exit 1 ;; esac
[[ -z "$expected_flavor" || "$flavor" == "$expected_flavor" ]] || {
  echo "package flavor mismatch: expected=$expected_flavor actual=$flavor" >&2
  exit 1
}

includes_ibus=false
includes_fcitx=false
case "$flavor" in
  universal) includes_ibus=true; includes_fcitx=true ;;
  ibus) includes_ibus=true ;;
  fcitx5) includes_fcitx=true ;;
esac

ibus_exec=""
if [[ "$includes_ibus" == true ]]; then
  check_path /usr/share/vocotype/ibus/scripts/install-gui.sh
  check_path /usr/share/vocotype/ibus/scripts/uninstall-gui.sh
  check_path /usr/share/ibus/component/vocotype.xml
  ibus_exec=$(sed -n 's:.*<exec>\(.*\) --ibus</exec>.*:\1:p' /usr/share/ibus/component/vocotype.xml)
  [[ -n "$ibus_exec" ]] || { echo 'unable to parse IBus launcher' >&2; exit 1; }
  check_path "$ibus_exec"
  grep -Fq 'PYTHONDONTWRITEBYTECODE=1' "$ibus_exec"
else
  reject_path /usr/share/vocotype/ibus
  reject_path /usr/share/ibus/component/vocotype.xml
fi

module=""
if [[ "$includes_fcitx" == true ]]; then
  check_path /usr/share/vocotype/fcitx5/scripts/install-gui.sh
  check_path /usr/share/vocotype/fcitx5/scripts/uninstall-gui.sh
  check_path /usr/bin/vocotype-fcitx5-backend
  check_path /usr/bin/vocotype-fcitx5-recorder
  check_path /usr/share/fcitx5/addon/vocotype.conf
  check_path /usr/lib/systemd/user/vocotype-fcitx5-backend.service
  module=$(find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' -type f -print -quit 2>/dev/null)
  [[ -n "$module" ]] || { echo 'Fcitx module not found' >&2; exit 1; }
  echo "PACKAGE_MODULE_OK $module"
  if ldd "$module" | grep -q 'not found'; then
    ldd "$module" >&2
    exit 1
  fi
  relocation_log=$(mktemp)
  if ! ldd -r "$module" >"$relocation_log" 2>&1; then
    cat "$relocation_log" >&2
    rm -f "$relocation_log"
    echo 'Fcitx module has unresolved runtime relocations' >&2
    exit 1
  fi
  if grep -qi 'undefined symbol' "$relocation_log"; then
    cat "$relocation_log" >&2
    rm -f "$relocation_log"
    echo 'Fcitx module has undefined symbols' >&2
    exit 1
  fi
  rm -f "$relocation_log"
else
  reject_path /usr/share/vocotype/fcitx5
  reject_path /usr/bin/vocotype-fcitx5-backend
  reject_path /usr/bin/vocotype-fcitx5-recorder
  reject_path /usr/share/fcitx5/addon/vocotype.conf
  reject_path /usr/lib/systemd/user/vocotype-fcitx5-backend.service
  if find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' -type f -print -quit 2>/dev/null | grep -q .; then
    echo 'excluded Fcitx module is present' >&2
    exit 1
  fi
fi

streaming_launcher=""
for candidate in \
  /usr/libexec/vocotype-streaming-worker \
  /usr/lib/vocotype/vocotype-streaming-worker \
  /usr/lib64/vocotype/vocotype-streaming-worker \
  /usr/lib/*/vocotype/vocotype-streaming-worker; do
  if [[ -x "$candidate" ]]; then
    streaming_launcher="$candidate"
    break
  fi
done
[[ -n "$streaming_launcher" ]] || {
  echo 'native streaming worker launcher missing' >&2
  exit 1
}

streaming_worker_elf=""
for candidate in \
  /usr/lib/vocotype/vocotype-streaming-worker \
  /usr/lib64/vocotype/vocotype-streaming-worker \
  /usr/lib/*/vocotype/vocotype-streaming-worker \
  "$streaming_launcher"; do
  [[ -x "$candidate" ]] || continue
  if readelf -h "$candidate" >/dev/null 2>&1; then
    streaming_worker_elf="$candidate"
    break
  fi
done
[[ -n "$streaming_worker_elf" ]] || {
  echo 'native streaming worker ELF missing' >&2
  exit 1
}

streaming_ldd_log=$(mktemp)
if ! ldd -r "$streaming_worker_elf" >"$streaming_ldd_log" 2>&1; then
  cat "$streaming_ldd_log" >&2
  rm -f "$streaming_ldd_log"
  echo 'native streaming worker failed runtime relocation checks' >&2
  exit 1
fi
if grep -Eqi 'not found|undefined symbol|version `[^`]+. not found' "$streaming_ldd_log"; then
  cat "$streaming_ldd_log" >&2
  rm -f "$streaming_ldd_log"
  echo 'native streaming worker has unresolved runtime dependencies' >&2
  exit 1
fi
rm -f "$streaming_ldd_log"
"$streaming_launcher" --help >/dev/null
check_path /usr/share/licenses/vocotype-linux/native-streaming/onnxruntime/LICENSE
check_path /usr/share/licenses/vocotype-linux/native-streaming/funasr/LICENSE
echo "PACKAGE_STREAMING_RUNTIME_OK launcher=$streaming_launcher elf=$streaming_worker_elf"

wheelhouse=/usr/share/vocotype/wheelhouse
check_path "$wheelhouse"
wheel_count=$(find "$wheelhouse" -maxdepth 1 -type f -name '*.whl' | wc -l)
[[ "$wheel_count" -ge 12 ]] || { echo "incomplete wheelhouse: $wheel_count" >&2; exit 1; }
for normalized in funasr_onnx jieba modelscope numpy onnxruntime PyGObject PyYAML scipy sentencepiece sounddevice soundfile WeTextProcessing; do
  find "$wheelhouse" -maxdepth 1 -type f -iname "${normalized//-/_}-*.whl" -print -quit | grep -q . || {
    echo "required wheel missing: $normalized" >&2; exit 1;
  }
done
for excluded in torch transformers socksio pyrime; do
  if find "$wheelhouse" -maxdepth 1 -type f -iname "${excluded}-*.whl" -print -quit | grep -q .; then
    echo "non-core wheel leaked into package: $excluded" >&2; exit 1
  fi
done
if find "$wheelhouse" -maxdepth 1 -type f ! -name '*.whl' -print -quit | grep -q .; then
  echo 'non-wheel file present in wheelhouse' >&2; exit 1
fi
echo "PACKAGE_WHEELHOUSE_OK $wheel_count"

grep -Fq 'PYTHONDONTWRITEBYTECODE=1' /usr/bin/vocotype-settings
echo "PACKAGE_METADATA_OK flavor=$flavor package=$package_name"
[[ "$includes_ibus" == true ]] && "$(dirname "$0")/smoke-ibus-registry.sh"
[[ "$includes_fcitx" == true ]] && "$(dirname "$0")/smoke-fcitx-addon.sh"

find /usr/share/vocotype -type d -name __pycache__ -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/share/vocotype python3 - \
  "$expected_version" "$flavor" "$includes_ibus" "$includes_fcitx" "$ibus_exec" <<'PY'
import sys
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from settings_center.config_service import load_runtime_config
from settings_center.setup_manager import installation_paths, native_package_flavor, native_package_name
from vocotype_version import __version__

expected, flavor, includes_ibus, includes_fcitx, ibus_exec = sys.argv[1:]
assert __version__ == expected, (__version__, expected)
assert native_package_flavor() == flavor
assert native_package_name() in {"vocotype-linux", "vocotype-linux-ibus", "vocotype-linux-fcitx5"}
paths = installation_paths()
if includes_ibus == "true":
    assert any(str(path) == ibus_exec for path in paths.ibus_launchers)
else:
    assert not any(path.is_file() for path in paths.ibus_launchers)
if includes_fcitx == "true":
    assert any(path.is_file() for path in paths.fcitx_modules)
else:
    assert not any(path.is_file() for path in paths.fcitx_modules)
assert isinstance(load_runtime_config(), dict)
print("PACKAGE_GUI_RUNTIME_IMPORT_OK", Gtk.get_major_version(), __version__, flavor)
PY

if find /usr/share/vocotype -type d -name __pycache__ -print -quit | grep -q .; then
  echo 'runtime import wrote __pycache__ into immutable tree' >&2
  exit 1
fi

lifecycle_home=$(mktemp -d)
trap 'rm -rf "$lifecycle_home"' EXIT
frameworks=()
[[ "$includes_ibus" == true ]] && frameworks+=(ibus)
[[ "$includes_fcitx" == true ]] && frameworks+=(fcitx5)
for framework in "${frameworks[@]}"; do
  log="$lifecycle_home/$framework-uninstall.log"
  HOME="$lifecycle_home" XDG_CONFIG_HOME="$lifecycle_home/.config" \
    bash "/usr/share/vocotype/$framework/scripts/uninstall-gui.sh" \
    --purge-runtime >"$log" 2>&1
  grep -Fq "NATIVE_PACKAGE_COMMAND:" "$log"
  grep -Fq "$package_name" "$log"
done
[[ "$includes_ibus" != true ]] || check_path /usr/share/ibus/component/vocotype.xml
[[ "$includes_fcitx" != true ]] || check_path "$module"
echo PACKAGE_UNINSTALL_OWNERSHIP_OK
echo PACKAGE_INSTALL_SMOKE_OK
