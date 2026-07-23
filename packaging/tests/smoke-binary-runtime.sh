#!/usr/bin/env bash
set -euo pipefail
root=${1:-/}
project=${2:-$root/usr/share/vocotype}
marker="$project/.system-package"

runtime_executable() {
  local name=$1 candidate
  for candidate in \
    "$root/usr/libexec/$name" \
    "$root/usr/lib/vocotype/$name" \
    "$root/usr/lib64/vocotype/$name"; do
    [[ -x "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

test -f "$marker"
grep -Fxq 'runtime=native' "$marker"
for executable in \
  "$root/usr/bin/vocotype-settings" \
  "$(runtime_executable vocotype-audio-recorder)" \
  "$(runtime_executable vocotype-model-manager)"; do
  test -x "$executable"
  file "$executable" | grep -q ELF
  ldd -r "$executable" >/dev/null
done
if find "$root/usr/share/vocotype" -type f \( -name '*.py' -o -name '*.whl' \) \
    -print -quit | grep -q .; then
  echo "native runtime contains Python files" >&2
  exit 1
fi
"$(runtime_executable vocotype-audio-recorder)" --help >/dev/null
"$(runtime_executable vocotype-model-manager)" --help >/dev/null
echo PACKAGE_NATIVE_RUNTIME_OK
