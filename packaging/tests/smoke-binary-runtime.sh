#!/usr/bin/env bash
set -euo pipefail
root=${1:-/}
project=${2:-$root/usr/share/vocotype}
marker="$project/.system-package"
test -f "$marker"
grep -Fxq 'runtime=native' "$marker"
for executable in \
  "$root/usr/bin/vocotype-settings" \
  "$root/usr/libexec/vocotype-audio-recorder" \
  "$root/usr/libexec/vocotype-model-manager"; do
  test -x "$executable"
  file "$executable" | grep -q ELF
  ldd -r "$executable" >/dev/null
 done
if find "$root/usr/share/vocotype" -type f \( -name '*.py' -o -name '*.whl' \) -print -quit | grep -q .; then
  echo "native runtime contains Python files" >&2; exit 1
fi
"$root/usr/libexec/vocotype-audio-recorder" --help >/dev/null
"$root/usr/libexec/vocotype-model-manager" --help >/dev/null
echo PACKAGE_NATIVE_RUNTIME_OK
