#!/usr/bin/env bash
set -euo pipefail
wheelhouse=${1:-/usr/share/vocotype/wheelhouse}
project_root=${2:-/usr/share/vocotype}
command -v uv >/dev/null 2>&1 || { echo "uv is required for binary runtime smoke" >&2; exit 127; }
test -d "$wheelhouse" || { echo "wheelhouse missing: $wheelhouse" >&2; exit 1; }
test -f "$project_root/requirements.txt" || { echo "requirements missing: $project_root" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
uv venv --python 3.12 "$work/venv"
UV_NO_BUILD=1 PIP_ONLY_BINARY=:all: \
uv pip install --python "$work/venv/bin/python" \
  --no-index --find-links "$wheelhouse" --only-binary :all: \
  -r "$project_root/requirements.txt"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$project_root" \
  "$work/venv/bin/python" "$project_root/installers/check-python-runtime.py"
"$work/venv/bin/python" - <<'PY'
import importlib.util

for optional in ("torch", "transformers", "socksio", "pyrime"):
    assert importlib.util.find_spec(optional) is None, optional
PY
echo PACKAGE_BINARY_RUNTIME_OK
