#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/wheelhouse"}
FLAVOR=${2:-${VOCOTYPE_PACKAGE_FLAVOR:-universal}}
FLAVOR=$(python3 "$ROOT/packaging/tools/package-flavor.py" "$FLAVOR" --field flavor)
INCLUDES_IBUS=$(python3 "$ROOT/packaging/tools/package-flavor.py" "$FLAVOR" --field includes_ibus)
PYTHON=${VOCOTYPE_WHEELHOUSE_PYTHON:-python3}
PYGOBJECT_SPEC=${VOCOTYPE_PYGOBJECT_SPEC:-PyGObject==3.50.2}
BASE_WHEELHOUSE=${VOCOTYPE_BASE_WHEELHOUSE_DIR:-}
INDEX_URL=${VOCOTYPE_PYPI_INDEX_URL:-https://pypi.org/simple}
UV=${VOCOTYPE_UV_BIN:-uv}
command -v "$UV" >/dev/null 2>&1 || {
  echo "uv is required to export the locked runtime dependency graph" >&2
  exit 127
}

PYGOBJECT_VERSION=""
if [[ "$INCLUDES_IBUS" == true ]]; then
  if [[ ! "$PYGOBJECT_SPEC" =~ ^[Pp]y[Gg][Oo]bject==([0-9]+([.][0-9]+)*)$ ]]; then
    echo "VOCOTYPE_PYGOBJECT_SPEC must pin one exact version, found: $PYGOBJECT_SPEC" >&2
    exit 2
  fi
  PYGOBJECT_VERSION=${BASH_REMATCH[1]}
fi

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
echo "Building locked runtime wheelhouse for $python_abi flavor=$FLAVOR"

rm -rf "$OUT"
mkdir -p "$OUT"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [[ -n "$BASE_WHEELHOUSE" ]]; then
  if [[ "$INCLUDES_IBUS" != true ]]; then
    echo "VOCOTYPE_BASE_WHEELHOUSE_DIR is only valid when extending an IBus-capable flavor" >&2
    exit 2
  fi
  python3 "$ROOT/packaging/tools/audit-wheelhouse.py" \
    "$BASE_WHEELHOUSE" --flavor fcitx5
  cp -a "$BASE_WHEELHOUSE"/*.whl "$OUT/"
fi

# Export the exact transitive closure required by the normal VoCoType runtime.
# PyGObject is the only flavor-specific native extension. It is built in the
# target distribution job; end users consume only the resulting wheel.
"$UV" export --locked --no-dev --no-emit-project --no-hashes \
  --format requirements-txt > "$work/locked-constraints.txt"
sed -i \
  -e '/^[Pp]y[Gg][Oo]bject==/d' \
  "$work/locked-constraints.txt"

grep -viE '^[[:space:]]*PyGObject([<>=!~]|$)' \
  "$ROOT/requirements.txt" > "$work/runtime-requirements.txt"
if [[ "$INCLUDES_IBUS" == true ]]; then
  printf '%s\n' "$PYGOBJECT_SPEC" >> "$work/runtime-requirements.txt"
fi

requirements_file="$work/runtime-requirements.txt"
find_links=()
if [[ -n "$BASE_WHEELHOUSE" ]]; then
  printf '%s\n' "$PYGOBJECT_SPEC" \
    > "$work/ibus-extension-requirements.txt"
  requirements_file="$work/ibus-extension-requirements.txt"
  find_links=(--find-links "$OUT")
fi

# Source distributions may be compiled here in controlled release CI. The
# package contains only the resulting wheels; user setup later runs with
# --no-index --only-binary and cannot invoke a compiler.
"$PYTHON" -m pip wheel \
  --disable-pip-version-check \
  --index-url "$INDEX_URL" \
  --timeout 60 \
  --retries 8 \
  --constraint "$work/locked-constraints.txt" \
  "${find_links[@]}" \
  --wheel-dir "$OUT" \
  -r "$requirements_file"

audit_args=(--flavor "$FLAVOR")
if [[ "$INCLUDES_IBUS" == true ]]; then
  audit_args+=(--expected-pygobject-version "$PYGOBJECT_VERSION")
fi
python3 "$ROOT/packaging/tools/audit-wheelhouse.py" "$OUT" "${audit_args[@]}"
