#!/usr/bin/env bash
set -euo pipefail
root=${1:?usage: $0 BUNDLE_DIR}
root=$(realpath "$root")
lib_dir="$root/lib"
[[ -d "$lib_dir" ]] || { echo "Missing bundle lib directory" >&2; exit 1; }
allowed_host=' libc.so.6 libdl.so.2 libgcc_s.so.1 libm.so.6 libpthread.so.0 librt.so.1 libstdc++.so.6 ld-linux-aarch64.so.1 ld-linux-x86-64.so.2 '
errors=0
mapfile -t paths < <(find "$root/bin" "$lib_dir" -maxdepth 1 -type f -print | sort)
for path in "${paths[@]}"; do
  section=$(readelf -d "$path" 2>/dev/null || true)
  [[ -n "$section" ]] || continue
  bundled=" $(find "$lib_dir" -maxdepth 1 -printf '%f ' | tr '\n' ' ')"
  allowed="$allowed_host"
  while IFS= read -r dependency; do
    [[ "$bundled" == *" $dependency "* || "$allowed" == *" $dependency "* ]] || {
      echo "$(basename "$path"): unbundled dependency $dependency" >&2
      errors=1
    }
  done < <(sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' <<<"$section")
  while IFS= read -r runpath; do
    IFS=: read -ra entries <<<"$runpath"
    for entry in "${entries[@]}"; do
      [[ "$entry" != /* ]] || { echo "$(basename "$path"): absolute RUNPATH $entry" >&2; errors=1; }
    done
  done < <(sed -n 's/.*Library \(rpath\|runpath\): \[\([^]]*\)\].*/\2/p' <<<"$section")
done
(( errors == 0 )) || exit 1
echo "Native bundle audit passed: $root"
