#!/usr/bin/env bash
set -euo pipefail
wheelhouse=${1:-/usr/share/vocotype/wheelhouse}
project_root=${2:-/usr/share/vocotype}
marker="$project_root/.system-package"
command -v uv >/dev/null 2>&1 || { echo "uv is required for binary runtime smoke" >&2; exit 127; }
test -d "$wheelhouse" || { echo "wheelhouse missing: $wheelhouse" >&2; exit 1; }
test -f "$marker" || { echo "native package marker missing: $marker" >&2; exit 1; }
requirements="$project_root/runtime-requirements.txt"
test -f "$requirements" || { echo "runtime requirements missing: $requirements" >&2; exit 1; }
flavor=$(sed -n 's/^flavor=//p' "$marker" | head -n 1)
case "$flavor" in universal|ibus|fcitx5) ;; *) echo "invalid flavor: $flavor" >&2; exit 1 ;; esac
includes_ibus=false
[[ "$flavor" == universal || "$flavor" == ibus ]] && includes_ibus=true

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
uv venv --python 3.12 "$work/venv"
UV_NO_BUILD=1 PIP_ONLY_BINARY=:all: \
uv pip install --python "$work/venv/bin/python" \
  --no-index --find-links "$wheelhouse" --only-binary :all: \
  -r "$requirements"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$project_root" \
  "$work/venv/bin/python" "$project_root/installers/check-python-runtime.py"

if [[ "$includes_ibus" == true ]]; then
  "$work/venv/bin/python" - <<'PY'
import importlib.util
import gi

gi.require_version("IBus", "1.0")
from gi.repository import IBus  # noqa: F401

for forbidden in ("pyrime", "wcwidth"):
    assert importlib.util.find_spec(forbidden) is None, forbidden
print("PACKAGE_IBUS_PRIVATE_RUNTIME_OK")
PY

  command -v rime_deployer >/dev/null 2>&1 || {
    echo 'rime_deployer missing from IBus package runtime dependencies' >&2
    exit 1
  }
  shared_data=""
  for candidate in /usr/share/rime-data /usr/local/share/rime-data; do
    if [[ -f "$candidate/default.yaml" ]]; then
      shared_data="$candidate"
      break
    fi
  done
  test -n "$shared_data" || { echo 'Rime shared data missing' >&2; exit 1; }
  mkdir -p "$work/rime-user" "$work/rime-log"
  cat > "$work/rime-user/default.custom.yaml" <<'YAML'
patch:
  schema_list:
    - schema: luna_pinyin
YAML
  cat > "$work/rime-user/user.yaml" <<'YAML'
var:
  previously_selected_schema: luna_pinyin
YAML
  rime_deployer --build \
    "$work/rime-user" "$shared_data" "$work/rime-user/build" >/dev/null
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$project_root" \
    python3 "$project_root/ibus/rime_runtime.py" \
      --shared-data-dir "$shared_data" \
      --user-data-dir "$work/rime-user" \
      --log-dir "$work/rime-log" \
      --schema luna_pinyin --key n | grep -F 'RIME_RUNTIME_OK'
  echo PACKAGE_RIME_KEYBOARD_OK
else
  "$work/venv/bin/python" - <<'PY'
import importlib.util
for excluded in ("gi", "pyrime", "wcwidth"):
    assert importlib.util.find_spec(excluded) is None, excluded
print("PACKAGE_FCITX_PRIVATE_RUNTIME_MINIMAL_OK")
PY
fi

"$work/venv/bin/python" - <<'PY'
import importlib.util
for forbidden in ("torch", "transformers", "socksio", "pyrime", "wcwidth"):
    assert importlib.util.find_spec(forbidden) is None, forbidden
PY
echo "PACKAGE_BINARY_RUNTIME_OK flavor=$flavor"
