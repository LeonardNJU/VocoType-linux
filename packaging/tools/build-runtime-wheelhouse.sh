#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/wheelhouse"}
PYTHON=${VOCOTYPE_WHEELHOUSE_PYTHON:-python3}
PYGOBJECT_SPEC=${VOCOTYPE_PYGOBJECT_SPEC:-PyGObject==3.50.2}
INDEX_URL=${VOCOTYPE_PYPI_INDEX_URL:-https://pypi.org/simple}
UV=${VOCOTYPE_UV_BIN:-uv}
command -v "$UV" >/dev/null 2>&1 || {
  echo "uv is required to export the locked runtime dependency graph" >&2
  exit 127
}

if [[ ! "$PYGOBJECT_SPEC" =~ ^[Pp]y[Gg][Oo]bject==([0-9]+([.][0-9]+)*)$ ]]; then
  echo "VOCOTYPE_PYGOBJECT_SPEC must pin one exact version, found: $PYGOBJECT_SPEC" >&2
  exit 2
fi
PYGOBJECT_VERSION=${BASH_REMATCH[1]}

python_abi=$("$PYTHON" - <<'PYTHON_CHECK'
import platform
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit(
        f"wheelhouse builder requires Python 3.12, found {sys.version.split()[0]}"
    )
if platform.machine() not in {"x86_64", "AMD64"}:
    raise SystemExit(
        f"wheelhouse builder requires x86_64, found {platform.machine()}"
    )
print("cp312-x86_64")
PYTHON_CHECK
)
echo "Building locked runtime wheelhouse for $python_abi"

rm -rf "$OUT"
mkdir -p "$OUT"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Export the exact transitive closure required by the normal VoCoType runtime.
# PyGObject is the only distro-specific exception because it links against the
# target distribution's GI/GLib ABI and is built in each package job.
"$UV" export --locked --no-dev --no-emit-project --no-hashes \
  --format requirements-txt > "$work/locked-constraints.txt"
sed -i '/^[Pp]y[Gg][Oo]bject==/d' "$work/locked-constraints.txt"

grep -viE '^[[:space:]]*PyGObject([<>=!~]|$)' \
  "$ROOT/requirements.txt" > "$work/runtime-requirements.txt"
printf '%s\n' "$PYGOBJECT_SPEC" >> "$work/runtime-requirements.txt"

# Source distributions may be compiled here in controlled release CI. The
# package contains only the resulting wheels; user setup later runs with
# --no-index --only-binary and cannot invoke a compiler.
"$PYTHON" -m pip wheel \
  --disable-pip-version-check \
  --index-url "$INDEX_URL" \
  --timeout 60 \
  --retries 8 \
  --constraint "$work/locked-constraints.txt" \
  --wheel-dir "$OUT" \
  -r "$work/runtime-requirements.txt"
python3 "$ROOT/packaging/tools/audit-wheelhouse.py" "$OUT" \
  --expected-pygobject-version "$PYGOBJECT_VERSION"
